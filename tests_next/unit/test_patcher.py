from dataclasses import replace
import json
from pathlib import Path

import pytest

from skillrace_next.pipeline.stages import build_patch_evidence, patch_skill
from skillrace_next.runtime.pi import PiRequest, PiResult
from skillrace_next.storage import tree_hash
from tests_next.unit.test_patch_evidence import patch_inputs
from tests_next.unit.test_test_cases import config_for


def patcher_pi(requests: list[PiRequest], mutation: str | None = None):
    def run(request: PiRequest) -> PiResult:
        requests.append(request)
        mounts = {destination: source for source, destination, mode in request.mounts}
        candidate = mounts["/skill"]
        evidence = mounts["/evidence"]
        skill_path = candidate / "SKILL.md"
        evidence_index = evidence / "evidence.json"
        assert "Fixture" in skill_path.read_text(encoding="utf-8")
        assert json.loads(evidence_index.read_text(encoding="utf-8"))["run_id"] == "run-1"
        skill_path.write_text(
            skill_path.read_text(encoding="utf-8")
            + "\n## Failure-driven validation\nRead the output back and compare exact content.\n",
            encoding="utf-8",
        )
        if mutation == "evidence":
            evidence_index.chmod(0o644)
            evidence_index.write_text("mutated\n", encoding="utf-8")
        elif mutation == "other-skill-file":
            (candidate / "notes.txt").write_text("mutated\n", encoding="utf-8")
        request.output_dir.mkdir(parents=True, exist_ok=True)
        calls = [
            ("read", {"path": "/skill/SKILL.md"}),
            ("read", {"path": "/evidence/evidence.json"}),
            ("edit", {"path": "/skill/SKILL.md", "oldText": "old", "newText": "new"}),
        ]
        trace = request.output_dir / "trace.jsonl"
        trace.write_text(
            "".join(
                json.dumps(
                    {
                        "type": "message",
                        "id": f"patch-{index}",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "thinking", "thinking": "Address exact-output validation."},
                                {"type": "toolCall", "name": name, "arguments": arguments},
                            ],
                        },
                    }
                )
                + "\n"
                for index, (name, arguments) in enumerate(calls, 1)
            ),
            encoding="utf-8",
        )
        receipt = request.output_dir / "receipt.json"
        receipt.write_text('{"usage":{"total_tokens":50}}\n', encoding="utf-8")
        return PiResult(
            operation_id=request.operation_id,
            model=request.model,
            status="completed",
            trace_path=trace,
            usage={"total_tokens": 50},
            stderr="",
            receipt_path=receipt,
            return_code=0,
            wall_seconds=0.2,
            timeout_seconds=request.timeout_seconds,
        )

    return run


def prepared_patch(tmp_path: Path):
    skill, test, run, bundle, results = patch_inputs(tmp_path)
    (skill.directory_path / "notes.txt").write_text("immutable sibling\n", encoding="utf-8")
    skill = replace(skill, tree_hash=tree_hash(skill.directory_path))
    evidence, evidence_hash = build_patch_evidence(
        "random",
        {},
        skill,
        test,
        run,
        bundle,
        results,
        tmp_path / "evidence",
    )
    return skill, evidence, evidence_hash


def test_patcher_reads_inputs_then_changes_only_skill_md(tmp_path: Path) -> None:
    skill, evidence, evidence_hash = prepared_patch(tmp_path)
    requests: list[PiRequest] = []

    attempt = patch_skill(
        skill,
        evidence,
        "random",
        config_for(tmp_path),
        tmp_path / "patch",
        pi_runner=patcher_pi(requests),
    )

    assert len(requests) == 1
    request = requests[0]
    assert request.model == "deepseek-v3.2"
    assert request.allowed_tools == ("read", "edit")
    assert request.max_turns == 6
    assert request.timeout_seconds == 300
    prompt = request.prompt_path.read_text(encoding="utf-8")
    assert "/evidence/common/results/check_results.json" in prompt
    assert "/evidence/common/test/prompt.txt" in prompt
    assert "authoritative executable check defines the required behavior" in prompt
    assert "Do not reread evidence already included in evidence.json" in prompt
    assert "Identify the root cause, not merely restate the failing check" in prompt
    assert "environment or command-launch failure" in prompt
    assert "/evidence/common/test/environment" in prompt
    assert "/evidence/common/run/trace.jsonl" in prompt
    assert {destination: mode for _, destination, mode in request.mounts} == {
        "/skill": "rw",
        "/evidence": "ro",
    }
    assert attempt.input_skill_hash == skill.tree_hash
    assert attempt.evidence_bundle_hash == evidence_hash
    assert attempt.method == "random"
    assert attempt.model_id == "deepseek-v3.2"
    assert attempt.patch_status == "patched"
    assert attempt.replay_path is None
    assert attempt.acceptance_status == "pending"
    assert attempt.candidate_skill_hash == tree_hash(tmp_path / "patch" / "candidate")
    assert (tmp_path / "patch" / "candidate" / "notes.txt").read_text() == "immutable sibling\n"
    assert "Failure-driven validation" in (tmp_path / "patch" / "candidate" / "SKILL.md").read_text()
    assert json.loads((tmp_path / "patch" / "patch-attempt.json").read_text()) == attempt.to_dict()


@pytest.mark.parametrize("mutation", ["evidence", "other-skill-file"])
def test_patcher_invalidates_forbidden_mutation(tmp_path: Path, mutation: str) -> None:
    skill, evidence, _ = prepared_patch(tmp_path)

    attempt = patch_skill(
        skill,
        evidence,
        "random",
        config_for(tmp_path),
        tmp_path / "patch",
        pi_runner=patcher_pi([], mutation),
    )

    assert attempt.patch_status == "patch_invalid"
    assert attempt.acceptance_status == "pending"
