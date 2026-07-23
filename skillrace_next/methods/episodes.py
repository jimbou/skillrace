"""Grounded tool-call projection and episode records for SkillRACE runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


RAW_EPISODE_FIELDS = {
    "start_call",
    "end_call",
    "purpose",
    "what_it_did",
    "outcome",
}
EPISODE_FIELDS = RAW_EPISODE_FIELDS | {"episode_id", "opening_reasoning"}
HEAD_LINES = 15
TAIL_LINES = 5


def target_episode_count(tool_call_count: int) -> int:
    if isinstance(tool_call_count, bool) or not isinstance(tool_call_count, int):
        raise TypeError("tool call count must be an integer")
    if tool_call_count < 0:
        raise ValueError("tool call count must not be negative")
    if tool_call_count == 0:
        return 0
    return max(1, round(tool_call_count / (3.0 + tool_call_count / 50.0)))


def _truncate(text: str, head: int = HEAD_LINES, tail: int = TAIL_LINES) -> str:
    text = (text or "").rstrip("\n")
    if not text:
        return "(empty)"
    lines = text.splitlines()
    if len(lines) <= head + tail + 1:
        return "\n".join(lines)
    omitted = len(lines) - head - tail
    return "\n".join(
        lines[:head]
        + [f"      … ({omitted} lines truncated for brevity) …"]
        + lines[-tail:]
    )


def _arguments(name: str, value: Any) -> tuple[str, str | None]:
    arguments = value if isinstance(value, dict) else {}
    if name == "bash":
        command = arguments.get("command", "")
        command = command if isinstance(command, str) else str(command)
        return "$ " + command.split("\n")[0][:200], command if "\n" in command else None
    if name == "read":
        return str(arguments.get("path", "")), None
    if name == "write":
        content = arguments.get("content")
        return str(arguments.get("path", "")), content if isinstance(content, str) else None
    if name == "edit":
        old = arguments.get("oldText", "")
        new = arguments.get("newText", "")
        return str(arguments.get("path", "")), f"- old:\n{old}\n+ new:\n{new}"
    encoded = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    return encoded[:200], encoded if len(encoded) > 200 else None


def _message_text(content: Any, block_type: str, field: str) -> str:
    if not isinstance(content, list):
        return ""
    return " ".join(
        block.get(field, "").strip()
        for block in content
        if isinstance(block, dict)
        and block.get("type") == block_type
        and isinstance(block.get(field), str)
        and block.get(field, "").strip()
    )


def _load_events(trace_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    event_ids: set[str] = set()
    for line_number, line in enumerate(
        trace_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"trace line {line_number} is not valid JSON") from exc
        if not isinstance(event, dict):
            raise ValueError(f"trace line {line_number} must be a JSON object")
        event_id = event.get("id")
        if isinstance(event_id, str):
            if not event_id or event_id in event_ids:
                raise ValueError("trace event IDs must be nonempty and unique")
            event_ids.add(event_id)
        events.append(event)
    return events


def project_trace(trace_path: str | Path) -> tuple[str, list[dict[str, Any]]]:
    """Return a readable flat trace and ordered, source-grounded tool-call records."""
    events = _load_events(Path(trace_path))
    results: dict[str, tuple[str, bool, str | None]] = {}
    for event in events:
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "toolResult":
            continue
        tool_call_id = message.get("toolCallId")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            continue
        content = message.get("content")
        result_text = "".join(
            block.get("text", "")
            for block in content if isinstance(block, dict)
            if block.get("type") == "text" and isinstance(block.get("text"), str)
        ) if isinstance(content, list) else ""
        result_event_id = event.get("id")
        results[tool_call_id] = (
            result_text,
            bool(message.get("isError")),
            result_event_id if isinstance(result_event_id, str) else None,
        )

    rendered: list[str] = []
    calls: list[dict[str, Any]] = []
    seen_tool_call_ids: set[str] = set()
    for event in events:
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        tool_calls = [
            block
            for block in content
            if isinstance(block, dict) and block.get("type") == "toolCall"
        ]
        if not tool_calls:
            continue
        reasoning = _message_text(content, "thinking", "thinking")
        note = _message_text(content, "text", "text")
        assistant_event_id = event.get("id")
        for turn_index, tool_call in enumerate(tool_calls):
            tool_call_id = tool_call.get("id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                raise ValueError("assistant tool calls must have nonempty IDs")
            if tool_call_id in seen_tool_call_ids:
                raise ValueError("assistant tool call IDs must be unique")
            seen_tool_call_ids.add(tool_call_id)
            name = tool_call.get("name")
            name = name if isinstance(name, str) and name else "?"
            arguments = tool_call.get("arguments")
            result, is_error, result_event_id = results.get(
                tool_call_id, ("(no result captured)", False, None)
            )
            call_number = len(calls) + 1
            summary, big = _arguments(name, arguments)
            rendered.append(f"Tool Call {call_number} — {name}")
            if turn_index == 0:
                rendered.append(
                    "  reasoning: "
                    + (_truncate(reasoning, 12, 3) if reasoning else "(none)")
                )
                if note:
                    rendered.append("  note: " + _truncate(note, 4, 1))
            rendered.append(f"  args: {summary}")
            if big:
                rendered.append("  args-body:\n" + _truncate(big))
            tag = "result(ERROR)" if is_error else "result"
            rendered.append(f"  {tag}:\n" + _truncate(result))
            rendered.append("")
            calls.append(
                {
                    "call": call_number,
                    "reasoning": reasoning,
                    "is_turn_start": turn_index == 0,
                    "tool": name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                    "result": result,
                    "is_error": is_error,
                    "tool_call_id": tool_call_id,
                    "assistant_event_id": (
                        assistant_event_id
                        if isinstance(assistant_event_id, str)
                        else None
                    ),
                    "result_event_id": result_event_id,
                }
            )
    if not calls:
        raise ValueError("trace contains no tool calls")
    return "\n".join(rendered).rstrip() + "\n", calls


def validate_raw_episodes(
    raw: Any, calls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Require exact fields and an ordered partition of calls 1 through N."""
    if not calls:
        raise ValueError("trace contains no tool calls")
    if not isinstance(raw, list) or not raw:
        raise ValueError("episodes must be a nonempty list")
    validated: list[dict[str, Any]] = []
    expected_start = 1
    for index, episode in enumerate(raw, 1):
        if not isinstance(episode, dict) or set(episode) != RAW_EPISODE_FIELDS:
            raise ValueError(f"episode {index} fields are invalid")
        start = episode["start_call"]
        end = episode["end_call"]
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
        ):
            raise ValueError(f"episode {index} span must use integer call numbers")
        if start != expected_start:
            raise ValueError(
                f"episode {index} start_call creates a gap/overlap; expected {expected_start}"
            )
        if end < start or end > len(calls):
            raise ValueError(f"episode {index} end_call is outside its valid span")
        opening_call = calls[start - 1]
        if not opening_call.get("is_turn_start") or not str(
            opening_call.get("reasoning", "")
        ).strip():
            raise ValueError(
                f"episode {index} must start at a new reasoning boundary"
            )
        normalized = {"start_call": start, "end_call": end}
        for field in ("purpose", "what_it_did", "outcome"):
            value = episode[field]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"episode {index} {field} must be nonempty")
            normalized[field] = value.strip()
        validated.append(normalized)
        expected_start = end + 1
    if expected_start != len(calls) + 1:
        raise ValueError(
            f"last episode ends at {expected_start - 1}; expected {len(calls)}"
        )
    return validated


def assemble_episodes(
    raw: list[dict[str, Any]], calls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    validated = validate_raw_episodes(raw, calls)
    return [
        {
            "episode_id": f"episode-{index}",
            "start_call": episode["start_call"],
            "end_call": episode["end_call"],
            "purpose": episode["purpose"],
            "what_it_did": episode["what_it_did"],
            "outcome": episode["outcome"],
            "opening_reasoning": calls[episode["start_call"] - 1]["reasoning"],
        }
        for index, episode in enumerate(validated, 1)
    ]


def validate_episodes(
    episodes: Any, trace_path: str | Path
) -> list[dict[str, Any]]:
    if not isinstance(episodes, list) or not episodes:
        raise ValueError("episodes must be a nonempty list")
    if any(not isinstance(episode, dict) or set(episode) != EPISODE_FIELDS for episode in episodes):
        raise ValueError("episode fields are invalid")
    _, calls = project_trace(trace_path)
    raw = [
        {name: episode[name] for name in RAW_EPISODE_FIELDS}
        for episode in episodes
    ]
    expected = assemble_episodes(raw, calls)
    if episodes != expected:
        raise ValueError("episode IDs or opening reasoning differ from the trace")
    return episodes
