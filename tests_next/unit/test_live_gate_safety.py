from pathlib import Path
import subprocess

import pytest

from tests_next.live import test_dual_model_gate_live as gate


def test_failed_slice_returns_evidence_path_and_redacts_provider_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "lab-unit-secret-value"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LAB_KEY_UNLIMITED", secret)

    def failed_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        created = (
            tmp_path
            / "out"
            / "live-contracts"
            / "part1"
            / "deepseek-v4-flash"
            / "created-run"
        )
        created.mkdir(parents=True)
        return subprocess.CompletedProcess(
            args[0],
            1,
            stdout=f"provider stdout contained {secret}",
            stderr=f"provider stderr contained {secret}",
        )

    monkeypatch.setattr(gate.subprocess, "run", failed_run)
    evidence = tmp_path / "gate"
    evidence.mkdir()

    created, status = gate.run_slice("part1", "deepseek-v4-flash", evidence)

    assert created.name == "created-run"
    assert status == 1
    assert secret not in (evidence / "part1.stdout.txt").read_text(encoding="utf-8")
    assert secret not in (evidence / "part1.stderr.txt").read_text(encoding="utf-8")
