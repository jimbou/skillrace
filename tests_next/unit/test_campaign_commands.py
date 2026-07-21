from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from skillrace_next.pipeline import campaigns
from skillrace_next.storage import file_hash, tree_hash
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
            "skillrace",
            {
                "schema": "skillrace-campaign-state/1",
                "phase": "branch",
                "execution_count": 10,
                "plan": {},
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
                            "node_id": "source",
                            "purpose": "inspect the artifact",
                            "outcome": "artifact found",
                            "member_run_ids": ["run-1"],
                            "member_episode_ids": ["episode-1"],
                            "reach_status": "reached",
                            "failure_ids": [],
                        },
                        {
                            "node_id": "target",
                            "purpose": "exercise write validation",
                            "outcome": "validation completed",
                            "member_run_ids": ["run-1"],
                            "member_episode_ids": ["episode-2"],
                            "reach_status": "reached",
                            "failure_ids": [],
                        },
                    ],
                    "edges": [
                        {
                            "source_node_id": "root",
                            "target_node_id": "source",
                            "reason": "inspect the artifact",
                        },
                        {
                            "source_node_id": "source",
                            "target_node_id": "target",
                            "reason": "target validation",
                        }
                    ],
                },
                "current_selection": None,
                "observations": [],
            },
            campaigns.skillrace_method,
        ),
    ],
)
def test_adaptive_proposals_are_stored_under_the_iteration_selection(
    tmp_path: Path, monkeypatch, method: str, state: dict, module
) -> None:
    skill = skill_version(tmp_path)
    config = replace(config_for(tmp_path), iteration_budget=30)
    destination = tmp_path / "method" / "iterations" / "1" / "selection"
    observed = {}
    proposed = SimpleNamespace(
        test_id=f"{method}-test",
        validation_status="valid",
        validation_diagnostic="",
        proposal_receipt=tmp_path / "adaptive-proposal.json",
    )
    proposed.proposal_receipt.write_text(
        '{"target_edge_id":"edge-selected"}\n', encoding="utf-8"
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
    assert state["current_selection"]["target_edge_id"] == "edge-selected"


def test_campaign_wires_verigrey_initial_corpus_selection_and_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = skill_version(tmp_path)
    config = config_for(tmp_path)
    properties = [
        {"property_id": "P1", "description": "The output is correct."},
        {"property_id": "P2", "description": "The output is verified."},
    ]
    state: dict = {}
    proposed = SimpleNamespace(
        test_id="verigrey-seed-P1",
        validation_status="valid",
        validation_diagnostic="",
    )
    observed = {}

    def fake_initialize(received_skill, received_properties, received_config, output):
        observed["initialize"] = (
            received_skill,
            received_properties,
            received_config,
            output,
        )
        return {
            "schema": "skillrace-verigrey-campaign-state/1",
            "phase": "seeding",
        }

    def fake_select(
        received_state,
        received_skill,
        received_properties,
        received_config,
        output,
    ):
        observed["select"] = (
            received_state,
            received_skill,
            received_properties,
            received_config,
            output,
        )
        return proposed

    monkeypatch.setattr(campaigns.verigrey_method, "initialize_corpus", fake_initialize)
    monkeypatch.setattr(campaigns.verigrey_method, "select_test", fake_select)
    monkeypatch.setattr(
        campaigns,
        "_seed_test",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("VeriGrey must not use the Random seed shortcut")
        ),
    )

    selected = campaigns._select_test(
        "verigrey",
        state,
        skill,
        properties,
        config,
        tmp_path / "selection",
    )

    assert state == {
        "schema": "skillrace-verigrey-campaign-state/1",
        "phase": "seeding",
    }
    assert observed["initialize"][0] is skill
    assert observed["initialize"][1] == properties
    assert observed["initialize"][3] == tmp_path / "selection" / "initial-corpus"
    assert observed["select"][0] is state
    assert observed["select"][1] is skill
    assert observed["select"][2] == properties
    assert observed["select"][4] == tmp_path / "selection" / "proposal"
    assert selected["case"] is proposed

    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        '{"message":{"role":"assistant","content":'
        '[{"type":"toolCall","name":"read","arguments":{"path":"/workspace/a"}}]}}\n',
        encoding="utf-8",
    )
    record = SimpleNamespace(trace_path=trace)

    def fake_observe(received_state, sequence):
        observed["observation"] = (received_state, sequence)
        return {"schema": "skillrace-verigrey-campaign-state/1", "phase": "mutation"}

    monkeypatch.setattr(campaigns.verigrey_method, "observe_execution", fake_observe)
    updated = campaigns._updated_state(
        "verigrey", state, record, [], config, tmp_path / "state"
    )

    assert observed["observation"][0] is state
    assert observed["observation"][1] == [
        {"tool": "read", "arguments": {"path": "string"}}
    ]
    assert updated["phase"] == "mutation"


def test_campaign_uses_ten_frozen_skillrace_descriptions_before_tree_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = skill_version(tmp_path)
    config = replace(config_for(tmp_path), iteration_budget=30)
    properties = [{"property_id": "P1", "description": "Exact output."}]
    plan_path = tmp_path / "diversity-plan.json"
    plan_path.write_text("[]\n", encoding="utf-8")
    plan = {
        "schema": "skillrace-diversity-plan/1",
        "descriptions": [
            {
                "seed_id": f"seed-{index:02d}",
                "task": f"Task {index}",
                "environment_conditions": f"Environment {index}",
            }
            for index in range(1, 11)
        ],
        "plan_path": str(plan_path),
        "plan_hash": "plan-hash",
        "catalog_hash": "catalog-hash",
        "receipt_path": str(tmp_path / "plan-receipt.json"),
    }
    observed: dict[str, object] = {"indices": []}

    def fake_plan(received_skill, received_properties, received_config, output):
        observed["plan"] = (
            received_skill,
            received_properties,
            received_config,
            output,
        )
        return plan

    def fake_materialize(
        received_plan,
        index,
        received_skill,
        received_properties,
        received_config,
        output,
    ):
        observed["indices"].append(index)
        return SimpleNamespace(
            test_id=f"skillrace-seed-{index + 1}",
            validation_status="valid",
            validation_diagnostic="",
        )

    branch = SimpleNamespace(
        test_id="skillrace-branch",
        validation_status="valid",
        validation_diagnostic="",
        proposal_receipt=tmp_path / "branch-proposal.json",
    )
    branch.proposal_receipt.write_text(
        '{"target_edge_id":"edge-observed"}\n', encoding="utf-8"
    )

    def fake_branch(tree, received_skill, received_properties, received_config):
        observed["branch"] = (
            tree,
            received_skill,
            received_properties,
            received_config,
        )
        return branch

    monkeypatch.setattr(campaigns.skillrace_method, "create_diversity_plan", fake_plan)
    monkeypatch.setattr(
        campaigns.skillrace_method, "materialize_initial_test", fake_materialize
    )
    monkeypatch.setattr(campaigns.skillrace_method, "propose_test", fake_branch)
    monkeypatch.setattr(
        campaigns,
        "_seed_test",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("SkillRACE must use its frozen diversity plan")
        ),
    )

    state: dict = {}
    first = campaigns._select_test(
        "skillrace", state, skill, properties, config, tmp_path / "selection-1"
    )

    assert observed["plan"][3] == tmp_path / "selection-1" / "diversity-plan"
    assert observed["indices"] == [0]
    assert state["schema"] == "skillrace-campaign-state/1"
    assert state["phase"] == "initial_seeds"
    assert state["execution_count"] == 0
    assert state["plan"] == plan
    assert state["current_selection"] == {
        "phase": "initial_seed",
        "seed_index": 1,
        "seed_id": "seed-01",
        "test_id": "skillrace-seed-1",
    }
    assert first["case"].test_id == "skillrace-seed-1"

    state["current_selection"] = None
    state["execution_count"] = 9
    tenth = campaigns._select_test(
        "skillrace", state, skill, properties, config, tmp_path / "selection-10"
    )
    assert observed["indices"] == [0, 9]
    assert tenth["case"].test_id == "skillrace-seed-10"

    state["current_selection"] = None
    state["execution_count"] = 10
    state["phase"] = "branch"
    state["tree"]["nodes"].extend(
        [
            {
                "node_id": "observed-source",
                "purpose": "Inspect the artifact workflow",
                "outcome": "The workflow was inspected",
                "member_run_ids": ["run-1"],
                "member_episode_ids": ["episode-1"],
                "reach_status": "reached",
                "failure_ids": [],
            },
            {
                "node_id": "observed-target",
                "purpose": "Execute the artifact workflow",
                "outcome": "The workflow was executed",
                "member_run_ids": ["run-1"],
                "member_episode_ids": ["episode-2"],
                "reach_status": "reached",
                "failure_ids": [],
            },
        ]
    )
    state["tree"]["edges"].extend(
        [
            {
                "source_node_id": "root",
                "target_node_id": "observed-source",
                "reason": "Inspect the workflow",
            },
            {
                "source_node_id": "observed-source",
                "target_node_id": "observed-target",
                "reason": "Assume the ordinary artifact path",
            },
        ]
    )
    branch_case = campaigns._select_test(
        "skillrace", state, skill, properties, config, tmp_path / "selection-11"
    )
    assert observed["branch"][0] is state["tree"]
    assert observed["branch"][2] == properties
    assert observed["branch"][3].output_root == tmp_path / "selection-11"
    assert branch_case["case"] is branch
    assert state["current_selection"]["target_edge_id"] == "edge-observed"


def test_tenth_skillrace_observation_updates_tree_then_enables_branch_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = {
        "schema": "skillrace-campaign-state/1",
        "phase": "initial_seeds",
        "execution_count": 9,
        "plan": {"plan_hash": "plan-hash"},
        "tree": campaigns._root_tree(),
        "current_selection": {
            "phase": "initial_seed",
            "seed_index": 10,
            "seed_id": "seed-10",
            "test_id": "test-10",
        },
        "observations": [],
    }
    record = SimpleNamespace(run_id="run-10")
    merged = campaigns._root_tree()
    merged["nodes"].append(
        {
            "node_id": "branch-10",
            "purpose": "A branch from the tenth seed",
            "outcome": "The branch remains unexplored",
            "member_run_ids": [],
            "member_episode_ids": [],
            "reach_status": "unreached",
            "failure_ids": [],
        }
    )
    merged["edges"].append(
        {
            "source_node_id": "root",
            "target_node_id": "branch-10",
            "reason": "Tenth seed exposed a branch",
        }
    )
    monkeypatch.setattr(
        campaigns.skillrace_method,
        "create_episodes",
        lambda *args: ([{"episode_id": "episode-10"}], tmp_path / "receipt.json"),
    )
    monkeypatch.setattr(
        campaigns.skillrace_method,
        "merge_episodes",
        lambda *args: merged,
    )

    updated = campaigns._updated_state(
        "skillrace",
        state,
        record,
        [],
        replace(config_for(tmp_path), iteration_budget=30),
        tmp_path / "update",
    )

    assert updated["execution_count"] == 10
    assert updated["phase"] == "branch"
    assert updated["tree"] == merged
    assert updated["current_selection"] is None
    assert updated["observations"] == [
        {
            "execution": 10,
            "phase": "initial_seed",
            "seed_index": 10,
            "seed_id": "seed-10",
            "test_id": "test-10",
            "run_id": "run-10",
            "episode_ids": ["episode-10"],
        }
    ]


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


def test_part2_heldout_executes_frozen_source_checks_without_codex(
    tmp_path: Path, monkeypatch
) -> None:
    suite = tmp_path / "suite"
    heldout = suite / "heldout" / "t1"
    environment = heldout / "environment"
    source_checks = heldout / "source-checks"
    environment.mkdir(parents=True)
    source_checks.mkdir()
    prompt = heldout / "prompt.txt"
    prompt.write_text("Fix /workspace/value.txt.\n", encoding="utf-8")
    (environment / "Dockerfile").write_text(
        "FROM skillrace-next/task-fixture:test\nWORKDIR /workspace\n",
        encoding="utf-8",
    )
    (environment / "sanity.json").write_text(
        '{"status":"pass"}\n', encoding="utf-8"
    )
    nl_checks = heldout / "nl-checks.json"
    nl_checks.write_text(
        '[{"property_id":"P1","description":"The value is repaired."},'
        '{"property_id":"P2","description":"The value remains readable."}]\n',
        encoding="utf-8",
    )
    frozen_script = source_checks / "value-repaired.sh"
    frozen_script.write_text(
        "#!/usr/bin/env bash\n[ -f /workspace/value.txt ]\n",
        encoding="utf-8",
    )
    receipt = heldout / "source-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema": "skillrace-part2-heldout-receipt/1",
                "property_source": {
                    "included_property_ids": ["value-repaired", "value-readable"]
                },
                "source_checks": [
                    {
                        "criterion_id": "value-repaired",
                        "prepared_path": "source-checks/value-repaired.sh",
                        "prepared_hash": file_hash(frozen_script),
                    }
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    hidden = heldout / "test-case.json"
    hidden.write_text(
        json.dumps(
            {
                "schema": "skillrace-test-case/1",
                "test_id": "heldout/t1",
                "prompt_path": "prompt.txt",
                "prompt_hash": file_hash(prompt),
                "environment_directory": "environment",
                "environment_hash": tree_hash(environment),
                "nl_check_path": "nl-checks.json",
                "nl_check_hash": file_hash(nl_checks),
                "origin_method": "heldout",
                "proposal_receipt": "source-receipt.json",
                "validation_status": "pending",
                "validation_diagnostic": "",
                "container_image_id": "",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    scenario = tmp_path / "scenario.md"
    scenario.write_text("Repair values.\n", encoding="utf-8")
    properties = tmp_path / "development-properties.json"
    properties.write_text(
        '[{"property_id":"P1","description":"The value is repaired."}]\n',
        encoding="utf-8",
    )
    generated = tmp_path / "generated"
    generated.mkdir()
    s0 = skill_version(generated)
    config = replace(
        config_for(tmp_path),
        part="part2",
        suite_path=suite,
        scenario_path=scenario,
        iteration_budget=1,
    )
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "value.txt").write_text("fixed\n", encoding="utf-8")
    record = SimpleNamespace(
        run_id="heldout-run",
        artifact_hash=tree_hash(artifact),
        artifact_path=artifact,
        container_id="container-id",
        image_id="image-id",
        model_id=config.model_id,
        cost_totals={"total_tokens": 7},
    )
    observed: dict[str, object] = {}

    monkeypatch.setattr(campaigns, "generate_base_skill", lambda *args: s0)
    monkeypatch.setattr(
        campaigns,
        "validate_test",
        lambda case, received_config: replace(
            case,
            validation_status="valid",
            validation_diagnostic="validated",
            container_image_id="sha256:fixture",
        ),
    )
    monkeypatch.setattr(campaigns, "_run", lambda *args: record)
    monkeypatch.setattr(
        campaigns,
        "_verify",
        lambda *args: pytest.fail("held-out evaluation must not invoke Codex"),
    )

    def fake_execute(container, received_artifact, bundle, output):
        observed["bundle"] = bundle
        observed["artifact"] = received_artifact
        return SimpleNamespace(
            results_id="heldout-results",
            results=({"status": "pass"},),
        )

    monkeypatch.setattr(campaigns, "execute_checks", fake_execute)

    def fake_loop(received_s0, received_config, output, **callbacks):
        test = callbacks["load_heldout"]()[0]
        evaluation = callbacks["evaluate"](
            "s0", received_s0, test, 0, tmp_path / "evaluation"
        )
        assert evaluation["passed"] is True
        return {"schema": "skillrace-part2/1"}

    monkeypatch.setattr(campaigns, "run_part2", fake_loop)

    campaigns.run_part2_campaign(
        config,
        scenario,
        properties,
        [hidden],
        tmp_path / "campaign",
    )

    bundle = observed["bundle"]
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    assert [item["property_id"] for item in manifest["checks"]] == ["P1", "P2"]
    copied_source = next(path for path in bundle.script_paths if path.suffix == ".sh")
    assert file_hash(copied_source) == file_hash(frozen_script)
    predefined_receipt = json.loads(
        bundle.codex_receipt_path.read_text(encoding="utf-8")
    )
    assert predefined_receipt["codex_used"] is False
    assert predefined_receipt["workspace_mode"] == (
        "disposable-copy-with-workspace-path-rebinding"
    )


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
