import json
from pathlib import Path
from typing import Any, Callable
import uuid

from ..records import ExperimentConfig, RunRecord
from ..runtime.pi import PiRequest, PiResult, run_pi
from ..storage import atomic_write_json


PiRunner = Callable[[PiRequest], PiResult]
_EPISODE_FIELDS = {
    "episode_id",
    "start_event_id",
    "end_event_id",
    "purpose",
    "outcome",
    "reason_for_next",
}


def _trace_events(trace_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    event_ids: set[str] = set()
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError("trace events must be JSON objects")
        event_id = event.get("id")
        if isinstance(event_id, str):
            if not event_id or event_id in event_ids:
                raise ValueError("trace event IDs must be nonempty and unique")
            event_ids.add(event_id)
        events.append(event)
    return events


def _is_relevant_event(event: dict[str, Any]) -> bool:
    if event.get("type") != "message" or not isinstance(event.get("id"), str):
        return False
    message = event.get("message")
    if not isinstance(message, dict):
        return False
    if message.get("role") == "toolResult":
        return True
    if message.get("role") != "assistant":
        return False
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(item, dict) and item.get("type") in {"thinking", "toolCall"}
        for item in content
    )


def validate_episodes(
    episodes: Any,
    trace_path: str | Path,
) -> list[dict[str, Any]]:
    if not isinstance(episodes, list) or not episodes:
        raise ValueError("episodes must be a nonempty list")
    events = _trace_events(Path(trace_path))
    positions = {
        event["id"]: index
        for index, event in enumerate(events)
        if isinstance(event.get("id"), str)
    }
    relevant = {
        index for index, event in enumerate(events) if _is_relevant_event(event)
    }
    if not relevant:
        raise ValueError("trace has no relevant reasoning/tool events")
    validated: list[dict[str, Any]] = []
    episode_ids: set[str] = set()
    covered: set[int] = set()
    previous_end = -1
    for episode in episodes:
        if not isinstance(episode, dict) or set(episode) != _EPISODE_FIELDS:
            raise ValueError("episode fields are invalid")
        episode_id = episode["episode_id"]
        if (
            not isinstance(episode_id, str)
            or not episode_id
            or episode_id in episode_ids
        ):
            raise ValueError("episode IDs must be nonempty and unique")
        episode_ids.add(episode_id)
        start_id = episode["start_event_id"]
        end_id = episode["end_event_id"]
        if start_id not in positions or end_id not in positions:
            raise ValueError("episode references an unknown trace event ID")
        start = positions[start_id]
        end = positions[end_id]
        if start > end:
            raise ValueError("episode start must not follow its end")
        if start <= previous_end:
            raise ValueError("episodes overlap or are out of order")
        previous_end = end
        grounded = relevant & set(range(start, end + 1))
        if not grounded:
            raise ValueError("episode range is not grounded in reasoning/tool events")
        covered.update(grounded)
        for field in ("purpose", "outcome"):
            if not isinstance(episode[field], str) or not episode[field].strip():
                raise ValueError(f"episode {field} must be nonempty")
        reason = episode["reason_for_next"]
        if reason is not None and (
            not isinstance(reason, str) or not reason.strip()
        ):
            raise ValueError("episode reason_for_next must be nonempty or null")
        validated.append(
            {
                **episode,
                "purpose": episode["purpose"].strip(),
                "outcome": episode["outcome"].strip(),
                "reason_for_next": reason.strip() if isinstance(reason, str) else None,
            }
        )
    if covered != relevant:
        raise ValueError("episode coverage omits relevant reasoning/tool events")
    return validated


def _assistant_json(trace_path: Path) -> Any:
    responses: list[str] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = event.get("message", {})
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        text = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
        if text:
            responses.append(text)
    if not responses:
        raise ValueError("episode response contains no assistant JSON")
    return json.loads(responses[-1])


def _episode_prompt(
    run_id: str,
    trace_jsonl: str,
    correction: str | None = None,
) -> str:
    suffix = (
        f"\nThe previous response was not valid JSON: {correction}. Correct only JSON syntax."
        if correction
        else ""
    )
    return (
        "Segment the supplied trace's reasoning and tool activity "
        "into ordered, non-overlapping, source-grounded episodes. Cover every assistant "
        "thinking/toolCall event and every toolResult event. Return only one JSON array. "
        "Every item must contain exactly episode_id, start_event_id, end_event_id, "
        "purpose, outcome, and reason_for_next. Event boundaries must use IDs from the "
        "trace. purpose and outcome must be nonempty. reason_for_next is a nonempty "
        f"string or null. Run ID: {run_id}.{suffix}\n\n"
        f"TRACE JSONL:\n{trace_jsonl}"
    )


def create_episodes(
    run: RunRecord,
    config: ExperimentConfig,
    output_dir: str | Path,
    pi_runner: PiRunner = run_pi,
) -> tuple[list[dict[str, Any]], Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    trace_jsonl = run.trace_path.read_text(encoding="utf-8")
    correction: str | None = None
    for ordinal in (1, 2):
        attempt = output / f"episode-attempt-{ordinal}"
        attempt.mkdir()
        prompt_path = attempt / "prompt.txt"
        prompt_path.write_text(
            _episode_prompt(run.run_id, trace_jsonl, correction), encoding="utf-8"
        )
        result = pi_runner(
            PiRequest(
                operation_id=f"episodes.{run.run_id}.{uuid.uuid4().hex}",
                model=config.model_id,
                prompt_path=prompt_path,
                output_dir=attempt,
                image=config.docker_image,
                allowed_tools=("read",),
                max_turns=config.role_budgets["segmenter"],
                timeout_seconds=config.timeouts["pi"],
                mounts=((run.trace_path, "/input/run-trace.jsonl", "ro"),),
            )
        )
        if result.status != "completed":
            raise RuntimeError(f"Pi episode creation failed: {result.status}")
        try:
            parsed = _assistant_json(result.trace_path)
        except json.JSONDecodeError as error:
            correction = str(error)
            if ordinal == 1:
                continue
            raise ValueError("two invalid JSON episode responses") from error
        episodes = validate_episodes(parsed, run.trace_path)
        atomic_write_json(output / "episodes.json", episodes)
        atomic_write_json(
            output / "episode-creation.json",
            {
                "schema": "skillrace-episode-creation/1",
                "run_id": run.run_id,
                "model": config.model_id,
                "episode_count": len(episodes),
                "pi_receipt_path": str(result.receipt_path),
            },
        )
        return episodes, result.receipt_path
    raise RuntimeError("episode creation loop did not return")
