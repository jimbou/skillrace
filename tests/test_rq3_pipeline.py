from __future__ import annotations

import json
import shutil
from pathlib import Path

from skillrace.closeai import nonproduction_chat_fixture
from skillrace.rq3_base import generate_base_skill
from skillrace.rq3_pipeline import run_rq3_scenario, verify_rq3_artifacts
from tests.test_rq3_campaign_adapter import _write_real_campaign2


ROOT = Path(__file__).parents[1]


def test_end_to_end_stage_campaign_confirm_project_revise_hidden_verify_with_fakes(
    tmp_path,
):
    scenarios_root = tmp_path / "scenarios"
    scenario = scenarios_root / "argparse-cli"
    shutil.copytree(ROOT / "scenarios" / "argparse-cli", scenario)
    original_skill = (scenario / "base_skill" / "SKILL.md").read_text()
    shutil.rmtree(scenario / "base_skill")
    generate_base_skill(
        scenario_id="argparse-cli",
        purpose_path=scenario / "scenario.md",
        output_dir=scenario / "base_skill",
        chat_fn=nonproduction_chat_fixture(
            lambda *_args, **_kwargs: {
                "content": original_skill,
                "model": "glm-4.5-flash",
                "id": "base-provider-call",
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
                "cost_provider_credits": 0.01,
            }
        ),
    )
    protocol = {
        "schema": "campaign-protocol/1",
        "protocol_id": "skillrace-issta-main-glm-4.5-flash-v1",
        "status": "frozen",
        "model": "glm-4.5-flash",
        "budget": 30,
        "bootstrap_count": 10,
        "max_generation_attempts_per_execution": 5,
        "seed_generator": {"batch_size": 5, "temperature": 0.9, "build_retries": 4},
        "greybox_level": "L1",
        "random_seed": 20260711,
        "repair": {
            "enabled": True,
            "timeout_seconds": 120,
            "max_output_tokens": 4000,
            "temperature": 0.0,
            "reasoning": True,
            "backend_by_method": {
                "random": "direct",
                "greybox": "direct",
                "skillrace": "pi",
            },
        },
    }
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(protocol))
    output = tmp_path / "artifact"
    campaign_calls = []
    base_build_calls = []

    def fake_campaign_runner(request):
        campaign_calls.append(request)
        path, _, _ = _write_real_campaign2(
            request.output_dir,
            request.method,
            base_hash=request.base_skill_hash,
            base_package_hash=request.base_package_hash,
            public_stage_hash=request.public_stage_hash,
        )
        return path

    def fake_base_builder(request):
        base_build_calls.append(request)
        return {
            "construction_image_id": "sha256:" + "b" * 64,
            "image_id": "sha256:" + "a" * 64,
        }

    revision_calls = []

    def fake_revision(messages, **settings):
        serialized = json.dumps(messages)
        assert str((scenario / "tests").resolve()) not in serialized
        assert "tests/t1" not in serialized
        revision_calls.append((messages, settings))
        return {
            "content": f"# Revised {len(revision_calls)}\n",
            "model": "glm-4.5-flash",
            "id": f"revision-provider-call-{len(revision_calls)}",
            "usage": {"prompt_tokens": 20, "completion_tokens": 3},
            "cost_provider_credits": 0.02,
        }

    hidden_calls = []

    def fake_hidden(request):
        phase_record = json.loads(
            (output / "public-phase-complete.json").read_text(encoding="utf-8")
        )
        assert phase_record["schema"] == "skillrace-rq3-public-phase-complete/1"
        assert phase_record["hidden_material_included"] is False
        assert "/tests/" not in json.dumps(phase_record)
        hidden_calls.append(request)
        run_id = f"hidden-agent-{len(hidden_calls):03d}"
        verdicts = [
            {
                "property_id": property_id,
                "provenance": "hidden-independent",
                "holds": True,
                "violated": False,
            }
            for property_id in request.criterion_ids
        ]
        verdicts.append(
            {
                "property_id": "fixed-safe",
                "provenance": "fixed",
                "holds": True,
                "violated": False,
            }
        )
        execution = request.run_dir / "execution"
        execution.mkdir(parents=True, exist_ok=True)
        (execution / "launch.json").write_text(
            json.dumps({"schema": "skillrace-hidden-launch/1"}), encoding="utf-8"
        )
        (execution / "run.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "base_image": f"skillrace/skillgen-base:0.73.1-{request.agent_model}",
                    "base_image_id": "sha256:" + "a" * 64,
                    "env_image_id": "sha256:" + "b" * 64,
                    "termination": {"reason": "completed", "seconds": 1.0},
                }
            ),
            encoding="utf-8",
        )
        (execution / "verdicts.json").write_text(
            json.dumps(verdicts), encoding="utf-8"
        )
        (execution / "cost.json").write_text(
            json.dumps({"in": 10, "out": 2, "price_provider_credits": 0.01}),
            encoding="utf-8",
        )
        return {
            "status": "completed",
            "verdicts": verdicts,
            "agent_id": run_id,
            "run_id": run_id,
            "input_tokens": 10,
            "output_tokens": 2,
            "cost_provider_credits": 0.01,
            "wall_seconds": 1.0,
        }

    manifest = run_rq3_scenario(
        scenario_dir=scenario,
        scenarios_root=scenarios_root,
        out_dir=output,
        protocol_path=protocol_path,
        campaign_runner=fake_campaign_runner,
        base_builder=fake_base_builder,
        revision_chat=nonproduction_chat_fixture(fake_revision),
        confirmation_executor=lambda _request: (_ for _ in ()).throw(
            AssertionError("no findings require confirmation in this fixture")
        ),
        hidden_executor=fake_hidden,
    )

    assert len(campaign_calls) == 3
    assert len(base_build_calls) == 1
    assert base_build_calls[0].model == "glm-4.5-flash"
    assert base_build_calls[0].image == "skillrace/rq3-argparse-cli:base-glm-4.5-flash"
    assert base_build_calls[0].construction_image == (
        "skillrace/rq3-argparse-cli:base-construction-glm-4.5-flash"
    )
    assert all(
        request.base_image == "skillrace/rq3-argparse-cli:base-glm-4.5-flash"
        for request in campaign_calls
    )
    assert len(revision_calls) == 3
    assert len(hidden_calls) == 40
    assert set(manifest["repairs"]) == {"random", "greybox", "skillrace"}
    assert all(
        manifest["repairs"][method]["repair_executions"] == 0
        and (
            output / "repairs" / method / "repairs.json"
        ).is_file()
        for method in ("random", "greybox", "skillrace")
    )
    public_barrier = json.loads(
        (output / "public-phase-complete.json").read_text(encoding="utf-8")
    )
    assert "public-per-failure-repairs" in public_barrier["phase_sequence"]
    assert public_barrier["hidden_evaluation_status"] == "not-started-at-public-barrier"
    assert manifest["base_skill"]["generation_id"]
    assert all(
        condition["summary"]["scheduled"] == 10
        and condition["summary"]["functional_pass_rate"] == 1.0
        for condition in manifest["evaluations"].values()
    )
    assert verify_rq3_artifacts(output, scenario_dir=scenario)["manifest_hash"] == manifest["manifest_hash"]

    resumed = run_rq3_scenario(
        scenario_dir=scenario,
        scenarios_root=scenarios_root,
        out_dir=output,
        protocol_path=protocol_path,
        campaign_runner=lambda _request: (_ for _ in ()).throw(
            AssertionError("campaigns must resume")
        ),
        base_builder=lambda _request: (_ for _ in ()).throw(
            AssertionError("base build must resume")
        ),
        revision_chat=nonproduction_chat_fixture(
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("revisions must resume")
            )
        ),
        confirmation_executor=lambda _request: (_ for _ in ()).throw(
            AssertionError("confirmations must resume")
        ),
        hidden_executor=lambda _request: (_ for _ in ()).throw(
            AssertionError("hidden evaluations must resume")
        ),
    )
    assert resumed == manifest
