from pathlib import Path

import pytest

from skillrace_next.methods.episodes import (
    assemble_episodes,
    project_trace,
    target_episode_count,
    validate_episodes,
    validate_raw_episodes,
)


MULTI_TRACE = Path("tests_next/fixtures/traces/multi-call-and-narration.jsonl")


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


def test_target_episode_count_matches_legacy_table() -> None:
    assert [
        target_episode_count(n) for n in (0, 5, 6, 9, 10, 20, 45, 60, 100)
    ] == [0, 2, 2, 3, 3, 6, 12, 14, 20]


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
