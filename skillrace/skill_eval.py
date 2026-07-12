"""Condition-blind hidden-test execution and explicit RQ3 grading semantics."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from .io_utils import atomic_write_json, file_hash
from .scenario_contract import load_test


EVALUATION_CONDITIONS = (
    "zero-shot",
    "random-feedback",
    "greybox-feedback",
    "skillrace-feedback",
)
EXECUTION_STATUSES = ("completed", "timeout", "error", "inconclusive", "missing")
RAW_EXECUTION_ARTIFACTS = {
    "launch": "launch.json",
    "run": "run.json",
    "verdicts": "verdicts.json",
    "cost": "cost.json",
}


@dataclasses.dataclass(frozen=True)
class HiddenExecutionRequest:
    """Everything the shared runner needs, deliberately without a condition label."""

    test_id: str
    hidden_case_dir: pathlib.Path
    skill_name: str
    skill_dir: pathlib.Path
    run_dir: pathlib.Path
    agent_model: str
    wall_clock: int
    contract_identity: str
    criterion_ids: tuple[str, ...]
    validation_image_digest: str


def _effective_candidate_skill(candidate: Mapping[str, Any]) -> str | None:
    explicit = candidate.get("skill")
    if isinstance(explicit, str) and explicit:
        return explicit
    base = candidate.get("base_image")
    if isinstance(base, str) and base:
        return base.split("/")[-1].split(":")[0]
    return None


def derive_case(test_dir, skill_name, skill_dir, work_dir):
    """Copy a hidden case byte-for-byte; keep the candidate skill as a host mount."""

    test_dir = pathlib.Path(test_dir)
    work_dir = pathlib.Path(work_dir)
    skill_dir = pathlib.Path(skill_dir)
    if not (skill_dir / "SKILL.md").is_file() or (skill_dir / "SKILL.md").is_symlink():
        raise ValueError(f"skill directory must contain a regular SKILL.md: {skill_dir}")
    if work_dir.exists() or work_dir.is_symlink():
        raise FileExistsError(work_dir)
    candidate_path = test_dir / "candidate.json"
    dockerfile_path = test_dir / "Dockerfile"
    if not candidate_path.is_file() or not dockerfile_path.is_file():
        raise ValueError(f"hidden test lacks candidate.json or Dockerfile: {test_dir}")
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    if _effective_candidate_skill(candidate) != skill_name:
        raise ValueError(
            "hidden candidate skill identity differs from the mounted skill name: "
            f"{_effective_candidate_skill(candidate)!r} != {skill_name!r}"
        )
    for path in test_dir.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"hidden test symlink is forbidden: {path}")
    shutil.copytree(test_dir, work_dir)
    return work_dir


def _invoke_shared_runner(
    case_dir, run_dir, agent_model, wall_clock, checks_dir, skill_dir
):
    case_dir = pathlib.Path(case_dir).resolve()
    run_dir = pathlib.Path(run_dir).resolve()
    checks_dir = pathlib.Path(checks_dir).resolve()
    skill_dir = pathlib.Path(skill_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    allowed_env = (
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "CLOSE_API_KEY",
        "SKILLRACE_LEDGER",
        "DOCKER_HOST",
        "DOCKER_CONFIG",
        "XDG_RUNTIME_DIR",
    )
    clean_env = {name: os.environ[name] for name in allowed_env if name in os.environ}
    runner_argv = [
        sys.executable,
        "-m",
        "skillrace.run_case",
        "--case",
        str(case_dir),
        "--model",
        agent_model,
        "--skill-dir",
        str(skill_dir),
        "--out",
        str(run_dir),
        "--wall-clock",
        str(wall_clock),
    ]
    checker_argv = [
        sys.executable,
        "-m",
        "skillrace.check_properties",
        "--run",
        str(run_dir),
        "--checks",
        str(checks_dir),
        "--verdict-provenance",
        "hidden-independent",
    ]
    atomic_write_json(
        run_dir / "launch.json",
        {
            "schema": "skillrace-hidden-launch/1",
            "commands": [
                {"role": "agent", "cwd": str(run_dir), "argv": runner_argv},
                {"role": "oracle", "cwd": str(run_dir), "argv": checker_argv},
            ],
            "environment": {
                "names": sorted(clean_env),
                "secret_names": sorted(
                    name for name in clean_env if "KEY" in name or "TOKEN" in name
                ),
                "values_recorded": False,
            },
        },
    )
    runner = subprocess.run(
        runner_argv,
        capture_output=True,
        text=True,
        check=False,
        cwd=run_dir,
        env=clean_env,
    )
    checker = subprocess.run(
        checker_argv,
        capture_output=True,
        text=True,
        check=False,
        cwd=run_dir,
        env=clean_env,
    )
    verdict_path = pathlib.Path(run_dir) / "verdicts.json"
    verdicts = json.loads(verdict_path.read_text(encoding="utf-8")) if verdict_path.exists() else []
    manifest_path = pathlib.Path(run_dir) / "run.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    return runner, checker, verdicts, manifest


def run_test(case_dir, run_dir, agent_model, wall_clock, checks_dir, skill_dir):
    """Legacy-compatible wrapper returning verdicts from the shared execution path."""

    _, _, verdicts, _ = _invoke_shared_runner(
        case_dir, run_dir, agent_model, wall_clock, checks_dir, skill_dir
    )
    return verdicts


def raw_execution_artifacts(
    execution_dir: str | pathlib.Path, *, path_prefix: str = "execution"
) -> dict[str, dict[str, str | None]]:
    """Hash the four raw files that ground one hidden-execution result."""

    root = pathlib.Path(execution_dir)
    if root.is_symlink():
        raise ValueError(f"raw hidden execution directory may not be a symlink: {root}")
    records: dict[str, dict[str, str | None]] = {}
    for name, filename in RAW_EXECUTION_ARTIFACTS.items():
        path = root / filename
        if path.is_symlink():
            raise ValueError(f"raw hidden artifact may not be a symlink: {path}")
        records[name] = {
            "path": f"{path_prefix}/{filename}",
            "sha256": file_hash(path) if path.is_file() else None,
        }
    return records


def execute_hidden_request(request: HiddenExecutionRequest) -> dict[str, Any]:
    """Execute one hidden test through the condition-blind runner/checker adapter."""

    request.run_dir.mkdir(parents=True, exist_ok=False)
    case_dir = derive_case(
        request.hidden_case_dir,
        request.skill_name,
        request.skill_dir,
        request.run_dir / "case",
    )
    execution_dir = request.run_dir / "execution"
    runner, checker, verdicts, manifest = _invoke_shared_runner(
        case_dir,
        execution_dir,
        request.agent_model,
        request.wall_clock,
        case_dir / "checks",
        request.skill_dir,
    )
    termination = manifest.get("termination")
    if isinstance(termination, Mapping):
        termination_reason = termination.get("reason")
        wall_seconds = termination.get("seconds", 0.0)
    else:
        termination_reason = termination
        wall_seconds = manifest.get("seconds", 0.0)
    if termination_reason == "timeout" or runner.returncode in {124, 137}:
        status = "timeout"
    elif runner.returncode != 0 or checker.returncode != 0:
        status = "error"
    elif not verdicts or all(row.get("holds") is None for row in verdicts):
        status = "inconclusive"
    else:
        status = "completed"
    cost_path = execution_dir / "cost.json"
    cost = json.loads(cost_path.read_text(encoding="utf-8")) if cost_path.exists() else {}
    return {
        "status": status,
        "verdicts": verdicts,
        "input_tokens": int(cost.get("in", 0) or 0),
        "output_tokens": int(cost.get("out", 0) or 0),
        "cost_usd": float(cost.get("usd", cost.get("price_usd", 0.0)) or 0.0),
        "wall_seconds": float(wall_seconds or 0.0),
        "run_id": manifest.get("run_id"),
        "agent_id": manifest.get("agent_id") or manifest.get("run_id"),
        "launch_hash": file_hash(execution_dir / "launch.json"),
        "runner_returncode": runner.returncode,
        "checker_returncode": checker.returncode,
        "raw_artifacts": raw_execution_artifacts(execution_dir),
    }


def grade_run(
    verdicts: Sequence[Mapping[str, Any]],
    *,
    execution_status: str = "completed",
    expected_criterion_ids: Sequence[str],
) -> dict[str, Any]:
    """Grade one execution against its exact hidden-test oracle contract."""

    if execution_status not in EXECUTION_STATUSES:
        raise ValueError(f"unknown execution status: {execution_status!r}")
    expected = tuple(expected_criterion_ids)
    if (
        not expected
        or any(not isinstance(item, str) or not item for item in expected)
        or len(set(expected)) != len(expected)
    ):
        raise ValueError("expected criterion IDs must be non-empty, unique strings")
    functional = [
        row for row in verdicts if row.get("provenance") == "hidden-independent"
    ]
    fixed = [row for row in verdicts if row.get("provenance") == "fixed"]
    functional_ids = [str(row.get("property_id", "")) for row in functional]
    counts = Counter(functional_ids)
    missing = sorted(set(expected) - set(functional_ids))
    extra = sorted(set(functional_ids) - set(expected))
    duplicates = sorted(identifier for identifier, count in counts.items() if count != 1)
    wrong_provenance = sorted(
        str(row.get("property_id"))
        for row in verdicts
        if row.get("property_id") in set(expected)
        and row.get("provenance") != "hidden-independent"
    )
    untrusted = [
        str(row.get("property_id", "unknown"))
        for row in verdicts
        if row.get("provenance") not in {"fixed", "hidden-independent"}
    ]
    inconclusive = [
        str(row.get("property_id", "unknown"))
        for row in verdicts
        if row.get("holds") is None
    ]
    base = {
        "status": execution_status,
        "functional_pass": None,
        "fixed_clean": None,
        "strict_pass": None,
        "functional_criteria_count": len(functional),
        "expected_criteria_count": len(expected),
        "fixed_criteria_count": len(fixed),
        "inconclusive_criteria": inconclusive,
        "untrusted_criteria": untrusted,
        "missing_criteria": missing,
        "extra_criteria": extra,
        "duplicate_criteria": duplicates,
        "wrong_provenance_criteria": wrong_provenance,
    }
    if execution_status != "completed":
        return base
    if untrusted or missing or extra or duplicates or wrong_provenance:
        base["status"] = "inconclusive"
        return base

    definite_functional_failure = any(
        row.get("holds") is False or row.get("violated") is True for row in functional
    )
    unknown_functional = any(row.get("holds") is None for row in functional)
    if definite_functional_failure:
        functional_pass: bool | None = False
    elif unknown_functional:
        functional_pass = None
        base["status"] = "inconclusive"
    else:
        functional_pass = all(row.get("holds") is True for row in functional)

    definite_fixed_failure = any(
        row.get("violated") is True or row.get("holds") is False for row in fixed
    )
    unknown_fixed = any(row.get("holds") is None for row in fixed)
    if definite_fixed_failure:
        fixed_clean: bool | None = False
    elif unknown_fixed:
        fixed_clean = None
        if functional_pass is not False:
            base["status"] = "inconclusive"
    else:
        fixed_clean = True

    base["functional_pass"] = functional_pass
    base["fixed_clean"] = fixed_clean
    if functional_pass is None or fixed_clean is None:
        base["strict_pass"] = None
    else:
        base["strict_pass"] = functional_pass and fixed_clean
    return base


def summarize_runs(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Use all scheduled tests for headline rates and retain available-case sensitivity."""

    counts = Counter(str(row.get("status", "missing")) for row in rows)
    unknown = set(counts) - set(EXECUTION_STATUSES)
    if unknown:
        raise ValueError(f"unknown run statuses: {sorted(unknown)}")
    functional = [row["functional_pass"] for row in rows if row.get("functional_pass") is not None]
    strict = [row["strict_pass"] for row in rows if row.get("strict_pass") is not None]
    scheduled = len(rows)
    functional_passes = sum(value is True for value in functional)
    strict_passes = sum(value is True for value in strict)
    return {
        "scheduled": scheduled,
        "scored": len(functional),
        "functional_passes": functional_passes,
        "functional_pass_rate": (
            functional_passes / scheduled if scheduled else None
        ),
        "available_case_functional_pass_rate": (
            functional_passes / len(functional) if functional else None
        ),
        "strict_scored": len(strict),
        "strict_passes": strict_passes,
        "strict_pass_rate": (
            strict_passes / scheduled if scheduled else None
        ),
        "available_case_strict_pass_rate": (
            strict_passes / len(strict) if strict else None
        ),
        "status_counts": {status: counts.get(status, 0) for status in EXECUTION_STATUSES},
    }


def _test_order(path: pathlib.Path) -> tuple[int, str]:
    suffix = path.name[1:] if path.name.startswith("t") else ""
    return (int(suffix) if suffix.isdigit() else 2**31 - 1, path.name)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate one frozen RQ3 skill condition on hidden tests"
    )
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--condition", choices=EVALUATION_CONDITIONS, default="zero-shot")
    parser.add_argument("--skill-name", required=True)
    parser.add_argument("--skill-dir", required=True)
    parser.add_argument("--agent-model", default="qwen3.6-flash")
    parser.add_argument("--wall-clock", type=int, default=1200)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    test_dirs = sorted(
        (
            path
            for path in (pathlib.Path(args.scenario) / "tests").iterdir()
            if path.is_dir() and (path / "Dockerfile").is_file()
        ),
        key=_test_order,
    )
    if len(test_dirs) != 10:
        raise SystemExit(f"expected exactly 10 hidden tests, found {len(test_dirs)}")

    results = []
    for test in test_dirs:
        contract = load_test(test)
        request = HiddenExecutionRequest(
            test_id=f"{pathlib.Path(args.scenario).name}/{test.name}",
            hidden_case_dir=test,
            skill_name=args.skill_name,
            skill_dir=pathlib.Path(args.skill_dir),
            run_dir=out / "runs" / test.name,
            agent_model=args.agent_model,
            wall_clock=args.wall_clock,
            contract_identity=contract.contract_identity_sha256,
            criterion_ids=tuple(criterion.id for criterion in contract.criteria),
            validation_image_digest=str(contract.evidence.payload.get("image_digest")),
        )
        raw = execute_hidden_request(request)
        grade = grade_run(
            raw.get("verdicts", []),
            execution_status=raw["status"],
            expected_criterion_ids=request.criterion_ids,
        )
        results.append({"test_id": request.test_id, **grade, **{
            key: raw.get(key) for key in ("input_tokens", "output_tokens", "cost_usd", "wall_seconds", "run_id")
        }})
        print(f"  {test.name}: {grade['status']} / functional={grade['functional_pass']}")
    summary = {
        "schema": "skillrace-hidden-evaluation/1",
        "scenario": pathlib.Path(args.scenario).name,
        "condition": args.condition,
        "agent_model": args.agent_model,
        "one_execution_per_test": True,
        "summary": summarize_runs(results),
        "results": results,
    }
    atomic_write_json(out / "skill_eval.json", summary)


if __name__ == "__main__":
    main()
