from dataclasses import replace
import json
from pathlib import Path
from typing import Any

import pytest

from skillrace_next.methods.random import propose_test, propose_valid_test
from skillrace_next.records import SkillVersion, TestCase as SkillTestCase
from skillrace_next.runtime.pi import PiRequest, PiResult
from skillrace_next.storage import tree_hash
from tests_next.unit.test_test_cases import config_for


def skill_version(tmp_path: Path) -> SkillVersion:
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "# File writer\nCreate the requested text file.\n", encoding="utf-8"
    )
    receipt = tmp_path / "skill-receipt.json"
    receipt.write_text("{}\n", encoding="utf-8")
    return SkillVersion(
        skill_id="file-writer",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill,
        tree_hash=tree_hash(skill),
        creation_role="fixture",
        model_id="deepseek-v3.2",
        receipt_path=receipt,
    )


PROPERTIES = [
    {"property_id": "P1", "description": "The requested file exists."},
    {"property_id": "P2", "description": "The file has the requested content."},
]


def proposal(prompt: str) -> str:
    return json.dumps(
        {
            "prompt": prompt,
            "dockerfile": (
                "FROM skillrace-next/task-fixture:test\n"
                "RUN printf 'seeded\\n' > /seed.txt\n"
                "WORKDIR /workspace\n"
            ),
        }
    )


def fake_pi_responses(responses: list[str], calls: list[PiRequest]) -> Any:
    def fake(request: PiRequest) -> PiResult:
        calls.append(request)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        trace = request.output_dir / "trace.jsonl"
        response = responses[len(calls) - 1]
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
        receipt = request.output_dir / "receipt.json"
        receipt.write_text("{}\n", encoding="utf-8")
        return PiResult(
            operation_id=request.operation_id,
            model=request.model,
            status="completed",
            trace_path=trace,
            usage={"input_tokens": 10, "output_tokens": 5},
            stderr="",
            receipt_path=receipt,
            return_code=0,
            wall_seconds=0.1,
            timeout_seconds=request.timeout_seconds,
        )

    return fake


def test_random_proposal_materializes_one_independent_test(tmp_path: Path) -> None:
    calls: list[PiRequest] = []
    response = proposal("Create result.txt containing exactly alpha.")

    proposed = propose_test(
        skill_version(tmp_path),
        PROPERTIES,
        config_for(tmp_path),
        tmp_path / "proposal",
        fake_pi_responses([response], calls),
    )

    assert len(calls) == 1
    assert calls[0].temperature == 1.0
    proposal_prompt = calls[0].prompt_path.read_text(encoding="utf-8")
    assert "Dockerfile" in proposal_prompt
    assert "skillrace-next/task-fixture:test" in proposal_prompt
    assert "do not use /mnt/data or /tmp" in proposal_prompt.lower()
    assert "consistent with requirements visible" in proposal_prompt
    assert "meaningfully exercise the supplied skill" in proposal_prompt
    assert "not a substitute for skill relevance" in proposal_prompt
    assert "internally consistent" in proposal_prompt
    assert "mutually inconsistent requirements" in proposal_prompt
    assert proposed.origin_method == "random"
    assert proposed.validation_status == "pending"
    assert proposed.prompt_path.read_text(encoding="utf-8") == (
        "Create result.txt containing exactly alpha.\n"
    )
    checks = json.loads(proposed.nl_check_path.read_text(encoding="utf-8"))
    assert checks == PROPERTIES
    assert (proposed.environment_directory / "Dockerfile").read_text(
        encoding="utf-8"
    ) == (
        "FROM skillrace-next/task-fixture:test\n"
        "RUN printf 'seeded\\n' > /seed.txt\n"
        "WORKDIR /workspace\n"
    )
    assert (proposed.environment_directory / "sanity.json").is_file()
    receipt = json.loads(proposed.proposal_receipt.read_text(encoding="utf-8"))
    assert receipt["schema"] == "skillrace-generated-test-proposal/1"
    assert receipt["method"] == "random"
    assert receipt["independent"] is True
    assert receipt["catalog_hash"] == proposed.nl_check_hash
    assert receipt["prompt_hash"] == proposed.prompt_hash
    assert receipt["environment_hash"] == proposed.environment_hash


def test_random_proposal_allows_one_format_correction(tmp_path: Path) -> None:
    calls: list[PiRequest] = []
    valid = proposal("Create beta.txt.")

    proposed = propose_test(
        skill_version(tmp_path),
        PROPERTIES,
        config_for(tmp_path),
        tmp_path / "proposal",
        fake_pi_responses(["not JSON", valid], calls),
    )

    assert proposed.prompt_path.read_text(encoding="utf-8") == "Create beta.txt.\n"
    assert len(calls) == 2
    assert "correction" in calls[1].operation_id


def test_random_proposal_accepts_one_standard_json_fence(tmp_path: Path) -> None:
    calls: list[PiRequest] = []
    response = (
        "```json\n"
        + proposal("Create result.txt.")
        + "\n```"
    )

    proposed = propose_test(
        skill_version(tmp_path),
        PROPERTIES,
        config_for(tmp_path),
        tmp_path / "proposal",
        fake_pi_responses([response, response], calls),
    )

    assert proposed.prompt_path.read_text(encoding="utf-8") == "Create result.txt.\n"
    assert len(calls) == 1


def test_random_proposal_accepts_outer_fence_when_prompt_contains_code_fence(
    tmp_path: Path,
) -> None:
    calls: list[PiRequest] = []
    response = "```json\n" + proposal(
        "Create example.py containing:\n```python\nprint('ok')\n```"
    ) + "\n```"

    proposed = propose_test(
        skill_version(tmp_path),
        PROPERTIES,
        config_for(tmp_path),
        tmp_path / "proposal",
        fake_pi_responses([response, response], calls),
    )

    assert "```python" in proposed.prompt_path.read_text(encoding="utf-8")
    assert len(calls) == 1


def test_random_proposal_stops_after_second_malformed_response(tmp_path: Path) -> None:
    calls: list[PiRequest] = []

    with pytest.raises(ValueError, match="two malformed"):
        propose_test(
            skill_version(tmp_path),
            PROPERTIES,
            config_for(tmp_path),
            tmp_path / "proposal",
            fake_pi_responses(["bad", "still bad"], calls),
        )

    assert len(calls) == 2


def test_invalid_proposal_gets_one_replacement_without_spending_agent_slot(
    tmp_path: Path,
) -> None:
    skill = skill_version(tmp_path)
    base = propose_test(
        skill,
        PROPERTIES,
        config_for(tmp_path),
        tmp_path / "base",
        fake_pi_responses(
            [proposal("Create gamma.txt.")],
            [],
        ),
    )
    proposal_calls = 0
    validation_calls = 0
    weak_agent_runs = 0

    def proposer(*args: Any, **kwargs: Any) -> SkillTestCase:
        nonlocal proposal_calls
        proposal_calls += 1
        return replace(base, test_id=f"proposal-{proposal_calls}")

    def validator(test: SkillTestCase, config: Any) -> SkillTestCase:
        nonlocal validation_calls
        validation_calls += 1
        status = "invalid_test" if validation_calls == 1 else "valid"
        return replace(test, validation_status=status)

    result = propose_valid_test(
        skill,
        PROPERTIES,
        config_for(tmp_path),
        tmp_path / "slot",
        proposer=proposer,
        validator=validator,
    )

    assert result.validation_status == "valid"
    assert proposal_calls == 2
    assert validation_calls == 2
    assert weak_agent_runs == 0
