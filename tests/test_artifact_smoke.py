from __future__ import annotations

import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts" / "artifact_smoke.sh"
REQUIREMENTS = ROOT / "REQUIREMENTS.md"
STATUS = ROOT / "STATUS.md"


def test_artifact_smoke_is_offline_and_covers_the_claim_boundaries():
    assert SMOKE.is_file()
    text = SMOKE.read_text(encoding="utf-8")

    assert "test_campaign_protocol.py" in text
    assert "test_baseline_information_boundaries.py" in text
    assert "test_campaign_parallel_engine.py" in text
    assert "test_experiment_driver.py" in text
    assert "test_rq3_leakage.py" in text
    assert "test_closeai_journal.py" in text
    assert "test_provider_evidence.py" in text
    assert "test_schedules.py" in text
    assert "test_development_pilot_schedule.py" in text
    assert "test_artifact_freeze.py" in text
    assert "skillrace.d1_audit" in text
    assert "skillrace.scenario_contract" in text
    assert "--require-runtime-evidence" in text

    forbidden = ("yunwu_key=", "--live", "pytest.mark.live", "curl ", "wget ")
    assert not any(token in text for token in forbidden)


def test_artifact_smoke_has_valid_bash_syntax():
    completed = subprocess.run(
        ["bash", "-n", str(SMOKE)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_artifact_boundary_documents_are_explicit():
    requirements = REQUIREMENTS.read_text(encoding="utf-8")
    status = STATUS.read_text(encoding="utf-8")

    for token in ("Python 3.12", "Docker", "yunwu_key", "glm-4.5-flash"):
        assert token in requirements
    assert "scripts/artifact_smoke.sh" in requirements
    assert "does not" in requirements and "API" in requirements

    for token in (
        "30",
        "10 scenarios",
        "100 hidden tests",
        "draft",
        "deepseek-v4-flash",
    ):
        assert token in status
    assert "No headline" in status
