"""Greybox generator — the VeriGrey adaptation (Rung 2).

Faithful port of VeriGrey's feedback + scheduling (Zhang et al., Alg. 1-2, §4.1),
per docs/design/greybox-verigrey-adaptation.md:
  - feedback = the run's SEQUENCE OF TOOL INVOCATIONS over schematized labels
    (granularity L0/L1/L2 — the declared adaptation parameter);
  - a run exhibiting a new tool / new transition / new sequence adds its candidate
    to the seed corpus (their Alg. 1 line 15);
  - energy = (+1 new tool, +1 new transition, +1 new sequence) — verbatim §4.1.2 —
    is the number of offspring the seed gets when chosen;
  - mutation = an LLM rewrites the seed's (task, env), conditioned on the tool
    sequence the seed produced (their Fig. 3 minus the injection-specific bridge);
  - realization/build/repair = the SAME shared pipeline as every other rung.

`fold` is pure code (no model). This rung never reads the agent's reasoning,
episodes, outcomes, or the tree — it is the no-reasoning/no-intent ablation.

Usage: driven by skillrace.loop; standalone smoke:
  python -m skillrace.greybox --schematize runs/ftt-case2 --level L1
"""
from __future__ import annotations
import argparse
import contextlib
import json
import pathlib
import re
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from .closeai import chat, extract_json
from .generator import (
    DEFAULT_BUILD_RETRIES,
    DEFAULT_BUILD_TIMEOUT,
    GenerationFailure,
    realize,
    realize_and_build,
    skill_context,
)
from .input_identity import skill_input_tree_hash
from .io_utils import canonical_json_hash
from .parallel_campaign import (
    apply_state_transition,
    load_state_transition,
    publish_state_transition,
    read_state_transition,
)

MUTATE_SYS = (
    "You mutate ONE test case for a coding-agent skill. You get the skill context, "
    "the seed test's task and environment (natural language), and the schematized "
    "TOOL SEQUENCE the agent produced when run on it. Produce a NEW (task, "
    "environment) variant of this seed that is likely to drive the agent through "
    "DIFFERENT tool behavior (different tools, different order, different parts of "
    "the project), while staying faithful to the skill's stated purpose. The "
    "environment must remain a GENUINE, UNSOLVED starting point.\n"
    'Return ONLY JSON: {"task": "...", "env": "..."}'
)

_SEED_PROVENANCE_FIELDS = {
    "source",
    "summary",
    "task_nl",
    "env_nl",
    "build_attempts",
    "parent_candidate",
    "granularity",
    "independent_test",
}


# ---------------------------------------------------------------- schematize

def _bash_head(cmd):
    """First meaningful command token: skips `cd x &&` prefixes and VAR= assigns."""
    s = (cmd or "").strip()
    m = re.match(r"(?:cd\s+\S+\s*&&\s*)+", s)
    if m:
        s = s[m.end():].strip()
    parts = s.split()
    for p in parts:
        if "=" not in p or p.startswith(("./", "/")):
            return p
    return parts[0] if parts else ""


def _path_bucket(path):
    """Normalize a path: first dir component under the repo + globbed name."""
    p = (path or "").replace("/workspace/", "").lstrip("./")
    parts = p.split("/")
    ext = ("." + parts[-1].rsplit(".", 1)[1]) if "." in parts[-1] else ""
    return (parts[0] + "/*" + ext) if len(parts) > 1 else ("*" + ext)


def label(name, args, level):
    a = args or {}
    if level == "L0":
        return name
    if name == "bash":
        head = _bash_head(a.get("command", ""))
        if level == "L1":
            return f"bash:{head}"
        second = (a.get("command") or "").split()
        tgt = next((t for t in second[1:] if not t.startswith("-")), "")
        return f"bash:{head}:{_path_bucket(tgt)}" if tgt else f"bash:{head}"
    path = a.get("path", "")
    ext = ("." + path.rsplit(".", 1)[1]) if "." in path.split("/")[-1] else ""
    if level == "L1":
        return f"{name}:{ext or '?'}"
    return f"{name}:{_path_bucket(path)}"


def schematize(run_dir, level="L1"):
    """The run's tool sequence as a list of schematized labels (from raw session)."""
    seq = []
    sess = pathlib.Path(run_dir) / "raw" / "session.jsonl"
    if not sess.exists():
        return seq
    for line in open(sess):
        try:
            m = json.loads(line).get("message", {})
        except Exception:
            continue
        if m.get("role") != "assistant":
            continue
        for b in m.get("content", []):
            if b.get("type") == "toolCall":
                seq.append(label(b.get("name", "?"), b.get("arguments"), level))
    return seq


# ---------------------------------------------------------------- generator

class GreyboxGenerator:
    """Drop-in Generator: fold() = pure-code novelty feedback; propose() = one LLM
    mutation of a novelty-chosen seed, realized via the shared build pipeline."""

    def __init__(self, skill, skill_dir, base_image, model="glm-4.5-flash",
                 level="L1", temperature=0.9,
                 build_retries=DEFAULT_BUILD_RETRIES,
                 build_timeout=DEFAULT_BUILD_TIMEOUT,
                 base_image_identity=None):
        self.skill = skill
        self.skill_dir = pathlib.Path(skill_dir)
        self.skill_input_hash = skill_input_tree_hash(self.skill_dir)
        self.base_image = base_image
        self.base_image_identity = base_image_identity or base_image
        self.build_base_image = self.base_image
        self.model = model
        self.level = level
        self.temperature = temperature
        self.build_retries = build_retries
        self.build_timeout = build_timeout
        self.ctx = skill_context(pathlib.Path(skill_dir))
        self.d_tool, self.d_trans, self.d_seq = set(), set(), set()
        self.corpus = []               # ALL kept seeds (VeriGrey's S — never exhausts)
        self.queue = deque()          # seeds: {"cand":.., "seq":[..], "energy":N}
        self._pending = None           # seed currently spending energy
        self.cost_provider_credits = 0.0
        self.stats = {
            "folded": 0,
            "initial_retained": 0,
            "novel_mutants": 0,
            "novel": 0,
            "mutations": 0,
            "skipped_builds": 0,
        }
        self.failure_state = None
        self.folded_attempt_ids = []
        self._fold_results = {}

    @classmethod
    def for_test(cls):
        """Construct a deterministic offline instance without a real skill tree."""
        return cls(
            "test-skill",
            "/__skillrace_missing_skill__",
            "skillrace/test-skill:base",
        )

    # -- feedback (VeriGrey Alg. 1 l.14-15 + §4.1.2) — code only, no model --
    def _observe(self, run_dir):
        seq = schematize(run_dir, self.level)
        trans = list(zip(seq, seq[1:]))
        energy = int(any(tool not in self.d_tool for tool in seq))
        energy += int(any(edge not in self.d_trans for edge in trans))
        energy += int(tuple(seq) not in self.d_seq)
        self.d_tool.update(seq)
        self.d_trans.update(trans)
        self.d_seq.add(tuple(seq))
        self.stats["folded"] += 1
        return seq, energy

    def _retain(self, candidate, seq, energy):
        provenance = candidate.get("provenance") or {}
        seed_candidate = {
            "candidate_id": candidate.get("candidate_id"),
            "skill": candidate.get("skill"),
            "provenance": {
                key: provenance[key]
                for key in _SEED_PROVENANCE_FIELDS
                if key in provenance
            },
        }
        seed = {
            "cand": seed_candidate,
            "seq": seq,
            "energy": energy,
            "base_energy": energy,
        }
        self.corpus.append(seed)
        if energy > 0:
            self.queue.append(seed)
        return seed

    def fold_initial(self, candidate, run_dir):
        """Observe and retain every counted bootstrap execution, including empties."""
        seq, energy = self._observe(run_dir)
        self.stats["initial_retained"] += 1
        return self._retain(candidate, seq, energy)

    def fold_mutant(self, candidate, run_dir):
        """Observe every mutant but retain it only when it expands coverage."""
        seq, energy = self._observe(run_dir)
        if energy == 0:
            return None
        self.stats["novel_mutants"] += 1
        self.stats["novel"] += 1  # compatibility with existing summaries
        return self._retain(candidate, seq, energy)

    def fold(self, candidate, run_dir, phase="explore", attempt_id=None):
        if attempt_id is not None and attempt_id in self._fold_results:
            return self._fold_results[attempt_id]
        if phase == "bootstrap":
            result = self.fold_initial(candidate, run_dir)
        elif phase == "explore":
            result = self.fold_mutant(candidate, run_dir)
        else:
            raise ValueError(f"unknown greybox fold phase: {phase}")
        if attempt_id is not None:
            self.folded_attempt_ids.append(attempt_id)
            self._fold_results[attempt_id] = result
        return result

    # -- scheduling: rotate the queue; a chosen seed yields `energy` offspring --
    def _choose_seed(self):
        if self._pending and self._pending["energy"] > 0:
            return self._pending
        self._pending = None
        if not self.queue:
            eligible = [
                seed for seed in self.corpus
                if seed.get("base_energy", 0) > 0
            ]
            if not eligible:
                return None
            for seed in eligible:
                seed["energy"] = seed["base_energy"]
            self.queue = deque(eligible)
        # highest-energy first (greater opportunity, §4.1.2), stable otherwise
        self.queue = deque(sorted(self.queue, key=lambda s: -s["energy"]))
        self._pending = self.queue.popleft()
        return self._pending

    def mutation_context(self, seed):
        """The complete and deliberately narrow information available to mutation."""
        prov = seed["cand"].get("provenance", {})
        return (
            f"{self.ctx}\n\nSEED TEST:\n"
            f"- task: {prov.get('task_nl', '')}\n"
            f"- environment: {prov.get('env_nl', '')}\n\n"
            f"TOOL SEQUENCE the agent produced on this seed "
            f"(granularity {self.level}):\n  "
            + " -> ".join(seed["seq"][:120])
            + "\n\nReturn ONLY the JSON."
        )

    def _mutate(self, seed):
        user = self.mutation_context(seed)
        resp = chat([{"role": "system", "content": MUTATE_SYS},
                     {"role": "user", "content": user}],
                    model=self.model, temperature=self.temperature, reasoning=False,
                    max_tokens=900, tag="greybox.mutate", skill=self.skill)
        self.cost_provider_credits += resp["cost_provider_credits"]
        obj = extract_json(resp["content"])
        return obj["task"].strip(), obj["env"].strip()

    def _mutate_reserved(self, seed):
        """Pure mutation call for an isolated reservation worker."""
        user = self.mutation_context(seed)
        resp = chat([{"role": "system", "content": MUTATE_SYS},
                     {"role": "user", "content": user}],
                    model=self.model, temperature=self.temperature, reasoning=False,
                    max_tokens=900, tag="greybox.mutate", skill=self.skill)
        obj = extract_json(resp["content"])
        return obj["task"].strip(), obj["env"].strip(), resp["cost_provider_credits"]

    def reserve_mutations(self, reservations, *, batch_path):
        """Spend scheduler energy serially and freeze independent worker inputs."""
        reservations = list(reservations)
        request = [
            {
                "candidate_id": getattr(reservation, "candidate_id", None),
                "provenance": dict(getattr(reservation, "provenance", {}) or {}),
            }
            for reservation in reservations
        ]
        request_hash = canonical_json_hash(request)
        transition = load_state_transition(
            batch_path,
            schema="greybox-reservation-transition/1",
            request_hash=request_hash,
        )
        if transition is not None:
            apply_state_transition(self.snapshot(), transition, restore=self.restore)
            return tuple(transition["payload"]["reservations"])

        pre = self.snapshot()
        frozen_scheduler_hash = canonical_json_hash(pre)
        planner = GreyboxGenerator(
            self.skill,
            self.skill_dir,
            self.base_image,
            model=self.model,
            level=self.level,
            temperature=self.temperature,
            build_retries=self.build_retries,
            build_timeout=self.build_timeout,
            base_image_identity=self.base_image_identity,
        )
        planner.restore(pre)
        records = []
        for reservation in reservations:
            candidate_id = getattr(reservation, "candidate_id", None)
            reserved_provenance = getattr(reservation, "provenance", None)
            if not isinstance(candidate_id, str) or reserved_provenance is None:
                raise ValueError("malformed greybox candidate reservation")
            seed = planner._choose_seed()
            if seed is None:
                raise GenerationFailure(
                    "greybox has no seed with positive earned energy",
                    reason="no-schedulable-energy",
                )
            seed["energy"] -= 1
            records.append(
                {
                    "schema": "greybox-mutation-reservation/1",
                    "candidate_id": candidate_id,
                    "provenance": dict(reserved_provenance),
                    "frozen_scheduler_hash": frozen_scheduler_hash,
                    "seed": json.loads(json.dumps(seed)),
                }
            )
        post = planner.snapshot()
        transition = publish_state_transition(
            batch_path,
            schema="greybox-reservation-transition/1",
            request_hash=request_hash,
            pre_state=pre,
            post_state=post,
            payload={"reservations": records},
        )
        apply_state_transition(self.snapshot(), transition, restore=self.restore)
        return tuple(records)

    def propose_reserved(self, reservation):
        """Realize one frozen reservation without mutating reducer-owned state."""
        if (
            not isinstance(reservation, dict)
            or reservation.get("schema") != "greybox-mutation-reservation/1"
            or not isinstance(reservation.get("seed"), dict)
            or not isinstance(reservation.get("provenance"), dict)
            or not isinstance(reservation.get("candidate_id"), str)
        ):
            raise ValueError("malformed greybox mutation reservation")
        seed = json.loads(json.dumps(reservation["seed"]))
        cid = reservation["candidate_id"]
        try:
            task_nl, env_nl, mutation_cost = self._mutate_reserved(seed)
        except Exception as error:
            raise GenerationFailure(
                f"greybox mutation failed: {error}",
                reason="mutation-failure",
                cost_provider_credits=float(
                    getattr(error, "cost_provider_credits", 0.0) or 0.0
                ),
            ) from error
        try:
            artifact, build_cost, last_error = realize_and_build(
                self.ctx,
                task_nl,
                env_nl,
                self.model,
                self.build_base_image,
                cid,
                build_retries=self.build_retries,
                build_timeout=self.build_timeout,
            )
        except Exception as error:
            raise GenerationFailure(
                f"greybox realization failed: {error}",
                reason="realization-failure",
                cost_provider_credits=round(
                    float(mutation_cost)
                    + float(getattr(error, "cost_provider_credits", 0.0) or 0.0),
                    12,
                ),
            ) from error
        if artifact is None:
            raise GenerationFailure(
                f"greybox build failed: {last_error}",
                reason="build-failure",
                cost_provider_credits=round(
                    float(mutation_cost) + float(build_cost), 12
                ),
            )
        provenance = {
            "source": "greybox",
            "requested_base_image": self.base_image,
            "base_image_identity": self.base_image_identity,
            "parent_candidate": seed["cand"].get("candidate_id"),
            "granularity": self.level,
            "task_nl": task_nl,
            "env_nl": env_nl,
            "build_attempts": artifact["build_attempts"],
            **reservation["provenance"],
            "frozen_scheduler_hash": reservation["frozen_scheduler_hash"],
        }
        candidate = {
            "candidate_id": cid,
            "skill": self.skill,
            "prompt": artifact["prompt"],
            "base_image": self.base_image,
            "base_image_identity": self.base_image_identity,
            "containerfile": artifact["containerfile"],
            "built_image": artifact["built_image"],
            "sanity": artifact["sanity"],
            "provenance": provenance,
        }
        return candidate, round(float(mutation_cost) + float(build_cost), 12)

    def complete_reserved_batch(
        self,
        batch_path,
        results,
        *,
        completion_path,
    ):
        batch = read_state_transition(
            batch_path, schema="greybox-reservation-transition/1"
        )
        reservations = batch["payload"]["reservations"]
        results = json.loads(json.dumps(list(results)))
        expected = [item["candidate_id"] for item in reservations]
        actual = [item.get("candidate_id") for item in results]
        if actual != expected or len(actual) != len(set(actual)):
            raise ValueError("greybox completion does not match reserved batch order")
        request_hash = canonical_json_hash(
            {"batch_transition_hash": batch["transition_hash"], "results": results}
        )
        transition = load_state_transition(
            completion_path,
            schema="greybox-reservation-completion/1",
            request_hash=request_hash,
        )
        if transition is None:
            pre = self.snapshot()
            if canonical_json_hash(pre) != batch["post_state_hash"]:
                raise ValueError("greybox completion state is not the reserved state")
            post = json.loads(json.dumps(pre))
            failures = [
                str(item.get("error") or "generation failed")
                for item in results
                if not isinstance(item.get("candidate"), dict)
            ]
            successes = len(results) - len(failures)
            post["stats"]["mutations"] += len(results)
            post["stats"]["skipped_builds"] += len(failures)
            total_cost = sum(float(item.get("cost_provider_credits", 0.0)) for item in results)
            post["cost_provider_credits"] = round(float(post["cost_provider_credits"]) + total_cost, 12)
            post["gen_cost_provider_credits"] = round(post["cost_provider_credits"], 6)
            post["failure_state"] = (
                None
                if successes
                else {
                    "type": "GenerationFailure",
                    "reason": "reserved-batch-failure",
                    "message": failures[-1] if failures else "reserved batch failed",
                }
            )
            transition = publish_state_transition(
                completion_path,
                schema="greybox-reservation-completion/1",
                request_hash=request_hash,
                pre_state=pre,
                post_state=post,
                payload={"results": results},
            )
        apply_state_transition(self.snapshot(), transition, restore=self.restore)
        return transition

    def propose_epoch(
        self, reservations, *, batch_dir, resource_pool=None, **_
    ):
        """Reserve scheduler energy once, then realize an isolated mutation batch."""
        reservations = list(reservations)
        root = pathlib.Path(batch_dir)
        batch_path = root / "reservation.json"
        completion_path = root / "completion.json"
        if batch_path.exists():
            batch_transition = read_state_transition(
                batch_path, schema="greybox-reservation-transition/1"
            )
            records = batch_transition["payload"]["reservations"]
            expected = [reservation.candidate_id for reservation in reservations]
            if [record["candidate_id"] for record in records] != expected:
                raise ValueError("persisted greybox epoch reservation identity mismatch")
        else:
            records = self.reserve_mutations(
                reservations, batch_path=batch_path
            )
            batch_transition = read_state_transition(
                batch_path, schema="greybox-reservation-transition/1"
            )

        if completion_path.exists():
            completion = read_state_transition(
                completion_path, schema="greybox-reservation-completion/1"
            )
            expected_request = canonical_json_hash(
                {
                    "batch_transition_hash": read_state_transition(
                        batch_path, schema="greybox-reservation-transition/1"
                    )["transition_hash"],
                    "results": completion["payload"]["results"],
                }
            )
            if completion["request_hash"] != expected_request:
                raise ValueError("persisted greybox epoch completion request mismatch")
            if canonical_json_hash(self.snapshot()) != completion["post_state_hash"]:
                apply_state_transition(
                    self.snapshot(), batch_transition, restore=self.restore
                )
                apply_state_transition(
                    self.snapshot(), completion, restore=self.restore
                )
            results = completion["payload"]["results"]
        else:
            apply_state_transition(
                self.snapshot(), batch_transition, restore=self.restore
            )
            def realize_one(record):
                worker_slots = (
                    resource_pool.slots("api", "docker")
                    if resource_pool is not None
                    else contextlib.nullcontext()
                )
                try:
                    with worker_slots:
                        candidate, cost = self.propose_reserved(record)
                    return candidate, cost, None
                except Exception as error:
                    return None, float(
                        getattr(error, "cost_provider_credits", 0.0) or 0.0
                    ), {
                        "type": type(error).__name__,
                        "reason": getattr(error, "reason", "generation-error"),
                        "message": str(error)[:500],
                    }

            workers = max(1, len(records))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                realized = list(executor.map(realize_one, records))
            results = [
                {
                    "candidate_id": record["candidate_id"],
                    "candidate": candidate,
                    "cost_provider_credits": cost,
                    "error": error,
                }
                for record, (candidate, cost, error) in zip(records, realized)
            ]
            self.complete_reserved_batch(
                batch_path, results, completion_path=completion_path
            )
        return [
            {
                "candidate": result.get("candidate"),
                "source": "greybox",
                "error": result.get("error"),
            }
            for result in results
        ]

    def propose(self):
        """Perform exactly one mutation/realization/build proposal attempt."""
        seed = self._choose_seed()
        if seed is None:
            error = GenerationFailure(
                "greybox has no seed with positive earned energy",
                reason="no-schedulable-energy",
            )
            self._remember_failure(error)
            raise error
        seed["energy"] -= 1
        cid = "cand-" + uuid.uuid4().hex[:12]
        try:
            task_nl, env_nl = self._mutate(seed)
        except Exception as error:
            failure = GenerationFailure(
                f"greybox mutation failed: {error}", reason="mutation-failure"
            )
            self._remember_failure(failure)
            raise failure from error
        self.stats["mutations"] += 1
        try:
            artifact, cost, last_error = realize_and_build(
                self.ctx,
                task_nl,
                env_nl,
                self.model,
                self.build_base_image,
                cid,
                build_retries=self.build_retries,
                build_timeout=self.build_timeout,
            )
            self.cost_provider_credits += cost
        except Exception as error:
            self.stats["skipped_builds"] += 1
            failure = GenerationFailure(
                f"greybox realization failed: {error}",
                reason="realization-failure",
            )
            self._remember_failure(failure)
            raise failure from error
        if artifact is None:
            self.stats["skipped_builds"] += 1
            failure = GenerationFailure(
                f"greybox build failed: {last_error}", reason="build-failure"
            )
            self._remember_failure(failure)
            raise failure
        self.failure_state = None
        return {
            "candidate_id": cid,
            "skill": self.skill,
            "prompt": artifact["prompt"],
            "base_image": self.base_image,
            "base_image_identity": self.base_image_identity,
            "containerfile": artifact["containerfile"],
            "built_image": artifact["built_image"],
            "sanity": artifact["sanity"],
            "provenance": {
                "source": "greybox",
                "requested_base_image": self.base_image,
                "base_image_identity": self.base_image_identity,
                "parent_candidate": seed["cand"].get("candidate_id"),
                "granularity": self.level,
                "task_nl": task_nl,
                "env_nl": env_nl,
                "build_attempts": artifact["build_attempts"],
            },
        }

    def _remember_failure(self, error):
        self.failure_state = {
            "type": type(error).__name__,
            "reason": getattr(error, "reason", "generation-error"),
            "message": str(error),
        }

    def snapshot(self):
        """Serialize exact scheduling/novelty state without skill context or secrets."""
        entry_ids = {id(seed): f"g{index:06d}" for index, seed in enumerate(self.corpus)}
        corpus = [
            {"entry_id": entry_ids[id(seed)], "seed": json.loads(json.dumps(seed))}
            for seed in self.corpus
        ]
        try:
            queue_order = [entry_ids[id(seed)] for seed in self.queue]
            pending = entry_ids[id(self._pending)] if self._pending is not None else None
        except KeyError as error:
            raise ValueError("greybox queue/pending must reference retained corpus objects") from error
        fold_results = []
        for attempt_id in self.folded_attempt_ids:
            result = self._fold_results.get(attempt_id)
            fold_results.append(
                {
                    "attempt_id": attempt_id,
                    "entry_id": entry_ids.get(id(result)),
                    "value": None if id(result) in entry_ids else json.loads(json.dumps(result)),
                }
            )
        return {
            "schema": "greybox-generator/1",
            "source": "greybox",
            "skill": self.skill,
            "model": self.model,
            "base_image": self.base_image,
            "base_image_identity": self.base_image_identity,
            "skill_input_hash": self.skill_input_hash,
            "config": {
                "level": self.level,
                "temperature": self.temperature,
                "build_retries": self.build_retries,
                "build_timeout": self.build_timeout,
            },
            "novelty": {
                "tools": sorted(self.d_tool),
                "transitions": [list(edge) for edge in sorted(self.d_trans)],
                "sequences": [list(sequence) for sequence in sorted(self.d_seq)],
            },
            "corpus": corpus,
            "queue_order": queue_order,
            "pending_entry_id": pending,
            "stats": json.loads(json.dumps(self.stats)),
            "cost_provider_credits": self.cost_provider_credits,
            "gen_cost_provider_credits": round(self.cost_provider_credits, 6),
            "failure_state": json.loads(json.dumps(self.failure_state)),
            "folded_attempt_ids": list(self.folded_attempt_ids),
            "fold_results": fold_results,
        }

    def restore(self, snapshot):
        if not isinstance(snapshot, dict) or snapshot.get("schema") != "greybox-generator/1":
            raise ValueError("unsupported greybox generator snapshot")
        expected_config = {
            "level": self.level,
            "temperature": self.temperature,
            "build_retries": self.build_retries,
            "build_timeout": self.build_timeout,
        }
        if (
            snapshot.get("source") != "greybox"
            or snapshot.get("skill") != self.skill
            or snapshot.get("model") != self.model
            or snapshot.get("base_image") != self.base_image
            or snapshot.get("config") != expected_config
        ):
            raise ValueError("greybox generator identity/configuration mismatch")
        if snapshot.get("skill_input_hash") != skill_input_tree_hash(self.skill_dir):
            raise ValueError("greybox generator skill input hash mismatch")
        if snapshot.get("base_image_identity") != self.base_image_identity:
            raise ValueError("greybox generator base-image identity mismatch")
        entries = snapshot.get("corpus")
        if not isinstance(entries, list):
            raise ValueError("malformed greybox corpus snapshot")
        restored = {}
        corpus = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("entry_id"), str):
                raise ValueError("malformed greybox corpus entry")
            entry_id = entry["entry_id"]
            if entry_id in restored or not isinstance(entry.get("seed"), dict):
                raise ValueError("duplicate/malformed greybox corpus entry")
            seed = json.loads(json.dumps(entry["seed"]))
            restored[entry_id] = seed
            corpus.append(seed)
        try:
            queue = deque(restored[entry_id] for entry_id in snapshot["queue_order"])
            pending_id = snapshot.get("pending_entry_id")
            pending = restored[pending_id] if pending_id is not None else None
        except (KeyError, TypeError) as error:
            raise ValueError("greybox queue/pending references unknown corpus entry") from error
        novelty = snapshot.get("novelty")
        if not isinstance(novelty, dict):
            raise ValueError("malformed greybox novelty snapshot")
        self.d_tool = set(novelty.get("tools", []))
        self.d_trans = {tuple(edge) for edge in novelty.get("transitions", [])}
        self.d_seq = {tuple(sequence) for sequence in novelty.get("sequences", [])}
        self.corpus = corpus
        self.queue = queue
        self._pending = pending
        self.stats = json.loads(json.dumps(snapshot.get("stats", {})))
        self.cost_provider_credits = float(snapshot.get("cost_provider_credits", 0.0))
        self.failure_state = json.loads(json.dumps(snapshot.get("failure_state")))
        self.folded_attempt_ids = list(snapshot.get("folded_attempt_ids", []))
        self._fold_results = {}
        for folded in snapshot.get("fold_results", []):
            attempt_id = folded["attempt_id"]
            entry_id = folded.get("entry_id")
            self._fold_results[attempt_id] = (
                restored[entry_id]
                if entry_id is not None
                else json.loads(json.dumps(folded.get("value")))
            )

    def state(self):
        return {"skill": self.skill, "source": "greybox", "level": self.level,
                "novelty": {"tools": len(self.d_tool), "transitions": len(self.d_trans),
                            "sequences": len(self.d_seq)},
                "queue": len(self.queue), "stats": self.stats,
                "gen_cost_provider_credits": round(self.cost_provider_credits, 6)}


def main():
    ap = argparse.ArgumentParser(description="Greybox utilities (schematize smoke test)")
    ap.add_argument("--schematize", help="run dir: print its schematized tool sequence")
    ap.add_argument("--level", default="L1", choices=["L0", "L1", "L2"])
    args = ap.parse_args()
    if args.schematize:
        seq = schematize(args.schematize, args.level)
        print(f"{len(seq)} tool events ({args.level}):")
        print("  " + " -> ".join(seq))


if __name__ == "__main__":
    main()
