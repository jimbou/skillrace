"""The assembled campaign loop — one loop, three drop-in generators.

  random       : 30 fresh cases and no bootstrap or execution feedback.
  adaptive     : 10 counted bootstrap cases, then 20 exploration cases.
  every phase  : shared sanity -> agent -> path-only Python checker -> fold.

The exact bootstrap cases are independently generated per adaptive campaign; their
generator configuration and counted allocation are identical and recorded.

Explore phase: until the counted agent-run budget is spent —
                 propose -> run agent -> author/check properties -> fold.

Only `make_generator(method)` differs between rungs ("random" | "greybox" |
"skillrace"); the runner and the property checker are byte-identical subprocess
invocations, so a measured difference is about TEST GENERATION, not detection.

Per-iteration record (campaign.json): candidate + provenance, run dir, termination,
verdict summary, violated property ids, wall-clock, costs, and — for the skillrace
rung — the run classification against the targeted branch:
  predicted_divergence  it reached the branch and took a NEW way (coverage gained)
  no_divergence         it reached the branch but behaved as before (guard not causal)
  path_miss             it never reached the branch (an earlier guard failed)

Usage:
  python -m skillrace.loop --method skillrace --skill fix-failing-test \
      --skill-dir skills/fix-failing-test --base skillrace/fix-failing-test:base \
      --props skills/fix-failing-test/properties.json \
      --protocol experiments/protocols/issta-main.draft.json \
      --out out/campaign/skillrace/fix-failing-test
"""
from __future__ import annotations
import argparse
import contextlib
import json
import pathlib
import re
import subprocess
import sys
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor

from .adaptive_artifacts import (
    capture_adaptive_artifacts,
    complete_fold_artifact_version,
    publish_completed_fold_artifact_version,
    recover_fold_artifact_version,
    restore_adaptive_artifacts,
    stage_fold_artifact_version,
    verify_adaptive_artifacts,
)
from .ablations import get_strategy
from .generator import DEFAULT_BUILD_RETRIES, RandomGenerator
from .greybox import GreyboxGenerator
from .campaign_engine import CampaignEngine
from .campaign_protocol import CampaignProtocol
from .closeai import OutcomeUnknownError
from .io_utils import atomic_write_json, atomic_write_text, canonical_json_hash
from .input_identity import skill_input_tree_hash
from .property_specs import load_applicable_properties
from .runtime_trust import (
    RuntimeFingerprintError,
    RuntimeIntegrityError,
    verify_runtime_integrity,
)
from .resource_pool import ResourcePool
from .parallel_campaign import plan_epoch
from .sanity import (
    CandidateSanityRejection,
    SanityInfrastructureError,
    run_candidate_sanity,
)
from .simplify_trace import render, target_episodes, call_reasonings
from .segment import segment_text, validate as validate_spans, assemble
from .tree import fold as tree_fold, empty_tree
from . import guards as G
from .model_policy import EXPERIMENT_MODELS


DEFAULT_MAIN_PROTOCOL_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "experiments" / "protocols" / "issta-main.glm-4.5-flash.draft.json"
)
DEFAULT_PILOT_PROTOCOL_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "experiments" / "protocols" / "pilot.glm-4.5-flash.json"
)
FROZEN_HEADLINE_PROTOCOL_IDS = {
    "glm-4.5-flash": "skillrace-issta-main-glm-4.5-flash-v1",
    "deepseek-v4-flash": "skillrace-issta-main-deepseek-v4-flash-v1",
}
FROZEN_HEADLINE_FIELDS = {
    "budget": 30,
    "bootstrap_count": 10,
    "max_generation_attempts_per_execution": 5,
    "greybox_level": "L1",
    "random_seed": 20260711,
    "seed_generator": {
        "batch_size": 5,
        "temperature": 0.9,
        "build_retries": DEFAULT_BUILD_RETRIES,
    },
}


def _plain_json(value):
    """Recursively thaw immutable planning values into JSON-native containers."""
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    return value


def campaign_output_identity(
    *,
    out_dir,
    base_image,
    base_image_identity,
    skill_input_hash,
    properties,
    applicability,
):
    """Identity of the reviewed inputs that must not change across a resume."""
    return canonical_json_hash(
        {
            "schema": "campaign-output-identity/1",
            "path": str(pathlib.Path(out_dir).resolve()),
            "base_image": base_image,
            "base_image_identity": base_image_identity,
            "skill_input_hash": skill_input_hash,
            "properties": properties,
            "applicability": applicability,
        }
    )


def resolve_base_image_identity(base_image, *, resolver=None):
    """Resolve a Docker tag to an immutable image ID without executing the image."""
    if resolver is None:
        def resolver(image):
            process = subprocess.run(
                ["docker", "image", "inspect", "--format", "{{.Id}}", image],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if process.returncode != 0:
                raise RuntimeError(
                    (process.stdout + process.stderr).strip()[-500:]
                    or "docker image inspect failed"
                )
            return process.stdout.strip()

    identity = resolver(base_image)
    if (
        not isinstance(identity, str)
        or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", identity)
    ):
        raise ValueError("resolver did not return an immutable base-image sha256 ID")
    return identity.lower()


def _require_frozen_headline(protocol: CampaignProtocol) -> CampaignProtocol:
    exact = (
        protocol.status == "frozen"
        and protocol.model in EXPERIMENT_MODELS
        and protocol.protocol_id == FROZEN_HEADLINE_PROTOCOL_IDS[protocol.model]
        and not protocol.protocol_id.startswith("development-only-")
        and all(
            getattr(protocol, field) == expected
            for field, expected in FROZEN_HEADLINE_FIELDS.items()
            if field != "seed_generator"
        )
        and protocol.seed_generator == FROZEN_HEADLINE_FIELDS["seed_generator"]
    )
    if not exact:
        raise ValueError(
            "non-development execution requires the exact approved frozen headline "
            "protocol for one selected model track (30/10, L1, fixed attempts/settings)"
        )
    return protocol


# ------------------------------------------------------------------ plumbing

def materialize_case(cand, cases_dir):
    """Write a candidate dict as a runnable case dir (Dockerfile + candidate.json)."""
    case = pathlib.Path(cases_dir) / cand["candidate_id"]
    case.mkdir(parents=True, exist_ok=True)
    atomic_write_text(case / "Dockerfile", cand["containerfile"])
    atomic_write_json(case / "candidate.json", cand)
    return str(case)


def classify_runner_result(returncode, manifest):
    """Classify whether a runner outcome is a counted agent execution."""
    if not manifest or not manifest.get("agent_started"):
        return {
            "agent_started": False,
            "consume_budget": False,
            "status": "infrastructure_error",
        }
    reason = (manifest.get("termination") or {}).get("reason")
    if reason == "completed":
        status = "completed"
    elif reason == "timeout" or returncode == 124:
        status = "timeout"
    else:
        status = "agent_error"
    return {"agent_started": True, "consume_budget": True, "status": status}


def classify_oracle_result(returncode, verdicts):
    """Keep checker execution failure distinct from inconclusive verdicts."""
    if returncode != 0:
        return "error"
    inconclusive = sum(item.get("holds") is None for item in verdicts)
    if inconclusive and inconclusive == len(verdicts):
        return "inconclusive"
    if inconclusive:
        return "partially_inconclusive"
    return "completed"


def run_agent(case_dir, run_dir, agent_model, wall_clock, skill_dir):
    """SHARED runner (byte-identical across rungs)."""
    p = subprocess.run([sys.executable, "-m", "skillrace.run_case",
                        "--case", str(case_dir), "--model", agent_model,
                        "--skill-dir", str(skill_dir),
                        "--out", str(run_dir), "--wall-clock", str(wall_clock)],
                       capture_output=True, text=True)
    tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-4:])
    manifest_path = pathlib.Path(run_dir) / "run.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    return p.returncode, tail, manifest


def bind_run_cost_receipt(run_dir):
    """Bind run_case's cost artifact into the immutable execution receipt."""
    path = pathlib.Path(run_dir) / "cost.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        cost = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"malformed run cost artifact at {path}: {error}") from error
    if not isinstance(cost, dict):
        raise ValueError(f"malformed run cost artifact at {path}: expected object")
    return {
        "schema": "run-cost-receipt/1",
        "path": str(path),
        "cost": cost,
        "cost_hash": canonical_json_hash(cost),
    }


def check_run(
    run_dir,
    model,
    *,
    properties=None,
    candidate=None,
    applicability=None,
):
    """Run fixed checks plus blinded post-run generated checks."""
    run_dir = pathlib.Path(run_dir)
    if not (run_dir / "run.json").is_file():
        return [], ["run.json missing; property checker suppressed"], None
    command = [
        sys.executable,
        "-m",
        "skillrace.check_properties",
        "--run",
        str(run_dir),
        "--model",
        model,
    ]
    if properties is not None:
        provenance = (candidate or {}).get("provenance") or {}
        checker_input = {
            "schema": "post-run-check-input/1",
            "properties": properties,
            "candidate": {
                "skill": (candidate or {}).get("skill"),
                "prompt": (candidate or {}).get("prompt", ""),
                "provenance": {"env_nl": provenance.get("env_nl", "")},
            },
            "applicability": applicability,
        }
        input_path = run_dir / "post-run-check-input.json"
        atomic_write_json(input_path, checker_input)
        command.extend(["--post-run-input", str(input_path)])
    unknown_path = run_dir / "checker-outcome-unknown.json"
    if unknown_path.is_file():
        unknown = json.loads(unknown_path.read_text())
        raise OutcomeUnknownError(unknown.get("error", "checker outcome unknown"))
    p = subprocess.run(command, capture_output=True, text=True)
    if unknown_path.is_file():
        unknown = json.loads(unknown_path.read_text())
        raise OutcomeUnknownError(unknown.get("error", "checker outcome unknown"))
    vp = run_dir / "verdicts.json"
    verdicts = json.loads(vp.read_text()) if vp.exists() else []
    return verdicts, (p.stdout + p.stderr).strip().splitlines()[-3:], p.returncode


class RealCampaignExecutor:
    """One shared materialize/trust/sanity/run/post-run-check execution pipeline.

    It always returns a JSON result instead of raising a stage failure.  That lets
    :class:`CampaignEngine` publish the immutable result before mutating campaign
    state and clean the candidate image only after its final consumer returns.
    """

    def __init__(
        self,
        *,
        skill,
        skill_dir,
        cases_dir,
        runs_dir,
        properties,
        applicability,
        model,
        wall_clock,
    ):
        self.skill = skill
        self.skill_dir = pathlib.Path(skill_dir)
        self.cases_dir = pathlib.Path(cases_dir)
        self.runs_dir = pathlib.Path(runs_dir)
        self.properties = properties
        self.applicability = applicability
        self.model = model
        self.wall_clock = wall_clock

    @staticmethod
    def _base_result(status, *, case_dir=None, agent_started=False):
        return {
            "agent_started": agent_started,
            "status": status,
            "generation_status": "generated",
            "infrastructure_status": "pending",
            "runner_status": "not_started",
            "oracle_status": "not_run",
            "violated": [],
            "inconclusive": [],
            "n_verdicts": 0,
            "case_dir": str(case_dir) if case_dir is not None else None,
        }

    def execute(self, candidate, execution_id, attempt_id, *, lifecycle=None):
        t0 = time.time()
        try:
            case_dir = pathlib.Path(materialize_case(candidate, self.cases_dir))
        except Exception as error:
            result = self._base_result("materialization_error")
            result.update(
                generation_status="materialization_error",
                infrastructure_status="not_started",
                error=str(error)[:500],
            )
            return result
        result = self._base_result("generated", case_dir=case_dir)

        runtime_path = case_dir / "runtime-integrity.json"
        try:
            fingerprint = verify_runtime_integrity(
                candidate.get("base_image_identity")
                or candidate.get("base_image"),
                candidate.get("built_image"),
            )
        except RuntimeIntegrityError as error:
            report = {
                "schema": "runtime-integrity/1",
                "valid": False,
                "outcome": "generation_rejection",
                "error": str(error)[:500],
            }
            atomic_write_json(runtime_path, report)
            result.update(
                status="runtime_rejected",
                generation_status="runtime_rejected",
                infrastructure_status="not_started",
                runtime_integrity=str(runtime_path),
                runtime_rejection=str(error)[:300],
                seconds=round(time.time() - t0, 3),
            )
            return result
        except Exception as error:
            report = {
                "schema": "runtime-integrity/1",
                "valid": False,
                "outcome": "infrastructure_error",
                "error": str(error)[:500],
            }
            atomic_write_json(runtime_path, report)
            result.update(
                status="runtime_infrastructure_error",
                infrastructure_status="runtime_fingerprint_error",
                runtime_integrity=str(runtime_path),
                runtime_error=str(error)[:300],
                seconds=round(time.time() - t0, 3),
            )
            return result
        atomic_write_json(
            runtime_path,
            {
                "schema": "runtime-integrity/1",
                "valid": True,
                "base_image": candidate.get("base_image"),
                "base_image_identity": candidate.get("base_image_identity"),
                "candidate_image": candidate.get("built_image"),
                "candidate_fingerprint": fingerprint,
            },
        )
        result["runtime_integrity"] = str(runtime_path)

        sanity_path = case_dir / "sanity.json"
        try:
            sanity_report = run_candidate_sanity(
                candidate.get("built_image"), candidate.get("sanity")
            )
        except CandidateSanityRejection as error:
            sanity_report = {
                "schema": "candidate-sanity/1",
                "image": candidate.get("built_image"),
                "valid": False,
                "outcome": "generation_rejection",
                "rejection": "invalid-sanity-contract",
                "checks": [],
                "error": str(error)[:300],
            }
        except Exception as error:
            sanity_report = {
                "schema": "candidate-sanity/1",
                "image": candidate.get("built_image"),
                "valid": False,
                "outcome": "infrastructure_error",
                "rejection": None,
                "checks": [],
                "error": str(error)[:300],
            }
        atomic_write_json(sanity_path, sanity_report)
        result["sanity"] = str(sanity_path)
        if sanity_report.get("outcome") == "infrastructure_error":
            result.update(
                status="sanity_infrastructure_error",
                infrastructure_status="sanity_infrastructure_error",
                sanity_status="infrastructure_error",
                sanity_error=sanity_report.get("error"),
                seconds=round(time.time() - t0, 3),
            )
            return result
        if not sanity_report.get("valid"):
            result.update(
                status="sanity_rejected",
                generation_status="sanity_rejected",
                infrastructure_status="not_started",
                sanity_status="rejected",
                sanity_rejection=sanity_report.get("rejection"),
                seconds=round(time.time() - t0, 3),
            )
            return result
        result["sanity_status"] = "accepted"

        run_dir = self.runs_dir / f"{attempt_id}-{candidate.get('candidate_id', 'x')[:24]}"
        result["run_dir"] = str(run_dir)
        if lifecycle is not None:
            lifecycle(
                "started",
                {
                    "run_dir": str(run_dir),
                    "case_dir": str(case_dir),
                    "launch_committed": None,
                    "agent_started": None,
                },
            )
        try:
            returncode, tail, manifest = run_agent(
                case_dir,
                run_dir,
                self.model,
                self.wall_clock,
                self.skill_dir,
            )
        except Exception as error:
            returncode, tail, manifest = None, str(error), None
            manifest_path = run_dir / "run.json"
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text())
                except (OSError, json.JSONDecodeError):
                    manifest = None
        cost_receipt = bind_run_cost_receipt(run_dir)
        if cost_receipt is not None:
            result["run_cost_receipt"] = cost_receipt
        runner = classify_runner_result(returncode, manifest)
        result.update(
            status=runner["status"],
            runner_status=runner["status"],
            runner_returncode=returncode,
            agent_started=runner["agent_started"],
        )
        if isinstance(manifest, Mapping):
            run_id = manifest.get("run_id")
            if isinstance(run_id, str) and run_id:
                result["run_id"] = run_id
        if lifecycle is not None:
            lifecycle("external-terminal", {"result": dict(result)})
        if not runner["consume_budget"]:
            result.update(
                infrastructure_status="runner_error",
                runner_error=str(tail)[-300:],
                seconds=round(time.time() - t0, 3),
            )
            return result

        result["infrastructure_status"] = "ready"
        result["termination"] = manifest.get("termination") if manifest else None
        try:
            verdicts, checker_tail, checker_returncode = check_run(
                run_dir,
                self.model,
                properties=self.properties,
                candidate=candidate,
                applicability=self.applicability,
            )
        except OutcomeUnknownError as error:
            result.update(
                status="external-outcome-indeterminate",
                infrastructure_status="external_state_indeterminate",
                oracle_status="not_run",
                cost_accounting="unknown-nonzero-possible",
                unrecorded_cost_possible=True,
                stop_campaign=True,
                error=str(error)[:500],
                seconds=round(time.time() - t0, 3),
            )
            return result
        except Exception as error:
            verdicts, checker_tail, checker_returncode = [], [str(error)], None
            result["oracle_error"] = str(error)[:300]
        result["oracle_status"] = classify_oracle_result(checker_returncode, verdicts)
        checker_manifest_path = pathlib.Path(run_dir) / "checks" / "manifest.json"
        if checker_manifest_path.is_file():
            checker_manifest = json.loads(checker_manifest_path.read_text())
            result["compile_cost_provider_credits"] = checker_manifest.get(
                "cost_provider_credits", 0.0
            )
            if checker_manifest.get("cost_accounting"):
                result["cost_accounting"] = checker_manifest["cost_accounting"]
        if checker_returncode not in (0, None):
            result["oracle_error"] = "\n".join(checker_tail)[-300:]
        result["violated"] = [
            verdict["property_id"] for verdict in verdicts if verdict.get("violated")
        ]
        result["inconclusive"] = [
            verdict["property_id"]
            for verdict in verdicts
            if verdict.get("holds") is None
        ]
        result["n_verdicts"] = len(verdicts)
        provenance = candidate.get("provenance") or {}
        if provenance.get("source") == "skillrace":
            result["discovery_relationships"] = classify_property_discoveries(
                result["violated"],
                targeted_property=provenance.get("targeted_property"),
            )
            result["observed_violation_count"] = len(
                result["discovery_relationships"]
            )
            result["confirmation_status"] = "unconfirmed"
        result["seconds"] = round(time.time() - t0, 3)
        return result


class _SkillRACEEngineAdapter:
    """Adapt the existing case-materializing search component to engine ports."""

    def __init__(self, generator, cases_dir):
        self.generator = generator
        self.cases_dir = pathlib.Path(cases_dir)
        self.skill = generator.skill

    def propose(self):
        case_dir, source = self.generator.propose(self.cases_dir)
        if case_dir is None:
            return None, source or "skillrace"
        candidate = json.loads((pathlib.Path(case_dir) / "candidate.json").read_text())
        candidate["case_dir"] = str(case_dir)
        return candidate, source

    def propose_epoch(self, reservations, *, batch_dir, **kwargs):
        generated = self.generator.propose_epoch(
            reservations,
            batch_dir=batch_dir,
            cases_dir=self.cases_dir,
            **kwargs,
        )
        results = []
        for item in generated:
            case_dir = item.get("case_dir")
            candidate = None
            error = item.get("error")
            if case_dir is not None:
                candidate_path = pathlib.Path(case_dir) / "candidate.json"
                try:
                    candidate = json.loads(candidate_path.read_text())
                except (OSError, json.JSONDecodeError) as read_error:
                    error = {
                        "type": type(read_error).__name__,
                        "reason": "candidate-artifact-error",
                        "message": str(read_error)[:500],
                    }
                else:
                    candidate["case_dir"] = str(case_dir)
            results.append(
                {
                    "candidate": candidate,
                    "source": item.get("source") or "skillrace",
                    "error": error,
                }
            )
        return results

    def fold(self, candidate, run_dir, phase="explore", attempt_id=None):
        execution_result = candidate.pop("_execution_result", {})
        actions = self.generator.fold(
            candidate, run_dir, phase=phase, attempt_id=attempt_id
        )
        provenance = candidate.get("provenance") or {}
        if provenance.get("source") != "skillrace":
            return {"actions": actions, "classification": None}
        summary = summarize_skillrace_discoveries(
            actions,
            target_parent=provenance.get("target_parent"),
            violated_property_ids=execution_result.get("violated", []),
            targeted_property=provenance.get("targeted_property"),
        )
        return {
            "actions": actions,
            "classification": summary.pop("branch_outcome"),
            **summary,
            "target_parent": provenance.get("target_parent"),
            "validation": provenance.get("validation"),
        }

    def snapshot(self):
        return self.generator.snapshot()

    def restore(self, snapshot):
        self.generator.restore(snapshot)

    def restore_for_pending_fold(self, snapshot, attempt_id):
        if hasattr(self.generator, "restore_for_pending_fold"):
            return self.generator.restore_for_pending_fold(snapshot, attempt_id)
        self.generator.restore(snapshot)
        return "unsupported-normal-restore"

    def drain_buffer(self):
        if hasattr(self.generator.seed_gen, "drain_buffer"):
            return self.generator.seed_gen.drain_buffer()
        return []

    def state(self):
        return self.generator.state()


def segment_and_fold(run_dir, tree_path, model, skill, attempt_id=None):
    """SkillRACE fold: segment the run, fold its episode line into the tree.
    Returns (actions|None, error, cost)."""
    run_dir = pathlib.Path(run_dir)
    tree_path = pathlib.Path(tree_path)
    if attempt_id is not None and tree_path.exists():
        existing_tree = json.loads(tree_path.read_text())
        prior = existing_tree.get("folded_attempts", {}).get(attempt_id)
        if prior is not None:
            if recover_fold_artifact_version(tree_path, attempt_id) is None:
                publish_completed_fold_artifact_version(tree_path, attempt_id)
            return prior.get("actions"), prior.get("error"), 0.0
    sess = run_dir / "raw" / "session.jsonl"
    if not sess.exists():
        return None, "no session trace", 0.0
    simplified, n = render(sess)
    if n == 0:
        return None, "empty trace", 0.0
    eps, cost = segment_text(simplified, target_episodes(n), model)
    ok, err = validate_spans(eps, n)
    if not ok:
        eps, c = segment_text(simplified + f"\n\n(Your previous split was invalid: {err}. "
                              "Make the spans partition all tool calls in order.)",
                              target_episodes(n), model)
        cost += c
        ok, err = validate_spans(eps, n)
    if not ok:
        atomic_write_json(
            run_dir / "episodes.json",
            {"unsegmentable": True, "error": err},
        )
        return None, f"unsegmentable: {err}", cost
    episodes = assemble(eps, call_reasonings(sess))
    atomic_write_json(
        run_dir / "episodes.json",
        {"run": str(run_dir), "n_tool_calls": n, "episodes": episodes},
    )

    tree = (json.loads(tree_path.read_text()) if tree_path.exists()
            else empty_tree(skill))
    if attempt_id is not None:
        prior = tree.get("folded_attempts", {}).get(attempt_id)
        if prior is not None:
            if recover_fold_artifact_version(tree_path, attempt_id) is None:
                publish_completed_fold_artifact_version(tree_path, attempt_id)
            return prior.get("actions"), prior.get("error"), cost
    cache_path = tree_path.with_suffix(".cache.json")
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    actions = tree_fold(tree, episodes, run_dir.name, model, cache,
                        run_meta={"dir": str(run_dir), "session": str(sess),
                                  "episodes": str(run_dir / "episodes.json")})
    if attempt_id is not None:
        tree.setdefault("folded_attempts", {})[attempt_id] = {
            "run_dir": str(run_dir),
            "actions": actions,
            "error": None,
        }
    tree_path.parent.mkdir(parents=True, exist_ok=True)
    if attempt_id is not None:
        rendered = {
            "tree.json": json.dumps(tree, indent=2, ensure_ascii=False) + "\n",
            "tree.cache.json": json.dumps(cache, indent=2, ensure_ascii=False) + "\n",
        }
        stage_fold_artifact_version(
            tree_path,
            attempt_id,
            capture_adaptive_artifacts(tree_path, overrides=rendered),
        )
    # Cache first: if interrupted before the tree marker is published, a replay
    # uses the same judgments.  Once the tree is visible, its attempt marker makes
    # the fold idempotent and the cache has already reached the matching version.
    atomic_write_json(cache_path, cache)
    atomic_write_json(tree_path, tree)
    if attempt_id is not None:
        complete_fold_artifact_version(tree_path, attempt_id)
    return actions, None, cost


def classify_target_execution(actions, target_parent):
    """Diagnose how an execution related to the branch that motivated it.

    This classification never accepts or rejects a test and never gates defect
    yield.  A different newly discovered branch is retained explicitly.
    """
    if actions is None:
        return "unfolded"
    if target_parent is None:
        return (
            "intended_branch"
            if actions and actions[0][0] == "new"
            else "no_divergence"
        )
    node_ids = [node_id for _, node_id, _ in actions]
    if target_parent in node_ids:
        position = node_ids.index(target_parent)
        if position + 1 < len(actions) and actions[position + 1][0] == "new":
            return "intended_branch"
        return "no_divergence"
    if any(kind == "new" for kind, _, _ in actions):
        return "different_new_branch"
    return "path_miss"


def classify_property_discoveries(violated_property_ids, targeted_property=None):
    """Label targeting provenance while counting all distinct violations equally."""
    if targeted_property is not None and not isinstance(targeted_property, str):
        raise ValueError("targeted_property must be a string or null")
    discoveries = []
    seen = set()
    for property_id in violated_property_ids:
        if not isinstance(property_id, str) or not property_id:
            raise ValueError("violated property IDs must be nonempty strings")
        if property_id in seen:
            continue
        seen.add(property_id)
        discoveries.append(
            {
                "property_id": property_id,
                "relationship": (
                    "targeted"
                    if targeted_property is not None and property_id == targeted_property
                    else "serendipitous"
                ),
            }
        )
    return discoveries


def summarize_skillrace_discoveries(
    actions,
    *,
    target_parent,
    violated_property_ids,
    targeted_property=None,
):
    discoveries = classify_property_discoveries(
        violated_property_ids, targeted_property=targeted_property
    )
    return {
        "branch_outcome": classify_target_execution(actions, target_parent),
        "discoveries": discoveries,
        "observed_violation_count": len(discoveries),
        "observed_property_ids": [item["property_id"] for item in discoveries],
        "confirmation_status": "unconfirmed",
    }


def classify(actions, parent_id):
    """How the new run relates to the branch it targeted (skillrace rung only)."""
    if actions is None:
        return "unfolded"
    ids = [nid for _, nid, _ in actions]
    if parent_id is None:                       # branch at the virtual root
        return ("predicted_divergence" if actions and actions[0][0] == "new"
                else "no_divergence")
    if parent_id not in ids:
        return "path_miss"
    i = ids.index(parent_id)
    if i + 1 >= len(actions):
        return "path_miss"                      # run ENDED at the branch node
    return "predicted_divergence" if actions[i + 1][0] == "new" else "no_divergence"


# ------------------------------------------------------------------ generators

class SkillRACEGenerator:
    """Components 2-5 behind the Generator protocol. propose() only ever returns a
    VALIDATED candidate case (or falls back to the seed generator, counted)."""

    def __init__(self, skill, skill_dir, base_image, props, model, out_dir,
                 seed_gen, base_image_identity=None, *, strategy="full",
                 headline=True):
        self.skill, self.skill_dir, self.base = skill, skill_dir, base_image
        self.skill_dir = pathlib.Path(skill_dir)
        self.skill_input_hash = skill_input_tree_hash(self.skill_dir)
        self.base_image_identity = base_image_identity or base_image
        self.strategy = get_strategy(strategy).validate(headline=headline)
        self.props, self.model = props, model
        self.out = pathlib.Path(out_dir)
        self.tree_path = self.out / "tree.json"
        self.seed_gen = seed_gen
        self.cost_provider_credits = 0.0
        self.stats = {"synthesized": 0, "fallbacks": 0, "synth_failures": 0}
        self.last_target_parent = None      # parent node id of the targeted branch
        self.last_target_metadata = None
        self.failure_state = None
        self.folded_attempt_ids = []
        self._fold_results = {}
        self._restored_tree_artifacts = {}

    def propose(self, cases_dir):
        self.last_target_parent = None
        self.last_target_metadata = None
        if self.tree_path.exists():
            tree = json.loads(self.tree_path.read_text())
            state, c = G.extract_all_guards(
                tree,
                self.tree_path,
                self.model,
                skill=self.skill,
                signal_mode=self.strategy.signal_mode,
            )
            self.cost_provider_credits += c
            frontier = G.build_frontier(state)
            target, c = G.select_target(frontier, self.props, self.model,
                                        skill=self.skill)
            self.cost_provider_credits += c
            if target:
                self.last_target_metadata = {
                    "parent_id": target["item"]["guard"].get("parent_id"),
                    "branch_key": target["item"]["guard"].get("branch_key"),
                    "mutation": target.get("mutation"),
                    "targeted_property": target.get("targeted_property"),
                    "rationale": target.get("rationale"),
                }
                case, info, c = G.synthesize(tree, target, self.skill,
                                             self.skill_dir,
                                             self.base,
                                             self.model, cases_dir,
                                             requested_base_image=self.base,
                                             base_image_identity=self.base_image_identity)
                self.cost_provider_credits += c
                st, sp = G.load_guard_state(
                    self.tree_path, signal_mode=self.strategy.signal_mode
                )
                G.mark_tried(st, sp, target["item"]["guard"]["branch_key"],
                             target["mutation"])
                if case:
                    self.stats["synthesized"] += 1
                    self.last_target_parent = target["item"]["guard"]["parent_id"]
                    self.failure_state = None
                    return case, "skillrace"
                self.stats["synth_failures"] += 1
        # frontier empty / synthesis failed -> diverse seed input (counted)
        self.stats["fallbacks"] += 1
        cand = self.seed_gen.propose()
        if cand is None:
            self.failure_state = {
                "type": "GenerationFailure",
                "reason": "fallback-exhausted",
                "message": "SkillRACE fallback generator returned no candidate",
            }
            return None, None
        cand["provenance"]["source"] = "skillrace-fallback"
        self.failure_state = None
        return materialize_case(cand, cases_dir), "skillrace-fallback"

    def _load_or_plan_epoch_targets(
        self,
        reservations,
        *,
        batch_dir,
        epoch,
        tree_version,
        frozen_state_hash,
        resource_pool,
    ):
        """Persist the complete target/reservation map before synthesis starts."""
        plan_path = pathlib.Path(batch_dir) / "targets.json"
        expected_ids = [reservation.candidate_id for reservation in reservations]
        if plan_path.exists():
            try:
                plan = json.loads(plan_path.read_text())
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError(f"malformed SkillRACE target plan: {error}") from error
            core = {key: value for key, value in plan.items() if key != "plan_hash"}
            if (
                plan.get("schema") != "skillrace-target-plan/1"
                or plan.get("plan_hash") != canonical_json_hash(core)
                or plan.get("epoch") != epoch
                or plan.get("tree_version") != tree_version
                or plan.get("frozen_state_hash") != frozen_state_hash
                or [
                    item.get("candidate_id")
                    for item in plan.get("assignments", [])
                    if isinstance(item, dict)
                ]
                != expected_ids
            ):
                raise ValueError("SkillRACE target plan identity/hash mismatch")
            before = float(plan["cost_before"])
            after = float(plan["cost_after_planning"])
            recorded_target_cost = 0.0
            for assignment in plan["assignments"]:
                if assignment.get("target", {}).get("kind") != "target":
                    continue
                receipt_path = (
                    pathlib.Path(batch_dir)
                    / "target-results"
                    / f"{assignment['candidate_id']}.json"
                )
                if not receipt_path.exists():
                    continue
                try:
                    receipt = json.loads(receipt_path.read_text())
                except (OSError, json.JSONDecodeError) as error:
                    raise ValueError(
                        f"malformed SkillRACE synthesis receipt: {error}"
                    ) from error
                receipt_core = {
                    key: value for key, value in receipt.items()
                    if key != "receipt_hash"
                }
                if (
                    receipt.get("assignment_hash")
                    != canonical_json_hash(assignment)
                    or receipt.get("receipt_hash")
                    != canonical_json_hash(receipt_core)
                ):
                    raise ValueError(
                        "SkillRACE synthesis receipt identity/hash mismatch"
                    )
                recorded_target_cost += float(receipt.get("cost_provider_credits", 0.0))
            current = round(float(self.cost_provider_credits), 12)
            if current == round(before, 12):
                self.cost_provider_credits = after
            elif current != round(after, 12) and current != round(
                after + recorded_target_cost, 12
            ):
                raise ValueError("SkillRACE generator cost does not match target plan")
            return plan

        intent_path = pathlib.Path(batch_dir) / "generation.intent.json"
        expected_reservations = [
            {
                "candidate_id": reservation.candidate_id,
                "execution_id": reservation.execution_id,
                "attempt_id": reservation.attempt_id,
            }
            for reservation in reservations
        ]
        if intent_path.exists():
            try:
                intent = json.loads(intent_path.read_text())
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError(
                    f"malformed SkillRACE generation intent: {error}"
                ) from error
            intent_core = {
                key: value for key, value in intent.items() if key != "intent_hash"
            }
            if (
                intent.get("schema") != "skillrace-generation-intent/1"
                or intent.get("intent_hash") != canonical_json_hash(intent_core)
                or intent.get("skill") != self.skill
                or intent.get("epoch") != epoch
                or intent.get("tree_version") != tree_version
                or intent.get("frozen_state_hash") != frozen_state_hash
                or intent.get("reservations") != expected_reservations
            ):
                raise ValueError("SkillRACE generation intent identity/hash mismatch")
            pre_artifacts = intent.get("pre_artifacts")
            if (
                intent.get("pre_artifacts_hash")
                != canonical_json_hash(pre_artifacts)
            ):
                raise ValueError("SkillRACE generation intent artifact hash mismatch")
            if capture_adaptive_artifacts(self.tree_path) != pre_artifacts:
                restore_adaptive_artifacts(self.tree_path, pre_artifacts)
        else:
            pre_artifacts = capture_adaptive_artifacts(self.tree_path)
            intent_core = {
                "schema": "skillrace-generation-intent/1",
                "skill": self.skill,
                "skill_input_hash": self.skill_input_hash,
                "properties_hash": canonical_json_hash(self.props),
                "epoch": epoch,
                "tree_version": tree_version,
                "frozen_state_hash": frozen_state_hash,
                "reservations": expected_reservations,
                "pre_artifacts_hash": canonical_json_hash(pre_artifacts),
                "pre_artifacts": pre_artifacts,
            }
            atomic_write_json(
                intent_path,
                {**intent_core, "intent_hash": canonical_json_hash(intent_core)},
            )

        cost_before = round(float(self.cost_provider_credits), 12)
        frontier = []
        selected = None
        tree = None
        if self.tree_path.exists():
            tree = json.loads(self.tree_path.read_text())
            planning_slot = (
                resource_pool.slots("api")
                if resource_pool is not None
                else contextlib.nullcontext()
            )
            with planning_slot:
                state, cost = G.extract_all_guards(
                    tree,
                    self.tree_path,
                    self.model,
                    skill=self.skill,
                    signal_mode=self.strategy.signal_mode,
                )
                self.cost_provider_credits = round(self.cost_provider_credits + cost, 12)
                frontier = G.build_frontier(state)
                if frontier:
                    selected, cost = G.select_target(
                        frontier, self.props, self.model, skill=self.skill
                    )
                    self.cost_provider_credits = round(self.cost_provider_credits + cost, 12)

        # Preserve the property-guided first choice, then rotate branches so one
        # prolific guard cannot consume the whole frozen epoch.
        frontier = json.loads(json.dumps(frontier))
        if selected is not None:
            selected_key = selected["item"].get("branch_key")
            selected_mutation = selected.get("mutation")
            for index, item in enumerate(frontier):
                if item.get("branch_key") != selected_key:
                    continue
                chosen = frontier.pop(index)
                mutations = list(chosen.get("mutations") or [])
                if selected_mutation in mutations:
                    mutations.remove(selected_mutation)
                    mutations.insert(0, selected_mutation)
                chosen["mutations"] = mutations
                frontier.insert(0, chosen)
                break

        targets = G.diverse_target_batch(
            frontier,
            limit=len(reservations),
            tree_version=tree_version,
            epoch=epoch,
            frozen_state_hash=frozen_state_hash,
        )
        if selected is not None:
            for target in targets:
                if (
                    target.get("kind") == "target"
                    and target.get("branch_key")
                    == selected["item"].get("branch_key")
                    and target.get("mutation") == selected.get("mutation")
                ):
                    target["targeted_property"] = selected.get(
                        "targeted_property"
                    )
                    target["rationale"] = selected.get("rationale", "")
                    break
        planned = plan_epoch(
            "skillrace",
            targets,
            epoch=epoch,
            tree_version=tree_version,
            limit=len(reservations),
            remaining_budget=len(reservations),
            agent_slots=len(reservations),
            frozen_state_hash=frozen_state_hash,
        )
        if len(planned) != len(reservations):
            raise ValueError("SkillRACE target planning lost a reserved slot")
        assignments = [
            {
                "candidate_id": reservation.candidate_id,
                "execution_id": reservation.execution_id,
                "attempt_id": reservation.attempt_id,
                "provenance": dict(reservation.provenance),
                "target": _plain_json(target),
            }
            for reservation, target in zip(reservations, planned)
        ]
        core = {
            "schema": "skillrace-target-plan/1",
            "epoch": epoch,
            "tree_version": tree_version,
            "frozen_state_hash": frozen_state_hash,
            "cost_before": cost_before,
            "cost_after_planning": round(float(self.cost_provider_credits), 12),
            "stats_before": json.loads(json.dumps(self.stats)),
            "assignments": assignments,
        }
        plan = {**core, "plan_hash": canonical_json_hash(core)}
        atomic_write_json(plan_path, plan)
        return plan

    def propose_epoch(
        self,
        reservations,
        *,
        batch_dir,
        cases_dir,
        epoch,
        tree_version,
        frozen_state_hash,
        resource_pool=None,
        **_,
    ):
        """Generate one frozen, branch-diverse SkillRACE exploration epoch."""
        reservations = list(reservations)
        if not reservations:
            return []
        batch_dir = pathlib.Path(batch_dir)
        cases_dir = pathlib.Path(cases_dir)
        batch_dir.mkdir(parents=True, exist_ok=True)
        cases_dir.mkdir(parents=True, exist_ok=True)
        plan = self._load_or_plan_epoch_targets(
            reservations,
            batch_dir=batch_dir,
            epoch=epoch,
            tree_version=tree_version,
            frozen_state_hash=frozen_state_hash,
            resource_pool=resource_pool,
        )
        assignments = plan["assignments"]
        by_candidate = {
            reservation.candidate_id: reservation for reservation in reservations
        }
        results = [None] * len(assignments)

        def synthesize_target(index, assignment):
            target = assignment["target"]
            candidate_id = assignment["candidate_id"]
            receipt_path = batch_dir / "target-results" / f"{candidate_id}.json"
            assignment_hash = canonical_json_hash(assignment)
            if receipt_path.exists():
                try:
                    receipt = json.loads(receipt_path.read_text())
                except (OSError, json.JSONDecodeError) as error:
                    raise ValueError(
                        f"malformed SkillRACE synthesis receipt: {error}"
                    ) from error
                core = {
                    key: value for key, value in receipt.items()
                    if key != "receipt_hash"
                }
                if (
                    receipt.get("schema") != "skillrace-synthesis-receipt/1"
                    or receipt.get("assignment_hash") != assignment_hash
                    or receipt.get("receipt_hash") != canonical_json_hash(core)
                ):
                    raise ValueError(
                        "SkillRACE synthesis receipt identity/hash mismatch"
                    )
                return index, receipt

            slots = (
                resource_pool.slots("api", "docker")
                if resource_pool is not None
                else contextlib.nullcontext()
            )
            try:
                tree = json.loads(self.tree_path.read_text())
                with slots:
                    case_dir, info, cost = G.synthesize(
                        tree,
                        target,
                        self.skill,
                        self.skill_dir,
                        self.base,
                        self.model,
                        cases_dir,
                        requested_base_image=self.base,
                        base_image_identity=self.base_image_identity,
                        proposal_id=candidate_id,
                        provenance=assignment["provenance"],
                    )
                error = None
                if case_dir is None:
                    error = {
                        "type": "GenerationFailure",
                        "reason": "synthesis-failure",
                        "message": str((info or {}).get("error") or "synthesis failed")[:500],
                    }
            except Exception as error_value:
                case_dir, info, cost = None, {}, 0.0
                error = {
                    "type": type(error_value).__name__,
                    "reason": getattr(error_value, "reason", "synthesis-error"),
                    "message": str(error_value)[:500],
                }
            core = {
                "schema": "skillrace-synthesis-receipt/1",
                "assignment_hash": assignment_hash,
                "candidate_id": candidate_id,
                "case_dir": case_dir,
                "info": info,
                "cost_provider_credits": round(float(cost), 12),
                "error": error,
            }
            receipt = {**core, "receipt_hash": canonical_json_hash(core)}
            atomic_write_json(receipt_path, receipt)
            return index, receipt

        targeted = [
            (index, assignment)
            for index, assignment in enumerate(assignments)
            if assignment["target"].get("kind") == "target"
        ]
        if targeted:
            with ThreadPoolExecutor(max_workers=len(targeted)) as executor:
                for index, receipt in executor.map(
                    lambda item: synthesize_target(*item), targeted
                ):
                    results[index] = {
                        "case_dir": receipt.get("case_dir"),
                        "source": "skillrace",
                        "error": receipt.get("error"),
                        "cost_provider_credits": receipt.get("cost_provider_credits", 0.0),
                    }

        fallback = [
            (index, assignment, by_candidate[assignment["candidate_id"]])
            for index, assignment in enumerate(assignments)
            if assignment["target"].get("kind") == "fallback"
        ]
        if fallback:
            fallback_reservations = [item[2] for item in fallback]
            propose_batch = getattr(self.seed_gen, "propose_epoch", None)
            try:
                if callable(propose_batch):
                    fallback_values = list(
                        propose_batch(
                            fallback_reservations,
                            batch_dir=batch_dir / "fallback",
                            epoch=epoch,
                            tree_version=tree_version,
                            frozen_state_hash=frozen_state_hash,
                            resource_pool=resource_pool,
                        )
                    )
                else:
                    fallback_values = []
                    for reservation in fallback_reservations:
                        slots = (
                            resource_pool.slots("api", "docker")
                            if resource_pool is not None
                            else contextlib.nullcontext()
                        )
                        with slots:
                            candidate = self.seed_gen.propose()
                        fallback_values.append(
                            {"candidate": candidate, "source": "bootstrap", "error": None}
                        )
                if len(fallback_values) != len(fallback):
                    raise ValueError(
                        "SkillRACE fallback generator returned the wrong cardinality"
                    )
            except Exception as error_value:
                fallback_values = [
                    {
                        "candidate": None,
                        "source": "skillrace-fallback",
                        "error": {
                            "type": type(error_value).__name__,
                            "reason": getattr(
                                error_value, "reason", "fallback-generation-error"
                            ),
                            "message": str(error_value)[:500],
                        },
                    }
                    for _ in fallback
                ]
            for (index, assignment, reservation), value in zip(
                fallback, fallback_values
            ):
                candidate = value.get("candidate") if isinstance(value, dict) else None
                error = value.get("error") if isinstance(value, dict) else {
                    "type": "GenerationFailure",
                    "reason": "fallback-generation-error",
                    "message": "malformed fallback proposal",
                }
                case_dir = None
                if candidate is not None:
                    candidate = json.loads(json.dumps(candidate))
                    if candidate.get("candidate_id") != reservation.candidate_id:
                        error = {
                            "type": "ValueError",
                            "reason": "reservation-identity-mismatch",
                            "message": "fallback candidate did not use its reserved identity",
                        }
                    else:
                        provenance = dict(candidate.get("provenance") or {})
                        provenance.update(dict(reservation.provenance))
                        provenance.update(
                            {
                                "source": "skillrace-fallback",
                                "frozen_state_hash": frozen_state_hash,
                            }
                        )
                        candidate["provenance"] = provenance
                        case_dir = materialize_case(candidate, cases_dir)
                        error = None
                results[index] = {
                    "case_dir": case_dir,
                    "source": "skillrace-fallback",
                    "error": error,
                    "cost_provider_credits": 0.0,
                }

        target_receipts = [
            result for result in results
            if result is not None and result["source"] == "skillrace"
        ]
        target_cost = round(
            sum(float(result.get("cost_provider_credits", 0.0)) for result in target_receipts),
            12,
        )
        desired_cost = round(float(plan["cost_after_planning"]) + target_cost, 12)
        current_cost = round(float(self.cost_provider_credits), 12)
        if current_cost not in {
            round(float(plan["cost_after_planning"]), 12), desired_cost
        }:
            raise ValueError("SkillRACE target synthesis cost replay mismatch")
        self.cost_provider_credits = desired_cost

        stats = json.loads(json.dumps(plan["stats_before"]))
        stats["synthesized"] += sum(
            result.get("case_dir") is not None for result in target_receipts
        )
        stats["synth_failures"] += sum(
            result.get("case_dir") is None for result in target_receipts
        )
        stats["fallbacks"] += len(fallback)
        if self.stats != plan["stats_before"] and self.stats != stats:
            raise ValueError("SkillRACE epoch statistics replay mismatch")
        self.stats = stats

        if targeted:
            guard_state, guard_path = G.load_guard_state(
                self.tree_path, signal_mode=self.strategy.signal_mode
            )
            guard_state.setdefault("tried", {})
            for _, assignment in targeted:
                target = assignment["target"]
                tried = guard_state["tried"].get(target["branch_key"], [])
                if target["mutation"] not in tried:
                    G.mark_tried(
                        guard_state,
                        guard_path,
                        target["branch_key"],
                        target["mutation"],
                    )

        successful_targets = [
            assignment for index, assignment in targeted
            if results[index] is not None and results[index].get("case_dir")
        ]
        if successful_targets:
            target = successful_targets[-1]["target"]
            self.last_target_parent = target["item"]["guard"].get("parent_id")
            self.last_target_metadata = {
                "parent_id": self.last_target_parent,
                "branch_key": target.get("branch_key"),
                "mutation": target.get("mutation"),
                "targeted_property": target.get("targeted_property"),
                "rationale": target.get("rationale"),
            }
        else:
            self.last_target_parent = None
            self.last_target_metadata = None
        if any(result and result.get("case_dir") for result in results):
            self.failure_state = None
        else:
            self.failure_state = {
                "type": "GenerationFailure",
                "reason": "epoch-generation-failure",
                "message": "SkillRACE epoch produced no runnable candidates",
            }
        return results

    def fold(self, case_dir, run_dir, phase="explore", attempt_id=None):
        if attempt_id is not None and attempt_id in self._fold_results:
            return self._fold_results[attempt_id]
        if isinstance(case_dir, dict):
            case_dir = case_dir.get("case_dir") or case_dir.get("_case_dir")
        actions, err, c = segment_and_fold(
            run_dir, self.tree_path, self.model, self.skill, attempt_id=attempt_id
        )
        self.cost_provider_credits += c
        if err:
            print(f"  [fold] {err}")
        if attempt_id is not None:
            self.folded_attempt_ids.append(attempt_id)
            self._fold_results[attempt_id] = actions
        return actions

    def publish_fold_artifact_version(self, attempt_id):
        return publish_completed_fold_artifact_version(self.tree_path, attempt_id)

    def snapshot(self):
        return {
            "schema": "skillrace-generator/1",
            "source": "skillrace",
            "skill": self.skill,
            "model": self.model,
            "base_image": self.base,
            "base_image_identity": self.base_image_identity,
            "skill_input_hash": self.skill_input_hash,
            "properties_hash": canonical_json_hash(self.props),
            "strategy": self.strategy.as_dict(),
            "strategy_hash": self.strategy.hash,
            "stats": json.loads(json.dumps(self.stats)),
            "cost_provider_credits": self.cost_provider_credits,
            "gen_cost_provider_credits": round(self.cost_provider_credits + self.seed_gen.cost_provider_credits, 6),
            "target_metadata": {
                "last_target_parent": self.last_target_parent,
                "last_target": json.loads(json.dumps(self.last_target_metadata)),
            },
            "tree_artifacts": capture_adaptive_artifacts(self.tree_path),
            "folded_attempt_ids": list(self.folded_attempt_ids),
            "fold_results": json.loads(json.dumps(self._fold_results)),
            "failure_state": json.loads(json.dumps(self.failure_state)),
            "seed_generator_state": self.seed_gen.snapshot(),
        }

    def _restore_search_state(self, snapshot):
        if not isinstance(snapshot, dict) or snapshot.get("schema") != "skillrace-generator/1":
            raise ValueError("unsupported SkillRACE generator snapshot")
        if (
            snapshot.get("source") != "skillrace"
            or snapshot.get("skill") != self.skill
            or snapshot.get("model") != self.model
            or snapshot.get("base_image") != self.base
            or snapshot.get("properties_hash") != canonical_json_hash(self.props)
        ):
            raise ValueError("SkillRACE generator identity/configuration mismatch")
        if snapshot.get("skill_input_hash") != skill_input_tree_hash(self.skill_dir):
            raise ValueError("SkillRACE generator skill input hash mismatch")
        if snapshot.get("base_image_identity") != self.base_image_identity:
            raise ValueError("SkillRACE generator base-image identity mismatch")
        if (
            snapshot.get("strategy") != self.strategy.as_dict()
            or snapshot.get("strategy_hash") != self.strategy.hash
        ):
            raise ValueError("SkillRACE generator strategy mismatch")
        self.stats = json.loads(json.dumps(snapshot.get("stats", {})))
        self.cost_provider_credits = float(snapshot.get("cost_provider_credits", 0.0))
        target = snapshot.get("target_metadata") or {}
        self.last_target_parent = target.get("last_target_parent")
        self.last_target_metadata = json.loads(json.dumps(target.get("last_target")))
        self.folded_attempt_ids = list(snapshot.get("folded_attempt_ids", []))
        self._fold_results = json.loads(json.dumps(snapshot.get("fold_results", {})))
        self.failure_state = json.loads(json.dumps(snapshot.get("failure_state")))
        self._restored_tree_artifacts = json.loads(
            json.dumps(snapshot.get("tree_artifacts", {}))
        )
        self.seed_gen.restore(snapshot["seed_generator_state"])

    def restore(self, snapshot):
        expected_artifacts = snapshot.get("tree_artifacts")
        try:
            verify_adaptive_artifacts(self.tree_path, expected_artifacts)
        except ValueError as artifact_error:
            recovered = False
            intents = sorted(
                self.out.glob(
                    "epochs/epoch-*/generation/generation.intent.json"
                )
            )
            for intent_path in intents:
                try:
                    intent = json.loads(intent_path.read_text())
                except (OSError, json.JSONDecodeError) as error:
                    raise ValueError(
                        f"malformed SkillRACE generation intent: {error}"
                    ) from error
                core = {
                    key: value for key, value in intent.items()
                    if key != "intent_hash"
                }
                if (
                    intent.get("schema") != "skillrace-generation-intent/1"
                    or intent.get("intent_hash") != canonical_json_hash(core)
                    or intent.get("skill") != self.skill
                    or intent.get("skill_input_hash") != self.skill_input_hash
                    or intent.get("properties_hash")
                    != canonical_json_hash(self.props)
                    or intent.get("pre_artifacts_hash")
                    != canonical_json_hash(intent.get("pre_artifacts"))
                ):
                    raise ValueError(
                        "SkillRACE generation intent identity/hash mismatch"
                    )
                if intent.get("pre_artifacts") != expected_artifacts:
                    continue
                restore_adaptive_artifacts(self.tree_path, expected_artifacts)
                recovered = True
                break
            if not recovered:
                raise artifact_error
        self._restore_search_state(snapshot)

    def restore_artifacts(self, snapshot):
        restore_adaptive_artifacts(self.tree_path, snapshot.get("tree_artifacts"))

    def restore_for_pending_fold(self, snapshot, attempt_id):
        version = recover_fold_artifact_version(self.tree_path, attempt_id)
        if version is None:
            self.restore_artifacts(snapshot)
            mode = "rollback"
        else:
            mode = "forward"
        self._restore_search_state(snapshot)
        return mode

    def state(self):
        return {"skill": self.skill, "source": "skillrace", "stats": self.stats,
                "gen_cost_provider_credits": round(self.cost_provider_credits + self.seed_gen.cost_provider_credits, 6)}


# ------------------------------------------------------------------ the loop

def resolve_campaign_protocol(
    protocol=None,
    *,
    development_only=False,
    budget=None,
    bootstrap_count=None,
    max_attempts=None,
    model=None,
    batch_size=None,
    temperature=None,
    build_retries=None,
    greybox_level=None,
    random_seed=None,
):
    """Load the reviewed authority, or explicitly construct a development protocol."""
    overrides = {
        "budget": budget,
        "bootstrap_count": bootstrap_count,
        "max_attempts": max_attempts,
        "model": model,
        "batch_size": batch_size,
        "temperature": temperature,
        "build_retries": build_retries,
        "greybox_level": greybox_level,
        "random_seed": random_seed,
    }
    if not development_only and any(value is not None for value in overrides.values()):
        raise ValueError(
            "headline protocol fields cannot be overridden; use development_only=True "
            "for explicitly non-headline tests"
        )
    if isinstance(protocol, CampaignProtocol):
        loaded = protocol
    else:
        default_path = (
            DEFAULT_PILOT_PROTOCOL_PATH if development_only
            else DEFAULT_MAIN_PROTOCOL_PATH
        )
        loaded = CampaignProtocol.load(protocol or default_path)
    if not development_only:
        if loaded.seed_generator["build_retries"] != DEFAULT_BUILD_RETRIES:
            raise ValueError("reviewed protocol must use the shared build retry policy")
        return _require_frozen_headline(loaded)

    # A protocol embedded by an already-started development campaign already has
    # its final identity. Preserve it byte-for-byte on resume instead of adding a
    # second development prefix or reconstructing its hash.
    if (
        loaded.status == "runtime"
        and loaded.protocol_id.startswith("development-only-")
        and not any(value is not None for value in overrides.values())
    ):
        return loaded

    raw = json.loads(json.dumps(loaded.raw))
    raw["protocol_id"] = f"development-only-{loaded.protocol_id}"
    raw["status"] = "runtime"
    if budget is not None:
        raw["budget"] = budget
    if bootstrap_count is not None:
        raw["bootstrap_count"] = bootstrap_count
    if max_attempts is not None:
        raw["max_generation_attempts_per_execution"] = max_attempts
    if model is not None:
        raw["model"] = model
    if batch_size is not None:
        raw["seed_generator"]["batch_size"] = batch_size
    if temperature is not None:
        raw["seed_generator"]["temperature"] = temperature
    if build_retries is not None:
        raw["seed_generator"]["build_retries"] = build_retries
    if greybox_level is not None:
        raw["greybox_level"] = greybox_level
    if random_seed is not None:
        raw["random_seed"] = random_seed
    return CampaignProtocol.from_dict(raw)


def run_campaign(
    method,
    skill,
    skill_dir,
    base,
    props_path,
    out_dir=None,
    *,
    protocol=None,
    development_only=False,
    wall_clock=1800,
    budget=None,
    seed_count=None,
    model=None,
    agent_model=None,
    greybox_level=None,
    seed_k=None,
    seed_temp=None,
    max_pre_agent_attempts=None,
    random_seed=None,
    base_image_resolver=None,
    resource_pool=None,
    epoch_size=None,
):
    if out_dir is None:
        raise ValueError("out_dir is required")
    if agent_model is not None:
        if not development_only:
            raise ValueError("agent_model override requires development_only=True")
        if model is not None and agent_model != model:
            raise ValueError("every model-driven role and the agent must use the same model")
        model = agent_model
    campaign_protocol = resolve_campaign_protocol(
        protocol,
        development_only=development_only,
        budget=budget,
        bootstrap_count=seed_count,
        max_attempts=max_pre_agent_attempts,
        model=model,
        batch_size=seed_k,
        temperature=seed_temp,
        greybox_level=greybox_level,
        random_seed=random_seed,
    )
    model = campaign_protocol.model
    agent_model = model
    budget = campaign_protocol.budget
    seed_count = campaign_protocol.bootstrap_count
    max_pre_agent_attempts = (
        campaign_protocol.max_generation_attempts_per_execution
    )
    greybox_level = campaign_protocol.greybox_level
    seed_k = campaign_protocol.seed_generator["batch_size"]
    seed_temp = campaign_protocol.seed_generator["temperature"]
    build_retries = campaign_protocol.seed_generator["build_retries"]
    random_seed = campaign_protocol.random_seed

    skill_dir = pathlib.Path(skill_dir)
    expected_props = (skill_dir / "properties.json").resolve()
    supplied_props = pathlib.Path(props_path).resolve()
    if supplied_props != expected_props:
        raise ValueError(
            f"props_path must be the reviewed skill properties file {expected_props}"
        )
    out = pathlib.Path(out_dir)
    cases_dir = out / "cases"
    runs_dir = out / "runs"
    cases_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    selection = load_applicable_properties(skill_dir)
    props = selection.properties
    applicability = selection.metadata()
    skill_input_hash = skill_input_tree_hash(skill_dir)
    base_image_identity = resolve_base_image_identity(
        base, resolver=base_image_resolver
    )
    if epoch_size is None:
        epoch_size = 1 if development_only else 4
    if resource_pool is None:
        resource_pool = ResourcePool(api=4, docker=2, agent=2)

    if method == "random":
        seed_gen = None
        gen = RandomGenerator(
            skill,
            skill_dir,
            base,
            model=model,
            k=seed_k,
            temperature=seed_temp,
            source="random",
            build_retries=build_retries,
            outdir=str(cases_dir),
            base_image_identity=base_image_identity,
        )
    elif method == "greybox":
        seed_gen = RandomGenerator(
            skill,
            skill_dir,
            base,
            model=model,
            k=seed_k,
            temperature=seed_temp,
            source="bootstrap",
            build_retries=build_retries,
            outdir=str(cases_dir),
            base_image_identity=base_image_identity,
        )
        gen = GreyboxGenerator(
            skill,
            skill_dir,
            base,
            model=model,
            level=greybox_level,
            temperature=seed_temp,
            build_retries=build_retries,
            base_image_identity=base_image_identity,
        )
    elif method == "skillrace":
        seed_gen = RandomGenerator(
            skill,
            skill_dir,
            base,
            model=model,
            k=seed_k,
            temperature=seed_temp,
            source="bootstrap",
            build_retries=build_retries,
            outdir=str(cases_dir),
            base_image_identity=base_image_identity,
        )
        component = SkillRACEGenerator(
            skill,
            skill_dir,
            base,
            props,
            model,
            out,
            seed_gen,
            base_image_identity=base_image_identity,
        )
        gen = _SkillRACEEngineAdapter(component, cases_dir)
    else:
        raise ValueError(f"unknown method {method!r}")

    executor = RealCampaignExecutor(
        skill=skill,
        skill_dir=skill_dir,
        cases_dir=cases_dir,
        runs_dir=runs_dir,
        properties=props,
        applicability=applicability,
        model=model,
        wall_clock=wall_clock,
    )
    campaign = CampaignEngine(
        protocol=campaign_protocol,
        method=method,
        skill=skill,
        out_dir=out,
        output_identity=campaign_output_identity(
            out_dir=out,
            base_image=base,
            base_image_identity=base_image_identity,
            skill_input_hash=skill_input_hash,
            properties=props,
            applicability=applicability,
        ),
        generator=gen,
        bootstrap_generator=seed_gen,
        executor=executor,
        epoch_size=epoch_size,
        resource_pool=resource_pool,
    ).run()
    print(f"\ncampaign done: {campaign.get('totals', {})}")
    print(f"wrote {out / 'campaign.json'}")
    return campaign
def build_parser():
    ap = argparse.ArgumentParser(description="Run one testing campaign (one method, one skill)")
    ap.add_argument("--method", required=True, choices=["random", "greybox", "skillrace"])
    ap.add_argument("--skill", required=True)
    ap.add_argument("--skill-dir", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--props", required=True)
    ap.add_argument("--protocol", default=str(DEFAULT_MAIN_PROTOCOL_PATH),
                    help="reviewed campaign protocol JSON (owns all headline controls)")
    ap.add_argument(
        "--development-only",
        action="store_true",
        help="allow an explicitly non-headline pilot/development protocol",
    )
    ap.add_argument("--wall-clock", type=int, default=1800)
    ap.add_argument("--epoch-size", type=int, default=1)
    ap.add_argument("--out", required=True)
    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()
    run_campaign(
        args.method,
        args.skill,
        args.skill_dir,
        args.base,
        args.props,
        out_dir=args.out,
        protocol=args.protocol,
        development_only=args.development_only,
        wall_clock=args.wall_clock,
        epoch_size=args.epoch_size,
    )


if __name__ == "__main__":
    main()
