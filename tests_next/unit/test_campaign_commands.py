from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from skillrace_next.pipeline import campaigns
from tests_next.unit.test_random_method import skill_version
from tests_next.unit.test_test_cases import config_for


def test_part1_campaign_constructs_immutable_s0_from_explicit_inputs(
    tmp_path: Path, monkeypatch
) -> None:
    source = skill_version(tmp_path)
    properties = tmp_path / "properties.json"
    properties.write_text(
        '[{"property_id":"P1","description":"The requested artifact is correct."}]\n',
        encoding="utf-8",
    )
    config = replace(config_for(tmp_path), iteration_budget=1)
    observed = {}

    def fake_loop(s0, received_config, output, **callbacks):
        observed.update(s0=s0, config=received_config, output=output, callbacks=callbacks)
        return {"schema": "skillrace-part1/1"}

    monkeypatch.setattr(campaigns, "run_part1", fake_loop)

    result = campaigns.run_part1_campaign(
        config,
        source.directory_path,
        source.receipt_path,
        "explicit-skill",
        properties,
        tmp_path / "campaign",
    )

    assert result["schema"] == "skillrace-part1/1"
    assert observed["s0"].skill_id == "explicit-skill"
    assert observed["s0"].version_id == "S0"
    assert observed["s0"].tree_hash == source.tree_hash
    assert observed["s0"].receipt_path == source.receipt_path
    assert set(observed["callbacks"]) == {
        "propose",
        "execute",
        "check",
        "update_state",
        "confirm",
        "patch",
    }


def test_generated_case_exposes_terminal_validation_status() -> None:
    invalid = SimpleNamespace(
        test_id="invalid-generated",
        validation_status="invalid_test",
        validation_diagnostic="second proposal failed validation",
    )

    assert campaigns._case(invalid) == {
        "test_id": "invalid-generated",
        "case": invalid,
        "validation_status": "invalid_test",
        "validation_diagnostic": "second proposal failed validation",
    }


def test_random_selection_rejects_accumulated_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        campaigns,
        "_seed_test",
        lambda *args: {"test_id": "must-not-be-called"},
    )

    with pytest.raises(ValueError, match="Random cannot receive accumulated state"):
        campaigns._select_test(
            "random",
            {"previous_test": "leaked"},
            skill_version(tmp_path),
            [{"property_id": "P1", "description": "The result is correct."}],
            replace(config_for(tmp_path), iteration_budget=30),
            tmp_path / "selection",
        )


def test_random_thirty_call_budget_reuses_only_the_complete_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = skill_version(tmp_path)
    properties = [
        {"property_id": "P1", "description": "The result is correct."},
        {"property_id": "P2", "description": "Inputs remain intact."},
        {"property_id": "P3", "description": "The result is verified."},
    ]
    config = replace(config_for(tmp_path), iteration_budget=30)
    calls: list[dict[str, object]] = []

    def fake_seed(method, received_skill, received_properties, received_config, output):
        calls.append(
            {
                "method": method,
                "skill": received_skill,
                "properties": received_properties,
                "output": output,
            }
        )
        return {"test_id": f"random-{len(calls)}"}

    monkeypatch.setattr(campaigns, "_seed_test", fake_seed)

    for iteration in range(config.iteration_budget):
        campaigns._select_test(
            "random",
            {},
            skill,
            properties,
            config,
            tmp_path / "selections" / str(iteration),
        )

    assert len(calls) == 30
    assert all(call["method"] == "random" for call in calls)
    assert all(call["skill"] is skill for call in calls)
    assert all(call["properties"] == properties for call in calls)
    assert len({str(call["output"]) for call in calls}) == 30


@pytest.mark.parametrize(
    ("method", "state", "module"),
    [
        (
            "verigrey",
            {"transition_counts": [{"count": 1}]},
            campaigns.verigrey_method,
        ),
        (
            "skillrace",
            {
                "tree": {
                    "schema": "skillrace-reasoning-tree/1",
                    "nodes": [
                        {
                            "node_id": "root",
                            "purpose": "root",
                            "outcome": "root",
                            "member_run_ids": [],
                            "member_episode_ids": [],
                            "reach_status": "reached",
                            "failure_ids": [],
                        },
                        {
                            "node_id": "target",
                            "purpose": "exercise write validation",
                            "outcome": "not reached",
                            "member_run_ids": [],
                            "member_episode_ids": [],
                            "reach_status": "unreached",
                            "failure_ids": [],
                        },
                    ],
                    "edges": [
                        {
                            "source_node_id": "root",
                            "target_node_id": "target",
                            "reason": "target validation",
                        }
                    ],
                }
            },
            campaigns.skillrace_method,
        ),
    ],
)
def test_adaptive_proposals_are_stored_under_the_iteration_selection(
    tmp_path: Path, monkeypatch, method: str, state: dict, module
) -> None:
    skill = skill_version(tmp_path)
    config = config_for(tmp_path)
    destination = tmp_path / "method" / "iterations" / "1" / "selection"
    observed = {}
    proposed = SimpleNamespace(
        test_id=f"{method}-test",
        validation_status="valid",
        validation_diagnostic="",
    )

    def fake_propose(*args, **kwargs):
        observed["properties"] = args[2]
        observed["config"] = args[3]
        return proposed

    monkeypatch.setattr(module, "propose_test", fake_propose)

    selected = campaigns._select_test(
        method, state, skill, [{"property_id": "P1"}], config, destination
    )

    assert observed["config"].output_root == destination
    assert observed["properties"] == [{"property_id": "P1"}]
    assert selected["case"] is proposed


def test_part2_campaign_opens_hidden_records_only_when_loop_requests_heldout(
    tmp_path: Path, monkeypatch
) -> None:
    config = replace(config_for(tmp_path), part="part2", iteration_budget=1)
    scenario = tmp_path / "scenario.md"
    scenario.write_text("Create a skill that writes exact requested artifacts.\n", encoding="utf-8")
    properties = tmp_path / "development-properties.json"
    properties.write_text(
        '[{"property_id":"P1","description":"The requested artifact is correct."}]\n',
        encoding="utf-8",
    )
    hidden = tmp_path / "hidden.json"
    hidden.write_text("{}\n", encoding="utf-8")
    generated = tmp_path / "generated"
    generated.mkdir()
    s0 = skill_version(generated)
    opened: list[Path] = []

    monkeypatch.setattr(campaigns, "generate_base_skill", lambda *args: s0)

    def fake_load(path, received_config):
        opened.append(path)
        return {"test_id": "hidden-1"}

    monkeypatch.setattr(campaigns, "_load_heldout_case", fake_load)

    def fake_loop(received_s0, received_config, output, **callbacks):
        assert received_s0 == s0
        assert opened == []
        assert callbacks["load_heldout"]() == [{"test_id": "hidden-1"}]
        assert opened == [hidden]
        return {"schema": "skillrace-part2/1"}

    monkeypatch.setattr(campaigns, "run_part2", fake_loop)

    result = campaigns.run_part2_campaign(
        config,
        scenario,
        properties,
        [hidden],
        tmp_path / "campaign",
    )

    assert result["schema"] == "skillrace-part2/1"


def test_part2_selection_uses_explicit_complete_development_properties(
    tmp_path: Path, monkeypatch
) -> None:
    config = replace(config_for(tmp_path), part="part2", iteration_budget=1)
    scenario = tmp_path / "scenario.md"
    scenario.write_text("The output must contain the exact requested bytes.\n", encoding="utf-8")
    properties_path = tmp_path / "development-properties.json"
    properties_path.write_text(
        "["
        '{"property_id":"P1","description":"The public behavior is correct."},'
        '{"property_id":"P2","description":"Edge cases are handled."},'
        '{"property_id":"P3","description":"The agent verifies the result."}'
        "]\n",
        encoding="utf-8",
    )
    hidden = tmp_path / "hidden.json"
    hidden.write_text("{}\n", encoding="utf-8")
    generated = tmp_path / "generated"
    generated.mkdir()
    s0 = skill_version(generated)
    observed = {}

    monkeypatch.setattr(campaigns, "generate_base_skill", lambda *args: s0)

    def fake_seed(method, skill, properties, received_config, output):
        observed.update(method=method, skill=skill, properties=properties, output=output)
        return {"test_id": "generated-development-test"}

    monkeypatch.setattr(campaigns, "_seed_test", fake_seed)

    def fake_loop(received_s0, received_config, output, **callbacks):
        selected = callbacks["select"](
            "random", {}, received_s0, 0, tmp_path / "selection"
        )
        assert selected == {"test_id": "generated-development-test"}
        return {"schema": "skillrace-part2/1"}

    monkeypatch.setattr(campaigns, "run_part2", fake_loop)

    campaigns.run_part2_campaign(
        config,
        scenario,
        properties_path,
        [hidden],
        tmp_path / "campaign",
    )

    assert observed["method"] == "random"
    assert observed["skill"] == s0
    assert observed["properties"] == [
        {"property_id": "P1", "description": "The public behavior is correct."},
        {"property_id": "P2", "description": "Edge cases are handled."},
        {"property_id": "P3", "description": "The agent verifies the result."},
    ]


def test_part2_checker_receives_the_exact_current_skill(
    tmp_path: Path, monkeypatch
) -> None:
    config = replace(config_for(tmp_path), part="part2", iteration_budget=1)
    scenario = tmp_path / "scenario.md"
    scenario.write_text("Create exact requested artifacts.\n", encoding="utf-8")
    properties = tmp_path / "development-properties.json"
    properties.write_text(
        '[{"property_id":"P1","description":"The requested artifact is correct."}]\n',
        encoding="utf-8",
    )
    hidden = tmp_path / "hidden.json"
    hidden.write_text("{}\n", encoding="utf-8")
    generated = tmp_path / "generated"
    generated.mkdir()
    s0 = skill_version(generated)
    test = {"test_id": "generated-test", "case": object()}
    observed = {}

    monkeypatch.setattr(campaigns, "generate_base_skill", lambda *args: s0)
    monkeypatch.setattr(campaigns, "_seed_test", lambda *args: test)
    monkeypatch.setattr(
        campaigns,
        "_run",
        lambda *args: SimpleNamespace(
            run_id="run-1",
            test_id="generated-test",
            model_id=config.model_id,
            skill_version_id="S0",
            cost_totals={"total_tokens": 1},
        ),
    )

    def fake_verify(skill, *args):
        observed["skill"] = skill
        return (
            object(),
            SimpleNamespace(results_id="results-1", results=()),
            {"checks": []},
        )

    monkeypatch.setattr(campaigns, "_verify", fake_verify)

    def fake_loop(received_s0, received_config, output, **callbacks):
        selected = callbacks["select"]("random", {}, received_s0, 0, tmp_path / "select")
        run = callbacks["execute"](
            "random", received_s0, selected, 0, tmp_path / "execute"
        )
        callbacks["check"]("random", run, selected, tmp_path / "checks")
        return {"schema": "skillrace-part2/1"}

    monkeypatch.setattr(campaigns, "run_part2", fake_loop)

    campaigns.run_part2_campaign(
        config,
        scenario,
        properties,
        [hidden],
        tmp_path / "campaign",
    )

    assert observed["skill"] is s0


def test_part1_patch_receives_state_from_representative_run(
    tmp_path: Path, monkeypatch
) -> None:
    source = skill_version(tmp_path)
    properties = tmp_path / "properties.json"
    properties.write_text(
        '[{"property_id":"P1","description":"Exact output."}]\n',
        encoding="utf-8",
    )
    config = replace(config_for(tmp_path), methods=("verigrey",), iteration_budget=1)
    test = {"test_id": "generated-test", "case": object()}
    record = SimpleNamespace(
        run_id="run-1",
        test_id="generated-test",
        model_id=config.model_id,
        skill_version_id="S0",
        cost_totals={"total_tokens": 1},
    )
    checked = SimpleNamespace(
        results_id="results-1",
        results=(
            {
                "check_id": "P1-C1",
                "property_id": "P1",
                "status": "fail",
                "diagnostic": "wrong output",
            },
        ),
    )
    observed = {}

    monkeypatch.setattr(campaigns, "_select_test", lambda *args: test)
    monkeypatch.setattr(campaigns, "_run", lambda *args: record)
    monkeypatch.setattr(
        campaigns,
        "_verify",
        lambda *args: (
            object(),
            checked,
            {
                "checks": [
                    {
                        "check_id": "P1-C1",
                        "root_cause_category": "validation_missing",
                    }
                ]
            },
        ),
    )
    monkeypatch.setattr(
        campaigns,
        "_updated_state",
        lambda *args: {"representative": "run-1"},
    )

    def fake_patch(method, state, *args):
        observed["state"] = state
        return (
            {
                "patch_attempt_id": "patch-1",
                "patch_status": "patch_invalid",
                "model_id": config.model_id,
                "backend": "pi",
                "cost": 0,
            },
            None,
        )

    monkeypatch.setattr(campaigns, "_patch", fake_patch)

    def fake_loop(s0, received_config, output, **callbacks):
        selected = callbacks["propose"]("verigrey", {}, s0, 0, tmp_path / "proposal")
        run = callbacks["execute"]("verigrey", s0, selected, 0, tmp_path / "execution")
        results = callbacks["check"]("verigrey", run, selected, tmp_path / "checks")
        callbacks["update_state"](
            "verigrey", {}, run, results, tmp_path / "state-update"
        )
        callbacks["patch"](
            {
                "candidate_id": "verigrey:run-1:P1-C1",
                "run_id": "run-1",
                "test_id": "generated-test",
                "method": "verigrey",
            },
            tmp_path / "patch",
        )
        return {"schema": "skillrace-part1/1"}

    monkeypatch.setattr(campaigns, "run_part1", fake_loop)

    campaigns.run_part1_campaign(
        config,
        source.directory_path,
        source.receipt_path,
        "explicit-skill",
        properties,
        tmp_path / "campaign",
    )

    assert observed["state"] == {"representative": "run-1"}
