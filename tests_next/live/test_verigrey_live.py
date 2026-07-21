from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from skillrace_next.methods.verigrey import (
    normalize_tool_sequence,
    propose_test,
    update_state,
)
from skillrace_next.records import SkillVersion
from skillrace_next.storage import atomic_write_json, tree_hash
from tests_next.live.test_tree_merge_live import live_config


pytestmark = pytest.mark.live


PROPERTIES = [
    {"property_id": "P1", "description": "The requested artifact is correct."},
    {"property_id": "P2", "description": "The agent verifies the result."},
]


def write_trace(path: Path) -> None:
    calls = [
        ("read", {"path": "/workspace/input.txt", "offset": 2, "limit": 3}),
        ("write", {"path": "/workspace/output.txt", "content": "three lines"}),
        ("bash", {"command": "wc -l /workspace/output.txt"}),
    ]
    path.write_text(
        "".join(
            json.dumps(
                {
                    "type": "message",
                    "id": f"call-{index}",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "toolCall", "name": name, "arguments": arguments}
                        ],
                    },
                }
            )
            + "\n"
            for index, (name, arguments) in enumerate(calls, 1)
        ),
        encoding="utf-8",
    )


def test_real_yunwu_proposes_valid_test_for_tool_novelty_target(
    live_evidence_root: Path,
) -> None:
    secret = os.environ.get("yunwu_key")
    if not secret:
        pytest.skip("yunwu_key is required for the VeriGrey proposal contract")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "verigrey" / run_id
    evidence.mkdir(parents=True)
    trace = evidence / "source-trace.jsonl"
    write_trace(trace)
    sequence = normalize_tool_sequence(trace)
    state = update_state({}, sequence[:2])
    state = update_state(state, sequence[:2])
    state = update_state(state, sequence[1:])
    atomic_write_json(evidence / "state.json", state)
    skill_dir = evidence / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "# Exact file workflow\nCreate requested files, then verify observable results "
        "with shell commands.\n",
        encoding="utf-8",
    )
    skill_receipt = evidence / "skill-receipt.json"
    atomic_write_json(skill_receipt, {"source": "live fixture"})
    skill = SkillVersion(
        skill_id="live-verigrey-skill",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id="deepseek-v3.2",
        receipt_path=skill_receipt,
    )
    config = replace(
        live_config(evidence, {"proposer": 4}),
        experiment_id="live-verigrey",
        methods=("verigrey",),
    )
    base_image_id = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", config.docker_image],
        check=True,
        text=True,
        capture_output=True,
        timeout=config.timeouts["docker"],
    ).stdout.strip()
    atomic_write_json(
        evidence / "docker-preflight.json",
        {"base_image": config.docker_image, "base_image_id": base_image_id},
    )

    proposed = propose_test(state, skill, PROPERTIES, config)

    assert proposed.validation_status == "valid"
    assert proposed.container_image_id.startswith("sha256:")
    proposal = json.loads(proposed.proposal_receipt.read_text(encoding="utf-8"))
    assert proposal["novelty_target"]["source"]["tool"] == "write"
    assert proposal["novelty_target"]["target"]["tool"] == "bash"
    assert proposal["tool_sequence_evidence"] == state["last_observation"]
    pi_receipt = json.loads(Path(proposal["pi_receipt_path"]).read_text(encoding="utf-8"))
    assert pi_receipt["provider"] == "yunwu"
    assert pi_receipt["model"] == "deepseek-v3.2"
    assert pi_receipt["status"] == "completed"
    assert pi_receipt["usage"]["total_tokens"] > 0
    assert json.loads(proposed.nl_check_path.read_text(encoding="utf-8")) == PROPERTIES
    atomic_write_json(evidence / "test-case.json", proposed.to_dict())
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")


def test_real_deepseek_v4_proposes_relevant_verigrey_test(
    live_evidence_root: Path,
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the live VeriGrey contract")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "verigrey" / "deepseek-v4-flash" / run_id
    evidence.mkdir(parents=True)
    trace = evidence / "source-trace.jsonl"
    write_trace(trace)
    sequence = normalize_tool_sequence(trace)
    state = update_state({}, sequence)
    atomic_write_json(evidence / "state.json", state)
    skill_dir = evidence / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "# CSV analysis\nInspect a concrete CSV, compute every requested aggregate from "
        "all rows, and report exact numeric results.\n",
        encoding="utf-8",
    )
    skill_receipt = evidence / "skill-receipt.json"
    atomic_write_json(skill_receipt, {"source": "live fixture"})
    skill = SkillVersion(
        skill_id="live-csv-verigrey",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id="deepseek-v4-flash",
        receipt_path=skill_receipt,
    )
    base = live_config(evidence, {"proposer": 4})
    config = replace(
        base,
        experiment_id="live-verigrey-deepseek-v4",
        methods=("verigrey",),
        provider="lab",
        model_id="deepseek-v4-flash",
        output_root=evidence,
        timeouts={**base.timeouts, "pi": 240},
    )

    proposed = propose_test(state, skill, PROPERTIES, config)

    assert proposed.validation_status == "valid"
    prompt = proposed.prompt_path.read_text(encoding="utf-8")
    assert "csv" in prompt.lower()
    assert "/mnt/data" not in prompt
    assert "/tmp" not in prompt
    proposal = json.loads(proposed.proposal_receipt.read_text(encoding="utf-8"))
    pi_receipt = json.loads(Path(proposal["pi_receipt_path"]).read_text(encoding="utf-8"))
    assert pi_receipt["provider"] == "lab"
    assert pi_receipt["model"] == "deepseek-v4-flash"
    assert pi_receipt["status"] == "completed"
    assert pi_receipt["usage"]["total_tokens"] > 0
    atomic_write_json(evidence / "test-case.json", proposed.to_dict())
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
