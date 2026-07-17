from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import uuid

import pytest

from skillrace_next.records import ExperimentConfig
from skillrace_next.storage import atomic_write_json, tree_hash
from skillrace_next.verification.codex import author_checks, command_invokes_docker


pytestmark = pytest.mark.live


def successful_task_run() -> Path:
    root = Path("out/live-contracts/task-runner")
    for candidate in sorted(root.iterdir(), reverse=True) if root.is_dir() else []:
        receipt_path = candidate / "runtime" / "exec.json"
        if not receipt_path.is_file():
            continue
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (
            receipt.get("exit_code") == 0
            and receipt.get("model") == "deepseek-v3.2"
            and (candidate / "artifact" / "task-result.txt").is_file()
            and (candidate / "runtime" / "trace.jsonl").is_file()
        ):
            return candidate
    pytest.fail("a successful real Yunwu task-runner artifact is required")


def test_real_codex_authors_checks_without_mutating_yunwu_run_inputs(
    live_evidence_root: Path,
) -> None:
    secret = os.environ.get("yunwu_key")
    if not secret:
        pytest.skip("yunwu_key is required to bind the real Yunwu prerequisite")
    source_run = successful_task_run()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "codex-verifier" / run_id
    workspace = evidence / "verifier_workspace"
    input_dir = workspace / "input"
    output = workspace / "output"
    (input_dir / "skill").mkdir(parents=True)
    (input_dir / "environment").mkdir()
    output.mkdir(parents=True)
    shutil.copy2("skillrace_next/verification/GUIDE.md", workspace / "GUIDE.md")
    (input_dir / "skill" / "SKILL.md").write_text(
        "# Exact task marker\n"
        "Write the exact marker requested by the user and read it back before stopping.\n",
        encoding="utf-8",
    )
    (input_dir / "prompt.txt").write_text(
        "Create /workspace/task-result.txt containing exactly "
        "SKILLRACE_TASK_AGENT_OK with no surrounding whitespace, then read it back.\n",
        encoding="utf-8",
    )
    shutil.copy2(
        "tests_next/fixtures/task/Dockerfile",
        input_dir / "environment" / "Dockerfile",
    )
    shutil.copytree(source_run / "artifact", input_dir / "artifact")
    shutil.copy2(source_run / "runtime" / "trace.jsonl", input_dir / "trace.jsonl")
    shutil.copy2(
        source_run / "runtime" / "tool_outputs.jsonl",
        input_dir / "tool_outputs.jsonl",
    )
    artifact_hash = tree_hash(input_dir / "artifact")
    atomic_write_json(
        input_dir / "run.json",
        {
            "run_id": f"codex-verifier-{run_id}",
            "source_task_run": str(source_run),
            "model": "deepseek-v3.2",
            "artifact_hash": artifact_hash,
        },
    )
    atomic_write_json(
        input_dir / "nl_checks.json",
        [
            {
                "property_id": "P1",
                "description": "The final artifact contains task-result.txt.",
            },
            {
                "property_id": "P2",
                "description": "task-result.txt contains exactly SKILLRACE_TASK_AGENT_OK "
                "with no surrounding whitespace.",
            },
        ],
    )
    input_hash_before = tree_hash(input_dir)
    config = ExperimentConfig(
        experiment_id="live-codex-verifier",
        part="part1",
        methods=("random",),
        replicate_count=1,
        provider="yunwu",
        model_id="deepseek-v3.2",
        pi_version="0.73.1",
        role_budgets={"proposer": 4, "weak_agent": 4, "patcher": 6},
        verifier_backend="codex",
        verifier_command=("codex", "exec"),
        verifier_model="gpt-5.6-terra",
        verifier_reasoning="medium",
        docker_image="skillrace-next/task-fixture:test",
        resource_limits={"cpus": "1", "memory_mb": 512},
        network_policy="none",
        timeouts={
            "provider": 60,
            "pi": 180,
            "docker": 180,
            "codex": 300,
            "check": 60,
            "patch": 300,
        },
        suite_path=evidence,
        scenario_path=evidence,
        iteration_budget=1,
        live=True,
        output_root=evidence,
        heldout_repetitions=1,
    )

    bundle = author_checks(workspace, config)

    assert tree_hash(input_dir) == input_hash_before
    assert bundle.artifact_hash == artifact_hash
    assert bundle.script_paths
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    covered = {check["property_id"] for check in manifest["checks"]}
    uncovered = {item["property_id"] for item in manifest["uncovered"]}
    assert covered | uncovered == {"P1", "P2"}
    assert covered == {"P1", "P2"}
    assert all(check["purpose"].strip() for check in manifest["checks"])
    assert all(check["pass_condition"].strip() for check in manifest["checks"])
    events = [
        json.loads(line)
        for line in bundle.codex_receipt_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    command_texts: list[str] = []
    for event in events:
        item = event.get("item") if isinstance(event, dict) else None
        if isinstance(item, dict) and item.get("type") == "command_execution":
            command = item.get("command")
            if isinstance(command, str):
                command_texts.append(command)
    assert all(not command_invokes_docker(command) for command in command_texts)
    assert all(source_run.name not in command for command in command_texts)
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
