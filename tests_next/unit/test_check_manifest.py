import json
from pathlib import Path
from typing import Any, Callable

import pytest

from skillrace_next.verification.codex import validate_check_manifest


NL_CHECKS = [
    {"property_id": "P1", "description": "result.txt exists"},
    {"property_id": "P2", "description": "result.txt contains ok"},
]


def valid_manifest(tmp_path: Path) -> Path:
    output = tmp_path / "output"
    checks = output / "checks"
    checks.mkdir(parents=True)
    (checks / "P1-C1.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    manifest = output / "check_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "skillrace-check-bundle/1",
                "run_id": "run-1",
                "artifact_hash": "artifact-hash",
                "checks": [
                    {
                        "check_id": "P1-C1",
                        "property_id": "P1",
                        "script": "checks/P1-C1.py",
                        "argv": [
                            "python3",
                            "/tmp/skillrace-checks/checks/P1-C1.py",
                            "/workspace",
                        ],
                        "timeout_seconds": 60,
                        "purpose": "Observe whether result.txt exists",
                        "pass_condition": "The file exists",
                        "failure_condition": "The file is absent",
                        "root_cause_category": "format_contract",
                    }
                ],
                "uncovered": [
                    {
                        "property_id": "P2",
                        "reason": "This fixture intentionally leaves P2 uncovered",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_validate_check_manifest_binds_scripts_and_input_hashes(tmp_path: Path) -> None:
    path = valid_manifest(tmp_path)

    bundle = validate_check_manifest(path, NL_CHECKS, "artifact-hash")

    assert bundle.run_id == "run-1"
    assert bundle.artifact_hash == "artifact-hash"
    assert bundle.manifest_path == path
    assert bundle.script_paths == (path.parent / "checks" / "P1-C1.py",)
    assert bundle.input_hashes["artifact"] == "artifact-hash"


def missing_coverage(path: Path, value: dict[str, Any]) -> None:
    value["uncovered"] = []


def escaping_script(path: Path, value: dict[str, Any]) -> None:
    value["checks"][0]["script"] = "../escape.py"


def non_list_argv(path: Path, value: dict[str, Any]) -> None:
    value["checks"][0]["argv"] = "python3 check.py"


def argv_omits_declared_script(path: Path, value: dict[str, Any]) -> None:
    value["checks"][0]["argv"] = ["/workspace"]


def zero_timeout(path: Path, value: dict[str, Any]) -> None:
    value["checks"][0]["timeout_seconds"] = 0


def excessive_timeout(path: Path, value: dict[str, Any]) -> None:
    value["checks"][0]["timeout_seconds"] = 61


def unknown_category(path: Path, value: dict[str, Any]) -> None:
    value["checks"][0]["root_cause_category"] = "invented"


def undeclared_script(path: Path, value: dict[str, Any]) -> None:
    (path.parent / "checks" / "extra.py").write_text(
        "raise SystemExit(0)\n", encoding="utf-8"
    )


def wrong_artifact_hash(path: Path, value: dict[str, Any]) -> None:
    value["artifact_hash"] = "other-hash"


@pytest.mark.parametrize(
    "invalidator",
    [
        missing_coverage,
        escaping_script,
        non_list_argv,
        argv_omits_declared_script,
        zero_timeout,
        excessive_timeout,
        unknown_category,
        undeclared_script,
        wrong_artifact_hash,
    ],
    ids=(
        "missing-property-coverage",
        "escaping-script",
        "non-list-argv",
        "argv-omits-declared-script",
        "zero-timeout",
        "excessive-timeout",
        "unknown-category",
        "undeclared-script",
        "artifact-hash-mismatch",
    ),
)
def test_validate_check_manifest_rejects_invalid_bundle(
    tmp_path: Path,
    invalidator: Callable[[Path, dict[str, Any]], None],
) -> None:
    path = valid_manifest(tmp_path)
    value = load_manifest(path)
    invalidator(path, value)
    save_manifest(path, value)

    with pytest.raises(ValueError):
        validate_check_manifest(path, NL_CHECKS, "artifact-hash")
