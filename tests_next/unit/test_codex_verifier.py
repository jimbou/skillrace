import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

import pytest

from skillrace_next.verification import codex
from skillrace_next.verification.codex import author_checks
from skillrace_next.storage import tree_hash
from tests_next.unit.test_test_cases import config_for


def verifier_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "verifier_workspace"
    input_dir = workspace / "input"
    output = workspace / "output"
    (input_dir / "skill").mkdir(parents=True)
    (input_dir / "environment").mkdir()
    (input_dir / "artifact").mkdir()
    output.mkdir()
    shutil.copy2("skillrace_next/verification/GUIDE.md", workspace / "GUIDE.md")
    (input_dir / "skill" / "SKILL.md").write_text("# Fixture skill\n", encoding="utf-8")
    (input_dir / "prompt.txt").write_text("Create result.txt.\n", encoding="utf-8")
    (input_dir / "environment" / "Dockerfile").write_text(
        "FROM scratch\n", encoding="utf-8"
    )
    (input_dir / "artifact" / "result.txt").write_text("ok\n", encoding="utf-8")
    (input_dir / "trace.jsonl").write_text("{}\n", encoding="utf-8")
    (input_dir / "tool_outputs.jsonl").write_text("{}\n", encoding="utf-8")
    (input_dir / "run.json").write_text(
        json.dumps({"run_id": "run-1"}), encoding="utf-8"
    )
    (input_dir / "nl_checks.json").write_text(
        json.dumps(
            [
                {"property_id": "P1", "description": "result.txt exists"},
                {"property_id": "P2", "description": "result.txt contains ok"},
            ]
        ),
        encoding="utf-8",
    )
    return workspace


def write_valid_bundle(output: Path, artifact_hash: str) -> None:
    checks = output / "checks"
    checks.mkdir(exist_ok=True)
    (checks / "P1-C1.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    (checks / "P2-C1.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    value = {
        "schema": "skillrace-check-bundle/1",
        "run_id": "run-1",
        "artifact_hash": artifact_hash,
        "checks": [
            {
                "check_id": f"{property_id}-C1",
                "property_id": property_id,
                "script": f"checks/{property_id}-C1.py",
                "argv": [
                    "python3",
                    f"/tmp/skillrace-checks/checks/{property_id}-C1.py",
                    "/workspace",
                ],
                "timeout_seconds": 60,
                "purpose": f"Check {property_id}",
                "pass_condition": "The required observation holds",
                "failure_condition": "The required observation does not hold",
                "root_cause_category": "format_contract",
            }
            for property_id in ("P1", "P2")
        ],
        "uncovered": [],
    }
    (output / "check_manifest.json").write_text(json.dumps(value), encoding="utf-8")


def test_guide_states_immutable_artifact_and_authoritative_execution_contract() -> None:
    guide = Path("skillrace_next/verification/GUIDE.md").read_text(encoding="utf-8")
    normalized = guide.lower()
    normalized_words = " ".join(guide.split())

    assert "must not modify, repair, complete, reformat" in guide
    assert "only writable directory" in guide
    assert "local exploratory commands" in normalized
    assert "not verdicts" in normalized
    assert "mark it uncovered" in guide
    assert "no Docker access" in guide
    assert "does not meaningfully exercise the supplied skill" in normalized_words
    assert "observed values satisfy the declared pass condition" in normalized_words
    assert "mutually inconsistent requirements" in normalized_words
    assert "must not import or call an artifact function" in normalized_words
    assert "signature is explicitly required by the prompt" in normalized_words
    assert "exit status `2`" in normalized_words


def test_docker_command_detection_distinguishes_dockerfile_from_cli_invocation() -> None:
    assert not codex.command_invokes_docker(
        "sed -n '1,120p' ../input/environment/Dockerfile"
    )
    assert not codex.command_invokes_docker("rg docker ../GUIDE.md")
    assert codex.command_invokes_docker("docker ps")
    assert codex.command_invokes_docker('/bin/bash -lc "docker exec fixture true"')


def test_author_checks_invokes_isolated_codex_and_changes_only_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = verifier_workspace(tmp_path)
    input_hash = tree_hash(workspace / "input")
    captured: dict[str, Any] = {}
    monkeypatch.setenv("yunwu_key", "unit-secret")
    monkeypatch.setenv("LAB_KEY_UNLIMITED", "lab-unit-secret")

    def fake_codex(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        output = Path(kwargs["cwd"])
        write_valid_bundle(output, tree_hash(workspace / "input" / "artifact"))
        return subprocess.CompletedProcess(
            command, 0, '{"type":"turn.completed"}\n', ""
        )

    bundle = author_checks(workspace, config_for(tmp_path), fake_codex)

    command = captured["command"]
    assert command[:2] == ["codex", "exec"]
    assert command[command.index("--model") + 1] == "gpt-5.6-terra"
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert "--json" in command
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--skip-git-repo-check" in command
    assert captured["kwargs"]["cwd"] == workspace / "output"
    assert captured["kwargs"]["env"]["DOCKER_HOST"].endswith("nonexistent.sock")
    assert "yunwu_key" not in captured["kwargs"]["env"]
    assert "LAB_KEY_UNLIMITED" not in captured["kwargs"]["env"]
    assert tree_hash(workspace / "input") == input_hash
    assert bundle.manifest_path.is_file()
    assert bundle.codex_receipt_path.is_file()


def test_author_checks_allows_one_structural_correction(
    tmp_path: Path,
) -> None:
    workspace = verifier_workspace(tmp_path)
    calls = 0

    def correcting_codex(
        command: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        output = Path(kwargs["cwd"])
        write_valid_bundle(output, tree_hash(workspace / "input" / "artifact"))
        if calls == 1:
            manifest = json.loads(
                (output / "check_manifest.json").read_text(encoding="utf-8")
            )
            manifest["checks"] = manifest["checks"][:1]
            (output / "checks" / "P2-C1.py").unlink()
            (output / "check_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
        return subprocess.CompletedProcess(command, 0, "{}\n", "")

    bundle = author_checks(workspace, config_for(tmp_path), correcting_codex)

    assert calls == 2
    assert len(bundle.script_paths) == 2


def test_author_checks_rejects_any_input_mutation(tmp_path: Path) -> None:
    workspace = verifier_workspace(tmp_path)

    def mutating_codex(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        artifact = workspace / "input" / "artifact" / "result.txt"
        artifact.chmod(0o644)
        artifact.write_text("mutated\n", encoding="utf-8")
        write_valid_bundle(Path(kwargs["cwd"]), tree_hash(workspace / "input" / "artifact"))
        return subprocess.CompletedProcess(command, 0, "{}\n", "")

    with pytest.raises(RuntimeError, match="mutated verifier input"):
        author_checks(workspace, config_for(tmp_path), mutating_codex)
