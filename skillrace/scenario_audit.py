"""Run positive and negative hidden-oracle validation without an agent/model."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import pathlib
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from .io_utils import atomic_write_json
from .scenario_contract import HiddenTest, load_test


@dataclasses.dataclass(frozen=True)
class MatrixGrade:
    reference_passed: bool
    starting_rejected: bool
    negative_oracles_passed: bool
    survivors: tuple[tuple[str, str], ...]


def evidence_state_for_grade(grade: MatrixGrade) -> str:
    """Never label a partially failing runtime matrix as validated evidence."""
    return (
        "validated"
        if grade.reference_passed
        and grade.starting_rejected
        and grade.negative_oracles_passed
        else "audit-failed"
    )


def grade_oracle_matrix(
    *,
    criteria: Sequence[str],
    reference: Mapping[str, int],
    starting: Mapping[str, int],
    negatives: Mapping[str, Mapping[str, int]],
    assignments: Mapping[str, Sequence[str]],
) -> MatrixGrade:
    criterion_ids = tuple(criteria)
    reference_passed = all(reference.get(criterion) == 0 for criterion in criterion_ids)
    starting_rejected = any(starting.get(criterion) != 0 for criterion in criterion_ids)
    survivors = tuple(
        sorted(
            (negative_id, criterion)
            for criterion in criterion_ids
            for negative_id in assignments.get(criterion, ())
            if negatives.get(negative_id, {}).get(criterion) == 0
        )
    )
    return MatrixGrade(
        reference_passed=reference_passed,
        starting_rejected=starting_rejected,
        negative_oracles_passed=not survivors,
        survivors=survivors,
    )


def _run(command: Sequence[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), text=True, capture_output=True, check=False, timeout=timeout
    )


def _docker_version() -> str:
    result = _run(["docker", "version", "--format", "{{.Server.Version}}"])
    if result.returncode:
        raise RuntimeError(f"Docker unavailable: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


def _last_line(text: str) -> str:
    lines = text.rstrip().splitlines()
    return lines[-1][-1000:] if lines else ""


def overlay_delete_paths(overlay: pathlib.Path) -> tuple[str, ...]:
    """Return confined workspace-relative tombstones declared by a mutant."""
    directive = overlay / ".skillrace-delete"
    if not directive.exists():
        return ()
    paths: list[str] = []
    for raw in directive.read_text(encoding="utf-8").splitlines():
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        relative = pathlib.PurePosixPath(value)
        if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
            raise ValueError(f"unsafe delete path in {directive}: {value!r}")
        paths.append(relative.as_posix())
    return tuple(paths)


def _prepare_variant_container(
    contract: HiddenTest,
    image: str,
    overlay: pathlib.Path | None,
    label: str,
    containers: list[str],
) -> str:
    container = f"skillrace-oracle-{uuid.uuid4().hex[:16]}"
    created = _run(
        [
            "docker",
            "create",
            "--name",
            container,
            "--entrypoint",
            "tail",
            image,
            "-f",
            "/dev/null",
        ]
    )
    if created.returncode:
        raise RuntimeError(f"cannot create audit container: {(created.stderr or created.stdout).strip()}")
    containers.append(container)
    started = _run(["docker", "start", container])
    if started.returncode:
        raise RuntimeError(f"cannot start audit container: {(started.stderr or started.stdout).strip()}")
    prepared = _run(["docker", "exec", container, "mkdir", "-p", "/check/oracle", "/workspace"])
    if prepared.returncode:
        raise RuntimeError(f"cannot prepare audit paths: {(prepared.stderr or prepared.stdout).strip()}")
    checks = contract.root / "checks"
    copied_checks = _run(["docker", "cp", f"{checks}/.", f"{container}:/check/oracle/"])
    if copied_checks.returncode:
        raise RuntimeError(f"cannot stage checks: {(copied_checks.stderr or copied_checks.stdout).strip()}")
    if overlay is not None:
        copied = _run(["docker", "cp", f"{overlay}/.", f"{container}:/workspace/"])
        if copied.returncode:
            raise RuntimeError(f"cannot stage {label}: {(copied.stderr or copied.stdout).strip()}")
        for relative in overlay_delete_paths(overlay):
            deleted = _run(["docker", "exec", container, "rm", "-f", f"/workspace/{relative}"])
            if deleted.returncode:
                raise RuntimeError(
                    f"cannot apply delete directive for {label}: "
                    f"{(deleted.stderr or deleted.stdout).strip()}"
                )
    return container


def _run_variant(
    contract: HiddenTest,
    image: str,
    overlay: pathlib.Path | None,
    label: str,
    containers: list[str],
) -> tuple[dict[str, int], dict[str, Any]]:
    """Run every criterion from the same variant in an independent container."""

    statuses: dict[str, int] = {}
    details: dict[str, Any] = {}
    for criterion in contract.criteria:
        container = _prepare_variant_container(
            contract, image, overlay, label, containers
        )
        started_at = time.monotonic()
        result = _run(
            ["docker", "exec", container, "bash", f"/check/oracle/{criterion.script.name}"],
            timeout=120,
        )
        duration = time.monotonic() - started_at
        statuses[criterion.id] = result.returncode
        details[criterion.id] = {
            "command": ["bash", f"/check/oracle/{criterion.script.name}"],
            "exit_code": result.returncode,
            "final_output_line": _last_line(result.stdout + result.stderr),
            "duration_seconds": round(duration, 6),
            "script_sha256": criterion.script_sha256,
            "isolation": "fresh-container-per-criterion",
        }
    return statuses, details


def audit_test(test_dir: str | pathlib.Path, *, persist: bool = False) -> dict[str, Any]:
    # The audit is the only repair path for stale runtime evidence. Structural hashes
    # remain mandatory, but the prior execution-isolation policy cannot gate its own
    # replacement.
    contract = load_test(test_dir, require_fresh_evidence=False)
    docker_version = _docker_version()
    image = f"skillrace-oracle-{uuid.uuid4().hex[:16]}"
    containers: list[str] = []
    image_created = False
    try:
        built = _run(["docker", "build", "-t", image, "-f", str(contract.root / "Dockerfile"), str(contract.root)], timeout=600)
        if built.returncode:
            raise RuntimeError(f"audit image build failed: {(built.stderr or built.stdout)[-4000:]}")
        image_created = True
        inspected = _run(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
        if inspected.returncode:
            raise RuntimeError(f"cannot inspect audit image: {inspected.stderr.strip()}")
        image_digest = inspected.stdout.strip()
        starting, starting_details = _run_variant(contract, image, None, "starting", containers)
        reference, reference_details = _run_variant(
            contract, image, contract.reference_overlay, "reference", containers
        )
        negatives: dict[str, dict[str, int]] = {}
        negative_details: dict[str, Any] = {}
        for negative in contract.negative_implementations:
            statuses, details = _run_variant(
                contract, image, negative.overlay, negative.id, containers
            )
            negatives[negative.id] = statuses
            negative_details[negative.id] = {
                "overlay_sha256": negative.overlay_sha256,
                "criteria": details,
            }
        assignments = {
            criterion.id: criterion.negative_ids for criterion in contract.criteria
        }
        grade = grade_oracle_matrix(
            criteria=tuple(assignments),
            reference=reference,
            starting=starting,
            negatives=negatives,
            assignments=assignments,
        )
        for negative_id, details in negative_details.items():
            assigned = [
                criterion.id
                for criterion in contract.criteria
                if negative_id in criterion.negative_ids
            ]
            details["assigned_criteria"] = assigned
            details["killed_assigned"] = all(
                negatives[negative_id].get(criterion_id) != 0
                for criterion_id in assigned
            )
        state = evidence_state_for_grade(grade)
        report: dict[str, Any] = {
            "schema": "skillrace-oracle-evidence/1",
            "test_id": contract.test_id,
            "state": state,
            "contract_identity_sha256": contract.contract_identity_sha256,
            "reason": (
                "Docker reference and negative oracle audit passed"
                if state == "validated"
                else "Docker reference or negative oracle audit failed"
            ),
            "validated_at": dt.datetime.now(dt.UTC).isoformat(),
            "image_digest": image_digest,
            "docker_version": docker_version,
            "reference": {
                "passed": grade.reference_passed,
                "overlay_sha256": contract.reference_sha256,
                "criteria": reference_details,
            },
            "starting": {
                "rejected": grade.starting_rejected,
                "criteria": starting_details,
            },
            "negative_implementations": negative_details,
            "reference_passed": grade.reference_passed,
            "starting_rejected": grade.starting_rejected,
            "negative_oracles_passed": grade.negative_oracles_passed,
            "survivors": [list(item) for item in grade.survivors],
        }
        if persist:
            atomic_write_json(contract.evidence.path, report)
        return report
    finally:
        for container in reversed(containers):
            _run(["docker", "rm", "-f", container])
        if image_created:
            _run(["docker", "image", "rm", "-f", image])


def _selected_tests(arguments: argparse.Namespace) -> list[pathlib.Path]:
    if arguments.test:
        return [arguments.test]
    if arguments.scenario:
        return sorted(arguments.scenario.glob("tests/t*"))
    return sorted(arguments.root.glob("*/tests/t*"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--test", type=pathlib.Path)
    group.add_argument("--scenario", type=pathlib.Path)
    group.add_argument("--root", type=pathlib.Path)
    parser.add_argument("--persist", action="store_true")
    arguments = parser.parse_args(argv)
    reports = [audit_test(path, persist=arguments.persist) for path in _selected_tests(arguments)]
    print(json.dumps(reports, indent=2))
    return int(
        any(
            not report["reference_passed"]
            or not report["starting_rejected"]
            or not report["negative_oracles_passed"]
            for report in reports
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
