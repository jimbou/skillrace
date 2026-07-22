from dataclasses import replace
import json
from pathlib import Path

import pytest

from skillrace_next.methods import verigrey
from skillrace_next.methods.verigrey import (
    normalize_tool_sequence,
    update_state,
)
from skillrace_next.records import SkillVersion, TestCase as CaseRecord
from skillrace_next.runtime.pi import PiRequest, PiResult
from skillrace_next.storage import tree_hash
from tests_next.unit.test_test_cases import config_for


PROPERTIES = [
    {"property_id": "P1", "description": "The requested artifact is correct."},
    {"property_id": "P2", "description": "The agent verifies the result."},
]


def proposal_response(prompt: str) -> str:
    return json.dumps(
        {
            "prompt": prompt,
            "dockerfile": (
                "FROM skillrace-next/task-fixture:test\n"
                "RUN printf 'input\\n' > /fixture.txt\n"
                "WORKDIR /workspace\n"
            ),
        }
    )


def tool_trace(path: Path) -> None:
    records = [
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "name": "read",
                        "arguments": {
                            "path": "/workspace/private-123.txt",
                            "offset": 17,
                            "limit": 4,
                        },
                    }
                ],
            },
        },
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "name": "write",
                        "arguments": {
                            "path": "/workspace/result-987.json",
                            "content": "volatile output",
                            "metadata": {"overwrite": True, "tags": ["one"]},
                        },
                    }
                ],
            },
        },
    ]
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )


def normalized_sequence() -> list[dict[str, object]]:
    return [
        {
            "tool": "read",
            "arguments": {"limit": "integer", "offset": "integer", "path": "string"},
        },
        {
            "tool": "write",
            "arguments": {
                "content": "string",
                "metadata": {"overwrite": "boolean", "tags": ["string"]},
                "path": "string",
            },
        },
    ]


def fixture_skill(tmp_path: Path) -> SkillVersion:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Fixture skill\n", encoding="utf-8")
    receipt = tmp_path / "skill-receipt.json"
    receipt.write_text("{}\n", encoding="utf-8")
    return SkillVersion(
        skill_id="fixture",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id="deepseek-v4-flash",
        receipt_path=receipt,
    )


def proposal_pi(requests: list[PiRequest]):
    def fake_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        trace = request.output_dir / "trace.jsonl"
        trace.write_text(
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": proposal_response(
                                    f"Create /workspace/result-{len(requests)}.txt."
                                ),
                            }
                        ],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        receipt = request.output_dir / "receipt.json"
        receipt.write_text("{}\n", encoding="utf-8")
        return PiResult(
            operation_id=request.operation_id,
            model=request.model,
            status="completed",
            trace_path=trace,
            usage={},
            stderr="",
            receipt_path=receipt,
            return_code=0,
            wall_seconds=0.1,
            timeout_seconds=request.timeout_seconds,
        )

    return fake_pi


def valid_case(case: CaseRecord, config: object) -> CaseRecord:
    return replace(
        case,
        validation_status="valid",
        validation_diagnostic="validated",
        container_image_id="sha256:fixture",
    )


def test_verigrey_response_parser_accepts_one_exact_json_fence(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "```json\n"
                                + proposal_response("Create /workspace/result.txt.")
                                + "\n```"
                            ),
                        }
                    ],
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    parsed = verigrey._assistant_json(trace)

    assert parsed["prompt"] == "Create /workspace/result.txt."
    assert parsed["dockerfile"].startswith("FROM skillrace-next/task-fixture:test")


def test_initialize_corpus_replaces_one_deterministically_invalid_seed(
    tmp_path: Path,
) -> None:
    requests: list[PiRequest] = []
    validation_calls: list[CaseRecord] = []

    def invalid_once(case: CaseRecord, config: object) -> CaseRecord:
        validation_calls.append(case)
        if len(validation_calls) == 1:
            return replace(
                case,
                validation_status="invalid_test",
                validation_diagnostic="generated task path is outside /workspace: /tmp",
                container_image_id="",
            )
        return valid_case(case, config)

    state = verigrey.initialize_corpus(
        fixture_skill(tmp_path),
        PROPERTIES,
        replace(config_for(tmp_path), role_budgets={"proposer": 4}),
        tmp_path / "verigrey",
        pi_runner=proposal_pi(requests),
        validator=invalid_once,
    )

    assert len(requests) == 3
    assert len(validation_calls) == 3
    assert validation_calls[0].validation_status == "pending"
    cases = [CaseRecord.from_dict(seed["test_case"]) for seed in state["corpus"]]
    assert all(case.validation_status == "valid" for case in cases)
    assert "replacement-2" in str(cases[0].proposal_receipt)
    assert "replacement-1" in str(validation_calls[0].proposal_receipt)
    receipt = json.loads(Path(state["initial_corpus_receipt"]).read_text(encoding="utf-8"))
    assert receipt["seed_replacements"] == {"seed-P1": 2, "seed-P2": 1}


def test_twice_invalid_initial_seed_is_returned_once_then_next_seed_runs(
    tmp_path: Path,
) -> None:
    requests: list[PiRequest] = []

    def invalid_p1(case: CaseRecord, config: object) -> CaseRecord:
        if case.test_id == "verigrey-seed-P1":
            return replace(
                case,
                validation_status="invalid_test",
                validation_diagnostic="Docker build failed twice",
                container_image_id="",
            )
        return valid_case(case, config)

    skill = fixture_skill(tmp_path)
    config = replace(
        config_for(tmp_path),
        iteration_budget=30,
        role_budgets={"proposer": 4},
    )
    state = verigrey.initialize_corpus(
        skill,
        PROPERTIES,
        config,
        tmp_path / "verigrey",
        pi_runner=proposal_pi(requests),
        validator=invalid_p1,
    )

    assert len(requests) == 3
    invalid = verigrey.select_test(
        state,
        skill,
        PROPERTIES,
        config,
        tmp_path / "selection-1",
        pi_runner=proposal_pi(requests),
        validator=invalid_p1,
    )
    assert invalid.validation_status == "invalid_test"
    assert state["corpus"][0]["status"] == "invalid"
    assert state["current_selection"] is None

    valid = verigrey.select_test(
        state,
        skill,
        PROPERTIES,
        config,
        tmp_path / "selection-2",
        pi_runner=proposal_pi(requests),
        validator=invalid_p1,
    )
    assert valid.test_id == "verigrey-seed-P2"
    assert valid.validation_status == "valid"

    state = verigrey.observe_execution(
        state,
        [{"tool": "write", "arguments": {"path": "/workspace/result.txt"}}],
    )
    assert state["phase"] == "mutation"
    assert state["queue"] == ["seed-P2"]


def test_initialize_corpus_materializes_every_ordered_property_seed_before_execution(
    tmp_path: Path,
) -> None:
    requests: list[PiRequest] = []

    state = verigrey.initialize_corpus(
        fixture_skill(tmp_path),
        PROPERTIES,
        replace(config_for(tmp_path), role_budgets={"proposer": 4}),
        tmp_path / "verigrey",
        pi_runner=proposal_pi(requests),
        validator=valid_case,
    )

    assert len(requests) == len(PROPERTIES)
    assert all(request.temperature == 1.0 for request in requests)
    assert state["schema"] == "skillrace-verigrey-campaign-state/1"
    assert state["phase"] == "seeding"
    assert state["execution_count"] == 0
    assert state["initial_seed_count"] == len(PROPERTIES)
    assert [seed["seed_id"] for seed in state["corpus"]] == ["seed-P1", "seed-P2"]
    assert [seed["focus_property_id"] for seed in state["corpus"]] == ["P1", "P2"]
    assert all(seed["status"] == "pending" for seed in state["corpus"])
    assert state["queue"] == []
    assert state["current_selection"] is None
    for seed, request in zip(state["corpus"], requests, strict=True):
        prompt = request.prompt_path.read_text(encoding="utf-8")
        focus = next(
            item for item in PROPERTIES if item["property_id"] == seed["focus_property_id"]
        )
        assert json.dumps(focus, sort_keys=True) in prompt
        assert json.dumps(PROPERTIES, sort_keys=True) in prompt
        assert "internally consistent" in prompt
        assert "Verify every stated exact count" in prompt
        assert "BASE IMAGE CAPABILITIES" in prompt
        assert "may install additional packages online" in prompt
        case = CaseRecord.from_dict(seed["test_case"])
        assert json.loads(case.nl_check_path.read_text(encoding="utf-8")) == PROPERTIES
        proposal = json.loads(case.proposal_receipt.read_text(encoding="utf-8"))
        assert proposal["phase"] == "initial_seed"
        assert proposal["seed_id"] == seed["seed_id"]
        assert proposal["focus_property_id"] == seed["focus_property_id"]
        assert proposal["temperature"] == 1.0
        assert proposal["capability_manifest_hash"] == "fixture"
    corpus_receipt = Path(state["initial_corpus_receipt"])
    assert corpus_receipt.is_file()
    frozen = json.loads(corpus_receipt.read_text(encoding="utf-8"))
    assert frozen["seed_ids"] == ["seed-P1", "seed-P2"]
    assert frozen["catalog_hash"] == CaseRecord.from_dict(
        state["corpus"][0]["test_case"]
    ).nl_check_hash


def test_verigrey_executes_all_initial_seeds_in_order_before_mutation(
    tmp_path: Path,
) -> None:
    requests: list[PiRequest] = []
    skill = fixture_skill(tmp_path)
    config = replace(
        config_for(tmp_path), iteration_budget=30, role_budgets={"proposer": 4}
    )
    state = verigrey.initialize_corpus(
        skill,
        PROPERTIES,
        config,
        tmp_path / "verigrey",
        pi_runner=proposal_pi(requests),
        validator=valid_case,
    )

    first = verigrey.select_test(
        state,
        skill,
        PROPERTIES,
        config,
        tmp_path / "selection-1",
        pi_runner=proposal_pi(requests),
        validator=valid_case,
    )
    assert first.test_id == "verigrey-seed-P1"
    assert state["current_selection"] == {
        "phase": "initial_seed",
        "seed_id": "seed-P1",
        "test_id": first.test_id,
    }
    after_first = verigrey.observe_execution(state, normalized_sequence())
    assert after_first["phase"] == "seeding"
    assert after_first["execution_count"] == 1
    assert after_first["queue"] == []
    assert after_first["corpus"][0]["status"] == "executed"
    assert after_first["corpus"][0]["energy"] == 3

    second = verigrey.select_test(
        after_first,
        skill,
        PROPERTIES,
        config,
        tmp_path / "selection-2",
        pi_runner=proposal_pi(requests),
        validator=valid_case,
    )
    assert second.test_id == "verigrey-seed-P2"
    after_second = verigrey.observe_execution(after_first, normalized_sequence())
    assert after_second["phase"] == "mutation"
    assert after_second["execution_count"] == 2
    assert after_second["corpus"][1]["energy"] == 1
    assert after_second["queue"] == ["seed-P1", "seed-P2"]
    assert after_second["current_selection"] is None
    assert len(requests) == len(PROPERTIES)


def test_empty_tool_sequence_is_recorded_with_minimum_energy() -> None:
    coverage = update_state({}, [])

    assert coverage["sequence_counts"] == [{"sequence": [], "count": 1}]
    assert coverage["last_observation"]["novelty_delta"] == {
        "tools": [],
        "transitions": [],
        "sequence": True,
    }


def test_verigrey_fifo_energy_mutation_and_coverage_admission(
    tmp_path: Path,
) -> None:
    requests: list[PiRequest] = []
    skill = fixture_skill(tmp_path)
    config = replace(
        config_for(tmp_path), iteration_budget=30, role_budgets={"proposer": 4}
    )
    state = verigrey.initialize_corpus(
        skill,
        PROPERTIES,
        config,
        tmp_path / "verigrey",
        pi_runner=proposal_pi(requests),
        validator=valid_case,
    )
    verigrey.select_test(
        state,
        skill,
        PROPERTIES,
        config,
        tmp_path / "initial-1",
        pi_runner=proposal_pi(requests),
        validator=valid_case,
    )
    state = verigrey.observe_execution(state, normalized_sequence())
    verigrey.select_test(
        state,
        skill,
        PROPERTIES,
        config,
        tmp_path / "initial-2",
        pi_runner=proposal_pi(requests),
        validator=valid_case,
    )
    state = verigrey.observe_execution(state, normalized_sequence())
    assert state["queue"] == ["seed-P1", "seed-P2"]
    assert state["corpus"][0]["energy"] == 3

    first_mutation = verigrey.select_test(
        state,
        skill,
        PROPERTIES,
        config,
        tmp_path / "mutation-1",
        pi_runner=proposal_pi(requests),
        validator=valid_case,
    )

    assert state["queue"] == ["seed-P2"]
    assert state["active_seed"] == {
        "seed_id": "seed-P1",
        "energy_total": 3,
        "energy_remaining": 3,
    }
    assert state["current_selection"]["phase"] == "mutation"
    assert state["current_selection"]["parent_seed_id"] == "seed-P1"
    assert state["current_selection"]["assigned_energy"] == 3
    assert state["current_selection"]["mutation_ordinal"] == 1
    mutation_request = requests[-1]
    mutation_prompt = mutation_request.prompt_path.read_text(encoding="utf-8")
    parent = state["corpus"][0]
    parent_case = CaseRecord.from_dict(parent["test_case"])
    assert parent_case.prompt_path.read_text(encoding="utf-8") in mutation_prompt
    assert json.dumps(parent["tool_sequence"], sort_keys=True) in mutation_prompt
    assert json.dumps(PROPERTIES, sort_keys=True) in mutation_prompt
    assert "internally consistent" in mutation_prompt
    assert "Verify every stated exact count" in mutation_prompt
    assert "BASE IMAGE CAPABILITIES" in mutation_prompt
    assert "may install additional packages online" in mutation_prompt
    proposal = json.loads(first_mutation.proposal_receipt.read_text(encoding="utf-8"))
    assert proposal["phase"] == "mutation"
    assert proposal["parent_seed_id"] == "seed-P1"
    assert proposal["assigned_energy"] == 3
    assert proposal["mutation_ordinal"] == 1
    assert proposal["temperature"] == 1.0
    assert proposal["capability_manifest_hash"] == "fixture"
    assert json.loads(first_mutation.nl_check_path.read_text(encoding="utf-8")) == PROPERTIES

    bash = {"tool": "bash", "arguments": {"command": "string"}}
    state = verigrey.observe_execution(state, [bash])
    admitted = state["corpus"][-1]
    assert admitted["kind"] == "offspring"
    assert admitted["parent_seed_id"] == "seed-P1"
    assert admitted["status"] == "executed"
    assert admitted["energy"] == 2
    assert state["observations"][-1]["corpus_admitted"] is True
    assert state["queue"] == ["seed-P2", admitted["seed_id"]]
    assert state["active_seed"]["energy_remaining"] == 2

    for ordinal in (2, 3):
        verigrey.select_test(
            state,
            skill,
            PROPERTIES,
            config,
            tmp_path / f"mutation-{ordinal}",
            pi_runner=proposal_pi(requests),
            validator=valid_case,
        )
        assert state["current_selection"]["parent_seed_id"] == "seed-P1"
        assert state["current_selection"]["mutation_ordinal"] == ordinal
        state = verigrey.observe_execution(state, [bash])

    assert state["active_seed"] is None
    assert state["queue"] == ["seed-P2", admitted["seed_id"], "seed-P1"]
    assert state["observations"][-1]["corpus_admitted"] is False


def test_verigrey_seed_and_mutation_executions_stop_at_total_budget(
    tmp_path: Path,
) -> None:
    requests: list[PiRequest] = []
    skill = fixture_skill(tmp_path)
    config = replace(
        config_for(tmp_path),
        iteration_budget=30,
        role_budgets={"proposer": 4},
    )
    state = verigrey.initialize_corpus(
        skill,
        PROPERTIES,
        config,
        tmp_path / "verigrey",
        pi_runner=proposal_pi(requests),
        validator=valid_case,
    )

    for execution in range(config.iteration_budget):
        verigrey.select_test(
            state,
            skill,
            PROPERTIES,
            config,
            tmp_path / "selections" / str(execution),
            pi_runner=proposal_pi(requests),
            validator=valid_case,
        )
        state = verigrey.observe_execution(state, normalized_sequence())

    assert state["execution_count"] == 30
    assert len(state["observations"]) == 30
    assert [item["phase"] for item in state["observations"][:2]] == [
        "initial_seed",
        "initial_seed",
    ]
    assert all(
        item["phase"] == "mutation" for item in state["observations"][2:]
    )
    with pytest.raises(ValueError, match="budget exhausted"):
        verigrey.select_test(
            state,
            skill,
            PROPERTIES,
            config,
            tmp_path / "selection-31",
            pi_runner=proposal_pi(requests),
            validator=valid_case,
        )


def test_normalize_tool_sequence_keeps_names_and_shapes_not_values(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "trace.jsonl"
    tool_trace(trace)

    sequence = normalize_tool_sequence(trace)

    assert sequence == normalized_sequence()
    serialized = json.dumps(sequence, sort_keys=True)
    assert "private-123" not in serialized
    assert "volatile output" not in serialized


def test_update_state_copies_json_state_and_counts_novelty() -> None:
    sequence = normalized_sequence()
    empty = {}

    first = update_state(empty, sequence)
    second = update_state(first, sequence)

    assert empty == {}
    assert first["schema"] == "skillrace-verigrey-state/1"
    assert first["tool_counts"] == [
        {"tool": sequence[0], "count": 1},
        {"tool": sequence[1], "count": 1},
    ]
    assert first["transition_counts"] == [
        {"source": sequence[0], "target": sequence[1], "count": 1}
    ]
    assert first["sequence_counts"] == [{"sequence": sequence, "count": 1}]
    assert first["last_observation"] == {
        "sequence": sequence,
        "novelty_delta": {
            "tools": sequence,
            "transitions": [{"source": sequence[0], "target": sequence[1]}],
            "sequence": True,
        },
        "coverage_counts": {
            "tools": [1, 1],
            "transitions": [1],
            "sequence": 1,
        },
    }
    assert second["last_observation"]["novelty_delta"] == {
        "tools": [],
        "transitions": [],
        "sequence": False,
    }
    assert second["last_observation"]["coverage_counts"] == {
        "tools": [2, 2],
        "transitions": [2],
        "sequence": 2,
    }
    assert first["tool_counts"][0]["count"] == 1


def test_update_state_rejects_non_list_tool_sequence() -> None:
    try:
        update_state({}, None)  # type: ignore[arg-type]
    except ValueError as error:
        assert "list" in str(error)
    else:
        raise AssertionError("non-list sequences must be rejected")
