from dataclasses import replace
import json
from pathlib import Path

import pytest

from skillrace_next.methods.episodes import (
    assemble_episodes,
    create_episodes,
    project_trace,
    target_episode_count,
    validate_episodes,
    validate_raw_episodes,
)
from skillrace_next.records import RunRecord
from skillrace_next.runtime.pi import PiRequest, PiResult
from tests_next.unit.test_test_cases import config_for


MULTI_TRACE = Path("tests_next/fixtures/traces/multi-call-and-narration.jsonl")
TRACE = Path("tests_next/fixtures/traces/two-step.jsonl")


def valid_raw_episodes() -> list[dict[str, object]]:
    return [
        {
            "start_call": 1,
            "end_call": 2,
            "purpose": "inspect inputs",
            "what_it_did": "read two files",
            "outcome": "one read succeeded and one failed",
        },
        {
            "start_call": 3,
            "end_call": 3,
            "purpose": "recover locally",
            "what_it_did": "used the available file",
            "outcome": "recovery succeeded",
        },
    ]


@pytest.mark.parametrize(
    ("calls", "target"),
    [
        (0, 0),
        (1, 1),
        (5, 5),
        (8, 8),
        (9, 9),
        (20, 10),
        (50, 14),
        (100, 20),
        (200, 20),
    ],
)
def test_target_episode_count_is_adaptive(calls: int, target: int) -> None:
    assert target_episode_count(calls) == target


def test_target_episode_count_rejects_negative_counts() -> None:
    with pytest.raises(ValueError, match="negative"):
        target_episode_count(-1)


def test_projection_excludes_text_only_narration_and_numbers_tool_calls() -> None:
    rendered, calls = project_trace(MULTI_TRACE)

    assert [item["call"] for item in calls] == [1, 2, 3]
    assert "Final answer without a tool" not in rendered
    assert "result(ERROR)" in rendered
    assert calls[0]["reasoning"] == "Inspect both candidate inputs."
    assert calls[1]["reasoning"] == calls[0]["reasoning"]
    assert calls[0]["is_turn_start"] is True
    assert calls[1]["is_turn_start"] is False
    assert calls[2]["is_turn_start"] is True
    assert calls[0]["assistant_event_id"] == "e1"
    assert calls[1]["result_event_id"] == "e3"
    assert calls[1]["is_error"] is True


def test_assembler_partitions_calls_and_attaches_verbatim_reasoning() -> None:
    _, calls = project_trace(MULTI_TRACE)
    raw = valid_raw_episodes()

    assert validate_raw_episodes(raw, calls) == raw
    episodes = assemble_episodes(raw, calls)

    assert episodes[0]["opening_reasoning"] == calls[0]["reasoning"]
    assert episodes[1]["opening_reasoning"] == calls[2]["reasoning"]
    assert set(episodes[0]) == {
        "episode_id",
        "start_call",
        "end_call",
        "purpose",
        "what_it_did",
        "outcome",
        "opening_reasoning",
    }
    assert validate_episodes(episodes, MULTI_TRACE) == episodes


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda raw: raw[1].__setitem__("start_call", 4), "gap/overlap"),
        (lambda raw: raw[1].__setitem__("start_call", 2), "gap/overlap"),
        (lambda raw: raw[0].__setitem__("end_call", 1.5), "integer"),
        (lambda raw: raw[0].pop("outcome"), "fields"),
        (lambda raw: raw[0].__setitem__("purpose", ""), "purpose"),
    ],
)
def test_raw_episode_validation_rejects_invalid_records(mutate, error: str) -> None:
    _, calls = project_trace(MULTI_TRACE)
    raw = valid_raw_episodes()
    mutate(raw)

    with pytest.raises(ValueError, match=error):
        validate_raw_episodes(raw, calls)


def test_raw_episode_validation_rejects_boundary_inside_multi_call_turn() -> None:
    _, calls = project_trace(MULTI_TRACE)
    raw = [
        {
            "start_call": 1,
            "end_call": 1,
            "purpose": "read first input",
            "what_it_did": "read one file",
            "outcome": "read succeeded",
        },
        {
            "start_call": 2,
            "end_call": 3,
            "purpose": "continue",
            "what_it_did": "read and recover",
            "outcome": "recovery succeeded",
        },
    ]

    with pytest.raises(ValueError, match="reasoning boundary"):
        validate_raw_episodes(raw, calls)


def test_projection_and_validation_reject_empty_or_tool_free_trace(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="tool calls"):
        project_trace(empty)

    tool_free = tmp_path / "tool-free.jsonl"
    tool_free.write_text(
        '{"type":"message","id":"e1","message":{"role":"assistant",'
        '"content":[{"type":"text","text":"Done"}]}}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="tool calls"):
        project_trace(tool_free)


def test_validate_episodes_rejects_changed_id_or_reasoning() -> None:
    _, calls = project_trace(MULTI_TRACE)
    episodes = assemble_episodes(valid_raw_episodes(), calls)
    episodes[0]["opening_reasoning"] = "invented"

    with pytest.raises(ValueError, match="opening reasoning"):
        validate_episodes(episodes, MULTI_TRACE)


def run_record(tmp_path: Path) -> RunRecord:
    return RunRecord(
        run_id="run-episodes",
        test_id="test-1",
        skill_id="skill-1",
        skill_version_id="S0",
        method="skillrace",
        model_id="deepseek-v3.2",
        budget=4,
        container_id="container-1",
        image_id="sha256:image",
        started_at="2026-07-17T00:00:00Z",
        ended_at="2026-07-17T00:00:01Z",
        termination_status="completed",
        artifact_path=tmp_path / "artifact",
        artifact_hash="artifact-hash",
        trace_path=TRACE,
        tool_log_path=tmp_path / "tool.jsonl",
        stdout_path=tmp_path / "stdout.txt",
        stderr_path=tmp_path / "stderr.txt",
        provider_receipt_paths=(),
        cost_totals={},
    )


def two_step_raw_split() -> list[dict[str, object]]:
    return [
        {
            "start_call": 1,
            "end_call": 2,
            "purpose": "create and verify the requested file",
            "what_it_did": "wrote result.txt and read it back",
            "outcome": "the write succeeded and the read returned ok",
        }
    ]


def fake_result(request: PiRequest, response: str, status: str = "completed") -> PiResult:
    request.output_dir.mkdir(parents=True, exist_ok=True)
    trace = request.output_dir / "trace.jsonl"
    trace.write_text(
        json.dumps(
            {
                "type": "message",
                "id": "response",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": response}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    receipt = request.output_dir / "receipt.json"
    receipt.write_text(
        json.dumps({"status": status, "model": request.model}) + "\n",
        encoding="utf-8",
    )
    return PiResult(
        operation_id=request.operation_id,
        model=request.model,
        status=status,
        trace_path=trace,
        usage={"total_tokens": 10},
        stderr="",
        receipt_path=receipt,
        return_code=0 if status == "completed" else 1,
        wall_seconds=0.1,
        timeout_seconds=request.timeout_seconds,
    )


def episode_config(tmp_path: Path):
    return replace(
        config_for(tmp_path),
        role_budgets={"segmenter": 4},
    )


def test_create_episodes_uses_target_example_temperature_zero_and_evidence(
    tmp_path: Path,
) -> None:
    requests: list[PiRequest] = []

    def fake_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        return fake_result(request, json.dumps({"episodes": two_step_raw_split()}))

    output = tmp_path / "episodes"
    config = episode_config(tmp_path)
    episodes, receipt = create_episodes(
        run_record(tmp_path), config, output, fake_pi
    )

    request = requests[0]
    assert request.provider == config.provider
    assert request.model == config.model_id
    assert request.temperature == 0
    assert request.allowed_tools == ()
    assert request.mounts == ()
    prompt = request.prompt_path.read_text(encoding="utf-8")
    assert "target episode count: 2" in prompt
    assert "WORKED EXAMPLE" in prompt
    assert "CONTINGENT" in prompt
    assert "ONLY from tool results" in prompt
    assert "exact component, artifact, symbol, bug, or validation target" in prompt
    assert "generic lifecycle phase" in prompt
    assert "new observed failure" in prompt
    assert "inside that episode's start_call through end_call span" in prompt
    example = json.loads(
        Path(
            "skillrace_next/methods/episode_assets/example_output.json"
        ).read_text(encoding="utf-8")
    )["episodes"]
    assert len(example) == 10
    assert [episode["start_call"] for episode in example] == [
        1,
        3,
        5,
        7,
        8,
        10,
        12,
        14,
        15,
        18,
    ]
    assert episodes == assemble_episodes(two_step_raw_split(), project_trace(TRACE)[1])
    creation = json.loads((output / "episode-creation.json").read_text())
    assert creation["schema"] == "skillrace-episode-creation/2"
    assert creation["tool_call_count"] == 2
    assert creation["target_episode_count"] == 2
    assert Path(creation["rendered_trace_path"]).is_file()
    assert receipt == request.output_dir / "receipt.json"


def test_create_episodes_corrects_json_then_partition_and_stops_on_success(
    tmp_path: Path,
) -> None:
    requests: list[PiRequest] = []
    responses = [
        "not-json",
        json.dumps(
            {
                "episodes": [
                    {
                        **two_step_raw_split()[0],
                        "start_call": 2,
                    }
                ]
            }
        ),
        json.dumps({"episodes": two_step_raw_split()}),
    ]

    def correcting_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        return fake_result(request, responses[len(requests) - 1])

    episodes, _ = create_episodes(
        run_record(tmp_path),
        episode_config(tmp_path),
        tmp_path / "episodes",
        correcting_pi,
    )

    assert len(requests) == 3
    assert episodes[0]["start_call"] == 1
    assert "not valid JSON" in requests[1].prompt_path.read_text(encoding="utf-8")
    assert "gap/overlap" in requests[2].prompt_path.read_text(encoding="utf-8")
    assert requests[1].prompt_path.read_text(encoding="utf-8").rstrip().endswith(
        "The first response character must be { and the last must be }."
    )


def test_create_episodes_gives_a_specific_markdown_fence_correction(
    tmp_path: Path,
) -> None:
    requests: list[PiRequest] = []
    raw = json.dumps({"episodes": two_step_raw_split()})
    responses = [f"```json\n{raw}\n```", raw]

    def correcting_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        return fake_result(request, responses[len(requests) - 1])

    create_episodes(
        run_record(tmp_path),
        episode_config(tmp_path),
        tmp_path / "episodes",
        correcting_pi,
    )

    assert len(requests) == 2
    correction = requests[1].prompt_path.read_text(encoding="utf-8")
    assert "Markdown code fence" in correction
    assert "remove the opening and closing fence lines" in correction


def test_create_episodes_rejects_three_invalid_responses(tmp_path: Path) -> None:
    calls = 0

    def invalid_pi(request: PiRequest) -> PiResult:
        nonlocal calls
        calls += 1
        return fake_result(request, "not-json")

    with pytest.raises(ValueError, match="three invalid"):
        create_episodes(
            run_record(tmp_path),
            episode_config(tmp_path),
            tmp_path / "episodes",
            invalid_pi,
        )
    assert calls == 3


def test_create_episodes_propagates_provider_status(tmp_path: Path) -> None:
    def failed_pi(request: PiRequest) -> PiResult:
        return fake_result(request, "", status="provider_error")

    with pytest.raises(RuntimeError, match="provider_error"):
        create_episodes(
            run_record(tmp_path),
            episode_config(tmp_path),
            tmp_path / "episodes",
            failed_pi,
        )
