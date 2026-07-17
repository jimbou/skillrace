from dataclasses import replace
import json
from pathlib import Path

import pytest

from skillrace_next.pipeline.stages import generate_base_skill
from skillrace_next.runtime.pi import PiRequest, PiResult
from skillrace_next.storage import tree_hash
from tests_next.unit.test_test_cases import config_for


VALID_SKILL = """---
name: reliable-file-work
description: Complete exact file tasks and verify their observable results.
---

# Reliable file work

Read the task, make the smallest requested change, and verify the exact output.
"""


def generation_config(root: Path):
    return replace(
        config_for(root),
        methods=("random", "verigrey", "skillrace"),
        role_budgets={"skill_generator": 6},
    )


def fake_generation_pi(requests: list[PiRequest], skill_text: str = VALID_SKILL):
    def run(request: PiRequest) -> PiResult:
        requests.append(request)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        trace = request.output_dir / "trace.jsonl"
        trace.write_text(
            json.dumps(
                {
                    "type": "message",
                    "id": "generation-response",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": skill_text}],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        receipt = request.output_dir / "receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "provider": "yunwu",
                    "model": request.model,
                    "status": "completed",
                    "usage": {"total_tokens": 42},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return PiResult(
            operation_id=request.operation_id,
            model=request.model,
            status="completed",
            trace_path=trace,
            usage={"total_tokens": 42},
            stderr="",
            receipt_path=receipt,
            return_code=0,
            wall_seconds=0.1,
            timeout_seconds=request.timeout_seconds,
        )

    return run


def test_generate_one_isolated_s0_with_receipt_and_identical_method_copies(
    tmp_path: Path,
) -> None:
    scenario = tmp_path / "scenario.md"
    scenario.write_text(
        "Build small command-line programs that transform local text files.\n",
        encoding="utf-8",
    )
    output = tmp_path / "generation-output"
    requests: list[PiRequest] = []
    config = generation_config(tmp_path)

    skill = generate_base_skill(
        scenario,
        config,
        output,
        pi_runner=fake_generation_pi(requests),
    )

    assert len(requests) == 1
    assert requests[0].model == "deepseek-v3.2"
    assert requests[0].allowed_tools == ("read",)
    assert requests[0].mounts == ()
    assert skill.skill_id == "test-validation-base"
    assert skill.version_id == "S0"
    assert skill.parent_version_id is None
    assert skill.creation_role == "skill_generator"
    assert skill.model_id == "deepseek-v3.2"
    assert skill.directory_path == output / "base"
    assert skill.tree_hash == tree_hash(output / "base")
    assert skill.receipt_path == output / "generation" / "pi" / "receipt.json"
    assert (output / "base" / "SKILL.md").read_bytes() == VALID_SKILL.encode()
    base_bytes = (output / "base" / "SKILL.md").read_bytes()
    for method in config.methods:
        copied = output / "methods" / method / "SKILL.md"
        assert copied.read_bytes() == base_bytes
        assert list(copied.parent.iterdir()) == [copied]
    generation = json.loads((output / "generation.json").read_text(encoding="utf-8"))
    assert generation == {
        "schema": "skillrace-base-skill-generation/1",
        "scenario_path": str(scenario),
        "model": "deepseek-v3.2",
        "trace_path": str(output / "generation" / "pi" / "trace.jsonl"),
        "pi_receipt_path": str(output / "generation" / "pi" / "receipt.json"),
        "usage": {"total_tokens": 42},
        "method_copy_paths": {
            method: str(output / "methods" / method) for method in config.methods
        },
    }
    record = json.loads((output / "skill-version.json").read_text(encoding="utf-8"))
    assert record == skill.to_dict()
    assert scenario.read_text(encoding="utf-8").startswith("Build small")


@pytest.mark.parametrize(
    "response",
    [
        "",
        "```markdown\n" + VALID_SKILL + "```\n",
        "# Missing front matter\n",
        "---\nname: x\ndescription:\n---\n# Empty description\n",
    ],
    ids=("empty", "fenced", "missing-front-matter", "empty-description"),
)
def test_generate_base_skill_rejects_invalid_skill_response(
    tmp_path: Path, response: str
) -> None:
    scenario = tmp_path / "scenario.md"
    scenario.write_text("A public coding scenario.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="SKILL.md"):
        generate_base_skill(
            scenario,
                generation_config(tmp_path),
            tmp_path / "output",
            pi_runner=fake_generation_pi([], response),
        )
