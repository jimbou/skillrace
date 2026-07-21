import json
from pathlib import Path

import pytest

from skillrace_next.runtime.docker import (
    CleanupResult,
    ExecResult,
    RunningContainer,
)
from skillrace_next.storage import tree_hash
from skillrace_next.verification import executor
from skillrace_next.verification.codex import validate_check_manifest
from skillrace_next.verification.executor import interpret_checker_result


def execution(
    exit_code: int | None,
    stdout: str,
    *,
    timed_out: bool = False,
) -> ExecResult:
    return ExecResult(
        argv=("python3", "check.py", "/workspace"),
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
        duration_seconds=0.25,
        timed_out=timed_out,
    )


def declared_check() -> dict[str, object]:
    return {
        "check_id": "P1-C1",
        "property_id": "P1",
    }


@pytest.mark.parametrize(
    ("exit_code", "expected_status"),
    [(0, "pass"), (1, "fail"), (2, "inconclusive")],
)
def test_checker_exit_status_maps_only_after_valid_json(
    exit_code: int, expected_status: str
) -> None:
    result = interpret_checker_result(
        declared_check(),
        execution(
            exit_code,
            '{"diagnostic":"observed fixture","evidence_paths":["result.txt"]}\n',
        ),
        Path("outputs/P1-C1.stdout"),
        Path("outputs/P1-C1.stderr"),
    )

    assert result["status"] == expected_status
    assert result["exit_code"] == exit_code
    assert result["diagnostic"] == "observed fixture"
    assert result["evidence_paths"] == ["result.txt"]


def test_timeout_is_inconclusive() -> None:
    result = interpret_checker_result(
        declared_check(),
        execution(None, "", timed_out=True),
        Path("outputs/P1-C1.stdout"),
        Path("outputs/P1-C1.stderr"),
    )

    assert result["status"] == "inconclusive"
    assert "timed out" in str(result["diagnostic"])


@pytest.mark.parametrize(
    ("exit_code", "stdout"),
    [
        (1, "not-json\n"),
        (1, '{"diagnostic":"missing evidence paths"}\n'),
        (9, '{"diagnostic":"unexpected exit","evidence_paths":[]}\n'),
    ],
)
def test_invalid_checker_outcome_is_never_a_property_failure(
    exit_code: int, stdout: str
) -> None:
    result = interpret_checker_result(
        declared_check(),
        execution(exit_code, stdout),
        Path("outputs/P1-C1.stdout"),
        Path("outputs/P1-C1.stderr"),
    )

    assert result["status"] == "inconclusive"


def test_explicitly_uncovered_properties_create_no_checker_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "result.txt").write_text("ok\n", encoding="utf-8")
    output = tmp_path / "bundle"
    output.mkdir()
    manifest = output / "check_manifest.json"
    nl_checks = [
        {"property_id": "P1", "description": "A trace-only observation."}
    ]
    manifest.write_text(
        json.dumps(
            {
                "schema": "skillrace-check-bundle/1",
                "run_id": "run-uncovered",
                "artifact_hash": tree_hash(artifact),
                "checks": [],
                "uncovered": [
                    {
                        "property_id": "P1",
                        "reason": "The supplied trace has no observable event for P1.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    bundle = validate_check_manifest(manifest, nl_checks, tree_hash(artifact))
    monkeypatch.setattr(executor, "_docker_setup", lambda *args: None)
    monkeypatch.setattr(
        executor,
        "remove_container",
        lambda container: CleanupResult(True, True, ""),
    )

    results = executor.execute_checks(
        RunningContainer("container-1", "run-uncovered", "sha256:image"),
        artifact,
        bundle,
        tmp_path / "results",
    )

    assert not results.results
