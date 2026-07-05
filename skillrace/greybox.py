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
import json
import pathlib
import re
import uuid
from collections import deque

from .closeai import chat, extract_json
from .generator import (skill_context, realize, repair_tail, containerfile_for,
                        build_image)

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

    def __init__(self, skill, skill_dir, base_image, model="qwen3.6-flash",
                 level="L1", temperature=0.9, build_retries=4, build_timeout=600):
        self.skill = skill
        self.base_image = base_image
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
        self.cost_usd = 0.0
        self.stats = {"folded": 0, "novel": 0, "mutations": 0, "skipped_builds": 0}

    # -- feedback (VeriGrey Alg. 1 l.14-15 + §4.1.2) — code only, no model --
    def fold(self, candidate, run_dir):
        seq = schematize(run_dir, self.level)
        if not seq:
            return
        trans = list(zip(seq, seq[1:]))
        energy = 0
        if any(t not in self.d_tool for t in seq):
            energy += 1
        if any(e not in self.d_trans for e in trans):
            energy += 1
        if tuple(seq) not in self.d_seq:
            energy += 1
        self.d_tool.update(seq)
        self.d_trans.update(trans)
        self.d_seq.add(tuple(seq))
        self.stats["folded"] += 1
        if energy > 0:                 # novel behavior -> keep as seed with energy
            self.stats["novel"] += 1
            seed = {"cand": candidate, "seq": seq, "energy": energy}
            self.corpus.append(seed)
            self.queue.append(seed)

    # -- scheduling: rotate the queue; a chosen seed yields `energy` offspring --
    def _choose_seed(self):
        if self._pending and self._pending["energy"] > 0:
            return self._pending
        if not self.queue:
            # VeriGrey's S never exhausts: ChooseSeed keeps drawing from the corpus
            # (Alg. 1 repeats until timeout). Recycle every kept seed with energy 1.
            if not self.corpus:
                return None
            for s in self.corpus:
                s["energy"] = 1
            self.queue = deque(self.corpus)
        # highest-energy first (greater opportunity, §4.1.2), stable otherwise
        self.queue = deque(sorted(self.queue, key=lambda s: -s["energy"]))
        self._pending = self.queue.popleft()
        return self._pending

    def _mutate(self, seed):
        prov = seed["cand"].get("provenance", {})
        user = (f"{self.ctx}\n\nSEED TEST:\n- task: {prov.get('task_nl', seed['cand'].get('prompt'))}\n"
                f"- environment: {prov.get('env_nl', '(see task)')}\n\n"
                f"TOOL SEQUENCE the agent produced on this seed "
                f"(granularity {self.level}):\n  " + " -> ".join(seed["seq"][:120]) +
                "\n\nReturn ONLY the JSON.")
        resp = chat([{"role": "system", "content": MUTATE_SYS},
                     {"role": "user", "content": user}],
                    model=self.model, temperature=self.temperature, reasoning=True,
                    max_tokens=900, tag="greybox.mutate", skill=self.skill)
        self.cost_usd += resp["cost_usd"]
        obj = extract_json(resp["content"])
        return obj["task"].strip(), obj["env"].strip()

    def propose(self):
        # Loop (not recursion) so an unbounded run of realize/build skips can never
        # exhaust the Python stack; each attempt spends one unit of a seed's energy.
        while True:
            seed = self._choose_seed()
            if seed is None:
                return None
            seed["energy"] -= 1
            task_nl, env_nl = self._mutate(seed)
            self.stats["mutations"] += 1
            try:
                prompt, tail, c = realize(self.ctx, task_nl, env_nl, self.model)
                self.cost_usd += c
            except Exception as e:
                print(f"  [greybox realize skip] {e}")
                self.stats["skipped_builds"] += 1
                continue
            cid = "cand-" + uuid.uuid4().hex[:12]
            tag = f"skillrace/{cid}:built"
            built = None
            for attempt in range(self.build_retries + 1):
                cf = containerfile_for(self.base_image, tail)
                ok, out = build_image(cf, tag, timeout=self.build_timeout)
                if ok:
                    built = {"candidate_id": cid, "skill": self.skill, "prompt": prompt,
                             "base_image": self.base_image, "containerfile": cf,
                             "built_image": tag,
                             "provenance": {"source": "greybox",
                                            "parent_candidate": seed["cand"].get("candidate_id"),
                                            "granularity": self.level,
                                            "task_nl": task_nl, "env_nl": env_nl,
                                            "build_attempts": attempt + 1}}
                    break
                if attempt < self.build_retries:
                    try:
                        tail, c = repair_tail(self.ctx, tail, out, self.model)
                        self.cost_usd += c
                    except Exception:
                        break
            if built is not None:
                return built
            self.stats["skipped_builds"] += 1
            # fall through to the while-loop: try the next seed / remaining energy

    def state(self):
        return {"skill": self.skill, "source": "greybox", "level": self.level,
                "novelty": {"tools": len(self.d_tool), "transitions": len(self.d_trans),
                            "sequences": len(self.d_seq)},
                "queue": len(self.queue), "stats": self.stats,
                "gen_cost_usd": round(self.cost_usd, 6)}


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
