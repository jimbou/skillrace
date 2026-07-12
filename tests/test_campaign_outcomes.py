import json

import pytest

import skillrace.loop as campaign_loop
from skillrace.loop import classify_oracle_result, classify_runner_result


def test_pre_agent_build_failure_does_not_consume_execution():
    assert classify_runner_result(returncode=2, manifest=None) == {
        "agent_started": False,
        "consume_budget": False,
        "status": "infrastructure_error",
    }


def test_agent_error_consumes_execution():
    manifest = {"agent_started": True, "termination": {"reason": "error", "rc": 7}}
    assert classify_runner_result(returncode=7, manifest=manifest) == {
        "agent_started": True,
        "consume_budget": True,
        "status": "agent_error",
    }


def test_timeout_consumes_execution():
    manifest = {
        "agent_started": True,
        "termination": {"reason": "timeout", "rc": 124},
    }
    assert classify_runner_result(returncode=124, manifest=manifest) == {
        "agent_started": True,
        "consume_budget": True,
        "status": "timeout",
    }


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        ([{"holds": True}, {"holds": False}], "completed"),
        ([{"holds": None}], "inconclusive"),
        ([{"holds": True}, {"holds": None}], "partially_inconclusive"),
    ],
)
def test_oracle_status_distinguishes_inconclusive_verdicts(verdicts, expected):
    assert classify_oracle_result(0, verdicts) == expected


def test_oracle_nonzero_or_missing_status_is_error():
    assert classify_oracle_result(2, []) == "error"
    assert classify_oracle_result(None, []) == "error"


def test_checker_is_not_spawned_without_run_manifest(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("checker subprocess must not be called")

    monkeypatch.setattr(campaign_loop.subprocess, "run", unexpected)

    verdicts, tail, returncode = campaign_loop.check_run(tmp_path, "model")

    assert verdicts == []
    assert returncode is None
    assert "run.json" in " ".join(tail)


class _FakeRandom:
    def __init__(self, *args, **kwargs):
        self.n = 0
        self.cost_usd = 0.0
        self.folded = []

    def propose(self):
        self.n += 1
        return {
            "candidate_id": f"candidate-{self.n}",
            "skill": "demo",
            "prompt": "repair the demo",
            "base_image": "demo:base",
            "containerfile": "FROM demo:base\n",
            "provenance": {"source": "random"},
        }

    def fold(self, candidate, run_dir, phase="explore", attempt_id=None):
        self.folded.append((candidate["candidate_id"], str(run_dir)))
        return None

    def snapshot(self):
        return {
            "source": "fake", "n": self.n, "folded": list(self.folded)
        }

    def restore(self, snapshot):
        self.n = snapshot["n"]
        self.folded = [tuple(item) for item in snapshot["folded"]]

    def state(self):
        return {"source": "fake", "proposals": self.n}


def _write_skill(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    properties = [
        {"id": "p1", "nl": "must work", "reads": "state"},
        {"id": "p2", "nl": "must verify", "reads": "trace"},
    ]
    (skill_dir / "properties.json").write_text(json.dumps(properties))
    (skill_dir / "applicability.json").write_text(
        json.dumps(
            {
                "skill": "demo",
                "property_ids": ["p2"],
                "fixed_invariants": ["fixed-no-force-push"],
                "sbe_categories": ["process-hygiene"],
                "contingency": "medium",
            }
        )
    )
    return skill_dir


def test_campaign_retries_pre_agent_failure_and_records_separate_statuses(
    tmp_path, monkeypatch
):
    skill_dir = _write_skill(tmp_path)
    compile_calls = []
    runner_calls = []
    checker_calls = []

    def fake_compile(case_dir, properties, model, image=None, applicability=None):
        compile_calls.append((properties, applicability))
        if len(compile_calls) == 1:
            raise RuntimeError("compile unavailable")
        return {"checks": [], "applicability": applicability}, 0.0

    def fake_runner(case_dir, run_dir, model, wall_clock, trusted_skill_dir):
        runner_calls.append(str(run_dir))
        manifest = {
            "agent_started": True,
            "termination": {"reason": "error", "rc": 7},
        }
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps(manifest))
        return 7, "agent failed", manifest

    def fake_checker(run_dir, model):
        checker_calls.append(str(run_dir))
        return (
            [
                {
                    "property_id": "fixed-no-force-push",
                    "holds": True,
                    "violated": False,
                }
            ],
            [],
            0,
        )

    monkeypatch.setattr(campaign_loop, "RandomGenerator", _FakeRandom)
    monkeypatch.setattr(
        campaign_loop,
        "resolve_base_image_identity",
        lambda image, resolver=None: "sha256:" + "a" * 64,
    )
    monkeypatch.setattr(
        campaign_loop,
        "run_candidate_sanity",
        lambda image, spec: {
            "schema": "candidate-sanity/1",
            "valid": True,
            "rejection": None,
            "checks": [],
        },
    )
    monkeypatch.setattr(campaign_loop, "compile_case", fake_compile)
    monkeypatch.setattr(
        campaign_loop, "verify_runtime_integrity", lambda *a, **k: {"runtime": "ok"}
    )
    monkeypatch.setattr(campaign_loop, "run_agent", fake_runner)
    monkeypatch.setattr(campaign_loop, "check_run", fake_checker)

    campaign = campaign_loop.run_campaign(
        "random",
        "demo",
        skill_dir,
        "demo:base",
        skill_dir / "properties.json",
        budget=1,
        seed_count=0,
        out_dir=tmp_path / "out",
        max_pre_agent_attempts=3,
        development_only=True,
    )

    assert len(campaign["attempts"]) == 2
    assert campaign["complete"] is True
    failed, counted = campaign["attempts"]
    assert failed["attempt_id"] == "e0000-a00"
    assert failed["generation_status"] == "generated"
    assert failed["infrastructure_status"] == "compile_error"
    assert failed["runner_status"] == "not_started"
    assert failed["oracle_status"] == "not_run"
    assert failed["agent_started"] is False
    assert failed["consume_budget"] is False

    assert counted["attempt_id"] == "e0000-a01"
    assert counted["generation_status"] == "generated"
    assert counted["infrastructure_status"] == "ready"
    assert counted["runner_status"] == "agent_error"
    assert counted["oracle_status"] == "completed"
    assert counted["agent_started"] is True
    assert counted["consume_budget"] is True
    assert len(campaign["iterations"]) == 1
    assert campaign["iterations"][0] == counted
    assert len(runner_calls) == 1
    assert len(checker_calls) == 1
    assert all(call[0] == [{"id": "p2", "nl": "must verify", "reads": "trace"}]
               for call in compile_calls)
    assert all(
        call[1]
        == {
            "property_ids": ["p2"],
            "fixed_invariants": ["fixed-no-force-push"],
            "categories": ["process-hygiene"],
            "contingency": "medium",
        }
        for call in compile_calls
    )


def test_campaign_stops_after_finite_pre_agent_attempt_cap(tmp_path, monkeypatch):
    skill_dir = _write_skill(tmp_path)
    runner_calls = []

    monkeypatch.setattr(campaign_loop, "RandomGenerator", _FakeRandom)
    monkeypatch.setattr(
        campaign_loop,
        "resolve_base_image_identity",
        lambda image, resolver=None: "sha256:" + "a" * 64,
    )
    monkeypatch.setattr(
        campaign_loop,
        "run_candidate_sanity",
        lambda image, spec: {
            "schema": "candidate-sanity/1",
            "valid": True,
            "rejection": None,
            "checks": [],
        },
    )
    monkeypatch.setattr(
        campaign_loop,
        "compile_case",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("still down")),
    )
    monkeypatch.setattr(
        campaign_loop, "verify_runtime_integrity", lambda *a, **k: {"runtime": "ok"}
    )
    monkeypatch.setattr(
        campaign_loop,
        "run_agent",
        lambda *args, **kwargs: runner_calls.append(args),
    )

    campaign = campaign_loop.run_campaign(
        "random",
        "demo",
        skill_dir,
        "demo:base",
        skill_dir / "properties.json",
        budget=1,
        seed_count=0,
        out_dir=tmp_path / "out",
        max_pre_agent_attempts=3,
        development_only=True,
    )

    assert len(campaign["attempts"]) == 3
    assert campaign["iterations"] == []
    assert campaign["status"] == "aborted_pre_agent_attempt_cap"
    assert campaign["complete"] is False
    assert campaign["consecutive_pre_agent_failures"] == 3
    assert runner_calls == []
