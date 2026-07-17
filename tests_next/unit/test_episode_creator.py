from dataclasses import replace
import json
from pathlib import Path

import pytest

from skillrace_next.records import RunRecord
from skillrace_next.runtime.pi import PiRequest, PiResult
from skillrace_next.methods.skillrace import create_episodes, validate_episodes
from tests_next.unit.test_test_cases import config_for


TRACE = Path("tests_next/fixtures/traces/two-step.jsonl")


def valid_episodes() -> list[dict[str, object]]:
    return [
        {
            "episode_id": "episode-1",
            "start_event_id": "e1",
            "end_event_id": "e2",
            "purpose": "Write the requested file",
            "outcome": "The write tool created result.txt",
            "reason_for_next": "Read the file to verify its content",
        },
        {
            "episode_id": "episode-2",
            "start_event_id": "e3",
            "end_event_id": "e4",
            "purpose": "Verify the written file",
            "outcome": "The read tool returned the expected content",
            "reason_for_next": None,
        },
    ]


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


def test_validate_episodes_accepts_ordered_grounded_complete_ranges() -> None:
    assert validate_episodes(valid_episodes(), TRACE) == valid_episodes()


def test_validate_episodes_rejects_overlap() -> None:
    episodes = valid_episodes()
    episodes[1]["start_event_id"] = "e2"

    with pytest.raises(ValueError, match="overlap"):
        validate_episodes(episodes, TRACE)


def test_validate_episodes_rejects_ungrounded_range() -> None:
    episodes = valid_episodes()
    episodes[0]["start_event_id"] = "e0"
    episodes[0]["end_event_id"] = "e0"

    with pytest.raises(ValueError, match="grounded"):
        validate_episodes(episodes, TRACE)


def test_validate_episodes_rejects_missing_relevant_event_coverage() -> None:
    with pytest.raises(ValueError, match="coverage"):
        validate_episodes(valid_episodes()[:1], TRACE)


def test_validate_episodes_rejects_missing_or_extra_fields() -> None:
    missing = valid_episodes()
    del missing[0]["outcome"]
    with pytest.raises(ValueError, match="fields"):
        validate_episodes(missing, TRACE)

    extra = valid_episodes()
    extra[0]["summary"] = "free-floating"
    with pytest.raises(ValueError, match="fields"):
        validate_episodes(extra, TRACE)


def test_create_episodes_uses_same_track_pi_and_saves_validated_output(
    tmp_path: Path,
) -> None:
    requests: list[PiRequest] = []

    def fake_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        trace = request.output_dir / "trace.jsonl"
        trace.write_text(
            json.dumps(
                {
                    "type": "message",
                    "id": "response",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": json.dumps(valid_episodes())}
                        ],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        receipt = request.output_dir / "receipt.json"
        receipt.write_text('{"status":"completed"}\n', encoding="utf-8")
        return PiResult(
            operation_id=request.operation_id,
            model=request.model,
            status="completed",
            trace_path=trace,
            usage={"total_tokens": 10},
            stderr="",
            receipt_path=receipt,
            return_code=0,
            wall_seconds=0.1,
            timeout_seconds=request.timeout_seconds,
        )

    config = replace(
        config_for(tmp_path),
        role_budgets={"segmenter": 4},
    )
    episodes, receipt = create_episodes(
        run_record(tmp_path), config, tmp_path / "episodes", fake_pi
    )

    assert episodes == valid_episodes()
    assert receipt == requests[0].output_dir / "receipt.json"
    assert requests[0].model == "deepseek-v3.2"
    assert requests[0].allowed_tools == ("read",)
    assert requests[0].mounts == ((TRACE, "/input/run-trace.jsonl", "ro"),)
    prompt = requests[0].prompt_path.read_text(encoding="utf-8")
    assert '"id":"e1"' in prompt
    assert "Do not create episodes for text-only assistant messages" in prompt
    assert '"episode_id":"episode-1"' in prompt
    assert "All IDs must be JSON strings" in prompt
    assert json.loads((tmp_path / "episodes" / "episodes.json").read_text()) == episodes


def test_create_episodes_allows_one_json_correction(tmp_path: Path) -> None:
    calls = 0

    def correcting_pi(request: PiRequest) -> PiResult:
        nonlocal calls
        calls += 1
        request.output_dir.mkdir(parents=True, exist_ok=True)
        trace = request.output_dir / "trace.jsonl"
        response = "not-json" if calls == 1 else json.dumps(valid_episodes())
        trace.write_text(
            json.dumps(
                {
                    "type": "message",
                    "id": f"response-{calls}",
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

    config = replace(config_for(tmp_path), role_budgets={"segmenter": 4})
    episodes, _ = create_episodes(
        run_record(tmp_path), config, tmp_path / "episodes", correcting_pi
    )

    assert episodes == valid_episodes()
    assert calls == 2
