from __future__ import annotations

import json
import pathlib

import pytest

import skillrace.loop as campaign_loop
from skillrace.runtime_trust import RuntimeFingerprintError, RuntimeIntegrityError
from skillrace.sanity import SanityInfrastructureError


SANITY = {
    "required_paths": ["/workspace"],
    "required_tools": ["bash"],
    "task_probe": {"command": "true", "allowed_exit_codes": [0]},
    "unsolved_check": None,
}


def write_skill(root):
    skill_dir = root / "skill"
    skill_dir.mkdir()
    (skill_dir / "properties.json").write_text(
        json.dumps([{"id": "p1", "nl": "works", "reads": "state"}])
    )
    (skill_dir / "applicability.json").write_text(
        json.dumps(
            {
                "skill": "demo",
                "property_ids": ["p1"],
                "fixed_invariants": [],
                "sbe_categories": [],
                "contingency": "low",
            }
        )
    )
    return skill_dir


def candidate(source, index):
    return {
        "candidate_id": f"{source}-{index}",
        "skill": "demo",
        "prompt": "do the task",
        "base_image": "demo:base",
        "containerfile": "FROM demo:base\n",
        "built_image": f"demo:{source}-{index}",
        "sanity": SANITY,
        "provenance": {
            "source": source,
            "summary": f"case {index}",
            "task_nl": "do the task",
            "env_nl": "unsolved project",
        },
    }


class FakeRandom:
    instances = []

    def __init__(self, *args, source="random", **kwargs):
        if source in {"seed", "bootstrap"} and FakeRandom.forbid_bootstrap_construction:
            raise AssertionError("random campaign constructed a bootstrap generator")
        self.source = source
        self.proposals = 0
        self.cost_usd = 0.0
        self.fold_calls = []
        FakeRandom.instances.append(self)

    forbid_bootstrap_construction = False

    def propose(self):
        self.proposals += 1
        return candidate(self.source, self.proposals)

    def fold(self, candidate, run_dir, phase="explore", attempt_id=None):
        self.fold_calls.append((candidate["candidate_id"], phase))

    def snapshot(self):
        return {
            "source": self.source,
            "proposals": self.proposals,
            "fold_calls": list(self.fold_calls),
        }

    def restore(self, snapshot):
        self.proposals = snapshot["proposals"]
        self.fold_calls = [tuple(item) for item in snapshot["fold_calls"]]

    def state(self):
        return {"source": self.source, "proposals": self.proposals}


class FakeAdaptive:
    instances = []

    def __init__(self, *args, **kwargs):
        self.skill = args[0] if args else "demo"
        self.seed_gen = args[-1] if len(args) >= 7 else None
        self.proposals = 0
        self.fold_calls = []
        self.last_target_parent = None
        self.cost_usd = 0.0
        FakeAdaptive.instances.append(self)

    def propose(self, cases_dir=None):
        self.proposals += 1
        cand = candidate("adaptive", self.proposals)
        if cases_dir is None:
            return cand
        return campaign_loop.materialize_case(cand, cases_dir), "skillrace"

    def fold(self, candidate_or_case, run_dir, phase="explore", attempt_id=None):
        if isinstance(candidate_or_case, dict):
            name = candidate_or_case["candidate_id"]
        else:
            name = pathlib.Path(candidate_or_case).name
        self.fold_calls.append((name, phase))
        return []

    def snapshot(self):
        return {
            "source": "adaptive",
            "proposals": self.proposals,
            "fold_calls": list(self.fold_calls),
        }

    def restore(self, snapshot):
        self.proposals = snapshot["proposals"]
        self.fold_calls = [tuple(item) for item in snapshot["fold_calls"]]

    def state(self):
        return {"source": "adaptive", "proposals": self.proposals}


@pytest.fixture(autouse=True)
def reset_fakes():
    FakeRandom.instances = []
    FakeAdaptive.instances = []
    FakeRandom.forbid_bootstrap_construction = False


def install_shared_execution_fakes(monkeypatch):
    monkeypatch.setattr(campaign_loop, "RandomGenerator", FakeRandom)
    monkeypatch.setattr(campaign_loop, "GreyboxGenerator", FakeAdaptive)
    monkeypatch.setattr(campaign_loop, "SkillRACEGenerator", FakeAdaptive)
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
            "image": image,
            "valid": True,
            "rejection": None,
            "checks": [],
        },
    )
    monkeypatch.setattr(campaign_loop, "compile_case", lambda *a, **k: ({}, 0.0))
    monkeypatch.setattr(
        campaign_loop, "verify_runtime_integrity", lambda *a, **k: {"runtime": "ok"}
    )

    def run_agent(case_dir, run_dir, model, wall_clock, skill_dir):
        run_dir.mkdir(parents=True)
        manifest = {"agent_started": True, "termination": {"reason": "completed"}}
        (run_dir / "run.json").write_text(json.dumps(manifest))
        return 0, "", manifest

    monkeypatch.setattr(campaign_loop, "run_agent", run_agent)
    monkeypatch.setattr(campaign_loop, "check_run", lambda *a, **k: ([], [], 0))


def test_random_uses_thirty_fresh_exploration_cases_and_never_constructs_bootstrap(
    tmp_path, monkeypatch
):
    skill_dir = write_skill(tmp_path)
    install_shared_execution_fakes(monkeypatch)
    FakeRandom.forbid_bootstrap_construction = True

    campaign = campaign_loop.run_campaign(
        "random", "demo", skill_dir, "demo:base", skill_dir / "properties.json",
        out_dir=tmp_path / "out", development_only=True,
        budget=30, seed_count=10,
    )

    random = FakeRandom.instances[0]
    assert random.source == "random"
    assert random.proposals == 30
    assert len(campaign["iterations"]) == 30
    assert {item["phase"] for item in campaign["iterations"]} == {"explore"}


@pytest.mark.parametrize("method", ["greybox", "skillrace"])
def test_adaptive_methods_count_ten_bootstrap_then_twenty_exploration(
    method, tmp_path, monkeypatch
):
    skill_dir = write_skill(tmp_path)
    install_shared_execution_fakes(monkeypatch)

    campaign = campaign_loop.run_campaign(
        method, "demo", skill_dir, "demo:base", skill_dir / "properties.json",
        out_dir=tmp_path / method, development_only=True,
        budget=30, seed_count=10,
    )

    bootstrap = next(item for item in FakeRandom.instances if item.source == "bootstrap")
    adaptive = FakeAdaptive.instances[0]
    assert bootstrap.proposals == 10
    assert adaptive.proposals == 20
    assert [item["phase"] for item in campaign["iterations"]] == [
        *("bootstrap" for _ in range(10)),
        *("explore" for _ in range(20)),
    ]
    assert [phase for _, phase in adaptive.fold_calls] == [
        *("bootstrap" for _ in range(10)),
        *("explore" for _ in range(20)),
    ]


def test_differing_agent_model_is_rejected_before_any_campaign_work(tmp_path):
    with pytest.raises(ValueError, match="same model"):
        campaign_loop.run_campaign(
            "random", "demo", tmp_path / "missing", "demo:base", "properties.json",
            budget=30, seed_count=10, out_dir=tmp_path / "out",
            model="qwen3.6-flash", agent_model="other-model", development_only=True,
        )


def test_sanity_rejection_is_saved_and_precedes_compile_and_pi_for_every_method(
    tmp_path, monkeypatch
):
    for method in ("random", "greybox", "skillrace"):
        root = tmp_path / method
        root.mkdir()
        skill_dir = write_skill(root)
        install_shared_execution_fakes(monkeypatch)
        calls = []
        monkeypatch.setattr(
            campaign_loop,
            "run_candidate_sanity",
            lambda image, spec: calls.append(("sanity", image)) or {
                "schema": "candidate-sanity/1",
                "image": image,
                "valid": False,
                "rejection": "task-probe",
                "checks": [],
            },
        )
        monkeypatch.setattr(
            campaign_loop,
            "compile_case",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("compile called")),
        )
        monkeypatch.setattr(
            campaign_loop,
            "run_agent",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("Pi called")),
        )

        campaign = campaign_loop.run_campaign(
            method, "demo", skill_dir, "demo:base", skill_dir / "properties.json",
            budget=1, seed_count=0, out_dir=root / "out",
            max_pre_agent_attempts=1, development_only=True,
        )

        assert calls and calls[0][0] == "sanity"
        assert campaign["iterations"] == []
        assert campaign["attempts"][0]["generation_status"] == "sanity_rejected"
        sanity_path = pathlib.Path(campaign["attempts"][0]["case"]) / "sanity.json"
        assert json.loads(sanity_path.read_text())["rejection"] == "task-probe"


def test_cli_and_suite_defaults_are_lean_and_have_no_model_or_level_sweep():
    parser = campaign_loop.build_parser()
    args = parser.parse_args(
        [
            "--method", "random", "--skill", "demo", "--skill-dir", "skills/demo",
            "--base", "demo:base", "--props", "properties.json", "--out", "out",
        ]
    )
    assert pathlib.Path(args.protocol).name == "issta-main.draft.json"
    for field in ["budget", "seed_count", "model", "greybox_level", "agent_model"]:
        assert not hasattr(args, field)

    suite = pathlib.Path("scripts/run_suite.sh").read_text()
    assert "PROTOCOL=${PROTOCOL:-experiments/protocols/issta-main.draft.json}" in suite
    assert suite.count("run random") == 1
    assert suite.count("run greybox") == 1
    assert suite.count("run skillrace") == 1
    assert "L0" not in suite and "L2" not in suite and "AGENT_MODEL" not in suite


def test_sanity_infrastructure_failure_is_not_counted_as_invalid_candidate(
    tmp_path, monkeypatch
):
    skill_dir = write_skill(tmp_path)
    install_shared_execution_fakes(monkeypatch)
    monkeypatch.setattr(
        campaign_loop,
        "run_candidate_sanity",
        lambda *a, **k: (_ for _ in ()).throw(
            SanityInfrastructureError("docker daemon unavailable")
        ),
    )
    campaign = campaign_loop.run_campaign(
        "random", "demo", skill_dir, "demo:base", skill_dir / "properties.json",
        out_dir=tmp_path / "out", budget=1, seed_count=0,
        max_pre_agent_attempts=1, development_only=True,
    )
    attempt = campaign["attempts"][0]
    assert attempt["generation_status"] == "generated"
    assert attempt["infrastructure_status"] == "sanity_infrastructure_error"
    assert attempt["sanity_status"] == "infrastructure_error"
    assert attempt["consume_budget"] is False


@pytest.mark.parametrize(
    ("error", "generation_status", "infrastructure_status"),
    [
        (RuntimeIntegrityError("tampered pi"), "runtime_rejected", "not_started"),
        (
            RuntimeFingerprintError("docker cp unavailable"),
            "generated",
            "runtime_fingerprint_error",
        ),
    ],
)
def test_runtime_mismatch_and_measurement_failure_are_separately_accounted(
    error, generation_status, infrastructure_status, tmp_path, monkeypatch
):
    skill_dir = write_skill(tmp_path)
    install_shared_execution_fakes(monkeypatch)
    monkeypatch.setattr(
        campaign_loop,
        "verify_runtime_integrity",
        lambda *a, **k: (_ for _ in ()).throw(error),
    )
    compile_calls = []
    monkeypatch.setattr(
        campaign_loop, "compile_case", lambda *a, **k: compile_calls.append(a)
    )
    campaign = campaign_loop.run_campaign(
        "random", "demo", skill_dir, "demo:base", skill_dir / "properties.json",
        out_dir=tmp_path / "out", budget=1, seed_count=0,
        max_pre_agent_attempts=1, development_only=True,
    )
    attempt = campaign["attempts"][0]
    assert attempt["generation_status"] == generation_status
    assert attempt["infrastructure_status"] == infrastructure_status
    assert attempt["consume_budget"] is False
    assert compile_calls == []


def test_host_runtime_fingerprint_precedes_any_candidate_command(
    tmp_path, monkeypatch
):
    skill_dir = write_skill(tmp_path)
    install_shared_execution_fakes(monkeypatch)
    order = []
    monkeypatch.setattr(
        campaign_loop,
        "verify_runtime_integrity",
        lambda *a, **k: order.append("host-fingerprint") or {"runtime": "ok"},
    )
    monkeypatch.setattr(
        campaign_loop,
        "run_candidate_sanity",
        lambda *a, **k: order.append("candidate-sanity") or {
            "schema": "candidate-sanity/1",
            "valid": False,
            "rejection": "task-probe",
            "checks": [],
        },
    )
    campaign_loop.run_campaign(
        "random", "demo", skill_dir, "demo:base", skill_dir / "properties.json",
        out_dir=tmp_path / "out", budget=1, seed_count=0,
        max_pre_agent_attempts=1, development_only=True,
    )
    assert order == ["host-fingerprint", "candidate-sanity"]


def test_real_campaign_resume_refuses_skill_edit_and_base_tag_retarget(
    tmp_path, monkeypatch
):
    skill_dir = write_skill(tmp_path)
    (skill_dir / "SKILL.md").write_text("version one")
    install_shared_execution_fakes(monkeypatch)
    base_id = {"value": "sha256:" + "a" * 64}
    monkeypatch.setattr(
        campaign_loop,
        "resolve_base_image_identity",
        lambda image, resolver=None: base_id["value"],
    )

    def run(out):
        return campaign_loop.run_campaign(
            "random", "demo", skill_dir, "demo:base",
            skill_dir / "properties.json", out_dir=out,
            development_only=True, budget=1, seed_count=0,
        )

    run(tmp_path / "skill-edit")
    (skill_dir / "SKILL.md").write_text("version two")
    with pytest.raises(ValueError, match="output identity"):
        run(tmp_path / "skill-edit")

    (skill_dir / "SKILL.md").write_text("version one")
    run(tmp_path / "base-retarget")
    base_id["value"] = "sha256:" + "b" * 64
    with pytest.raises(ValueError, match="output identity"):
        run(tmp_path / "base-retarget")
