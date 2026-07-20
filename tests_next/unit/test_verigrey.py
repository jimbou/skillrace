from dataclasses import replace
import json
from pathlib import Path

from skillrace_next.methods.verigrey import (
    normalize_tool_sequence,
    propose_test,
    update_state,
)
from skillrace_next.records import SkillVersion, TestCase as CaseRecord
from skillrace_next.runtime.pi import PiRequest, PiResult
from skillrace_next.storage import tree_hash
from tests_next.unit.test_test_cases import config_for


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


def test_update_state_rejects_empty_tool_sequence() -> None:
    try:
        update_state({}, [])
    except ValueError as error:
        assert "nonempty" in str(error)
    else:
        raise AssertionError("empty sequences must be rejected")


def test_proposal_targets_undercovered_transition_and_records_exact_evidence(
    tmp_path: Path,
) -> None:
    read, write = normalized_sequence()
    bash = {"tool": "bash", "arguments": {"command": "string"}}
    state = update_state({}, [read, write])
    state = update_state(state, [read, write])
    state = update_state(state, [write, bash])
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Fixture skill\n", encoding="utf-8")
    receipt = tmp_path / "skill-receipt.json"
    receipt.write_text("{}\n", encoding="utf-8")
    skill = SkillVersion(
        skill_id="fixture",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id="deepseek-v3.2",
        receipt_path=receipt,
    )
    requests: list[PiRequest] = []

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
                                "text": json.dumps(
                                    {
                                        "prompt": "Create /workspace/result.txt with one line.",
                                        "check_description": (
                                            "/workspace/result.txt exists and has exactly one line."
                                        ),
                                    }
                                ),
                            }
                        ],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        pi_receipt = request.output_dir / "receipt.json"
        pi_receipt.write_text("{}\n", encoding="utf-8")
        return PiResult(
            operation_id=request.operation_id,
            model=request.model,
            status="completed",
            trace_path=trace,
            usage={},
            stderr="",
            receipt_path=pi_receipt,
            return_code=0,
            wall_seconds=0.1,
            timeout_seconds=request.timeout_seconds,
        )

    def fake_validator(case: CaseRecord, config: object) -> CaseRecord:
        return replace(
            case,
            validation_status="valid",
            validation_diagnostic="validated",
            container_image_id="sha256:fixture",
        )

    proposed = propose_test(
        state,
        skill,
        replace(config_for(tmp_path), role_budgets={"proposer": 4}),
        pi_runner=fake_pi,
        validator=fake_validator,
    )

    target = {"source": write, "target": bash, "count": 1}
    assert len(requests) == 1
    pi_prompt = requests[0].prompt_path.read_text(encoding="utf-8")
    assert "starts with an empty /workspace" in pi_prompt
    assert "Do not use /mnt/data or /tmp" in pi_prompt
    assert "must not add requirements" in pi_prompt
    assert "meaningfully exercise the supplied skill" in pi_prompt
    assert "not a substitute for skill relevance" in pi_prompt
    assert "internally consistent" in pi_prompt
    assert "mutually inconsistent requirements" in pi_prompt
    assert json.dumps(target, sort_keys=True) in pi_prompt
    assert proposed.origin_method == "verigrey"
    assert proposed.validation_status == "valid"
    proposal = json.loads(proposed.proposal_receipt.read_text(encoding="utf-8"))
    assert proposal["novelty_target"] == target
    assert proposal["tool_sequence_evidence"] == state["last_observation"]
    assert "write" in proposed.nl_check_path.read_text(encoding="utf-8")
    assert "bash" in proposed.nl_check_path.read_text(encoding="utf-8")


def test_proposal_allows_one_format_correction(tmp_path: Path) -> None:
    read, write = normalized_sequence()
    state = update_state({}, [read, write])
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# CSV analysis\n", encoding="utf-8")
    receipt = tmp_path / "skill-receipt.json"
    receipt.write_text("{}\n", encoding="utf-8")
    skill = SkillVersion(
        skill_id="csv-analysis",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id="deepseek-v4-flash",
        receipt_path=receipt,
    )
    requests: list[PiRequest] = []

    def correcting_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        response = (
            "```json\n{\"prompt\": \"bad\", \"check_description\": \"bad\"}\n```"
            if len(requests) == 1
            else json.dumps(
                {
                    "prompt": "Create /workspace/data.csv and summarize its rows.",
                    "check_description": "The row count is reported.",
                }
            )
        )
        trace = request.output_dir / "trace.jsonl"
        trace.write_text(
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": response}],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        pi_receipt = request.output_dir / "receipt.json"
        pi_receipt.write_text("{}\n", encoding="utf-8")
        return PiResult(
            operation_id=request.operation_id,
            model=request.model,
            status="completed",
            trace_path=trace,
            usage={},
            stderr="",
            receipt_path=pi_receipt,
            return_code=0,
            wall_seconds=0.1,
            timeout_seconds=request.timeout_seconds,
        )

    def validator(case: CaseRecord, config: object) -> CaseRecord:
        return replace(
            case,
            validation_status="valid",
            validation_diagnostic="validated",
            container_image_id="sha256:fixture",
        )

    proposed = propose_test(
        state,
        skill,
        replace(config_for(tmp_path), role_budgets={"proposer": 4}),
        pi_runner=correcting_pi,
        validator=validator,
    )

    assert len(requests) == 2
    assert requests[0].output_dir.name == "proposal-attempt-1"
    assert requests[1].output_dir.name == "proposal-attempt-2"
    correction = requests[1].prompt_path.read_text(encoding="utf-8")
    assert "previous response was invalid" in correction
    assert "raw JSON only" in correction
    assert proposed.prompt_path.read_text(encoding="utf-8").startswith(
        "Create /workspace/data.csv"
    )
