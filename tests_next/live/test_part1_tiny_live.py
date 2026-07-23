from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import uuid

import pytest

from skillrace_next.methods.episodes import create_episodes
from skillrace_next.methods.reasoning_tree import empty_tree, merge_episodes
from skillrace_next.methods.verigrey import normalize_tool_sequence, update_state as update_verigrey
from skillrace_next.pipeline.part1 import run_part1
from skillrace_next.pipeline.stages import run_agent, validate_test
from skillrace_next.records import SkillVersion, TestCase as CaseRecord
from skillrace_next.runtime.docker import RunningContainer
from skillrace_next.storage import atomic_write_json, file_hash, tree_hash
from skillrace_next.verification.codex import author_checks
from skillrace_next.verification.executor import execute_checks
from tests_next.live.test_tree_merge_live import live_config


pytestmark = pytest.mark.live


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "qwen3.6-flash"])
def test_tiny_real_part1_runs_each_method_once_before_any_repair(
    model: str, live_evidence_root: Path,
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the tiny Part I contract")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "part1" / model / run_id
    evidence.mkdir(parents=True)
    base_config = live_config(evidence, model)
    config = replace(
        base_config,
        experiment_id="tiny-real-part1",
        methods=("random", "verigrey", "skillrace"),
        iteration_budget=1,
        network_policy="host",
        provider="lab",
        model_id=model,
        role_budgets={
            "weak_agent": 4,
            "segmenter": 4,
            "tree_alignment": 4,
            "patcher": 6,
        },
    )
    assert config.model_id == model
    assert config.role_budgets["weak_agent"] == 4
    skill_dir = evidence / "s0"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: exact-marker\ndescription: Write and verify exact requested markers.\n---\n"
        "# Exact marker\nWrite the exact requested content, read it back, and correct any mismatch.\n",
        encoding="utf-8",
    )
    skill_receipt = evidence / "s0-receipt.json"
    atomic_write_json(skill_receipt, {"source": "tiny live S0"})
    s0 = SkillVersion(
        skill_id="tiny-live-s0",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id=model,
        receipt_path=skill_receipt,
    )
    tests = {}
    for method in config.methods:
        case = evidence / "tests" / method
        environment = case / "environment"
        environment.mkdir(parents=True)
        (environment / "Dockerfile").write_text(
            f"FROM {config.docker_image}\nWORKDIR /workspace\n", encoding="utf-8"
        )
        atomic_write_json(environment / "sanity.json", {"status": "pass"})
        prompt = case / "prompt.txt"
        prompt.write_text(
            "Create /workspace/result.txt containing exactly PART1_LIVE_OK with no "
            "surrounding whitespace, then read the file back before stopping.\n",
            encoding="utf-8",
        )
        checks = case / "nl_checks.json"
        atomic_write_json(
            checks,
            [{"property_id": "P1", "description": "result.txt contains exactly PART1_LIVE_OK."}],
        )
        proposal = case / "proposal.json"
        atomic_write_json(
            proposal,
            {
                "schema": "skillrace-generated-test-proposal/1",
                "method": method,
                "source": "tiny live fixture",
                "catalog_hash": file_hash(checks),
                "prompt_hash": file_hash(prompt),
                "environment_hash": tree_hash(environment),
            },
        )
        pending = CaseRecord(
            test_id=f"tiny-{method}",
            prompt_path=prompt,
            prompt_hash=file_hash(prompt),
            environment_directory=environment,
            environment_hash=tree_hash(environment),
            nl_check_path=checks,
            nl_check_hash=file_hash(checks),
            origin_method=method,
            proposal_receipt=proposal,
            validation_status="pending",
            validation_diagnostic="",
            container_image_id="",
        )
        tests[method] = validate_test(pending, config)
        assert tests[method].validation_status == "valid"
    records = {}
    results_by_run = {}
    discovery_complete = []

    def propose(method, state, skill, slot, output):
        return tests[method]

    def execute(method, skill, test, slot, output):
        record = run_agent(skill, test, config, output)
        records[record.run_id] = record
        discovery_complete.append(method)
        return {
            "run_id": record.run_id,
            "test_id": record.test_id,
            "method": method,
            "model_id": record.model_id,
            "skill_hash": skill.tree_hash,
            "cost": record.cost_totals.get("total_tokens", 0),
        }

    def check(method, run, test, output):
        record = records[run["run_id"]]
        workspace = Path(output) / "verifier"
        input_dir = workspace / "input"
        verifier_output = workspace / "output"
        (input_dir / "skill").mkdir(parents=True)
        (input_dir / "environment").mkdir()
        verifier_output.mkdir(parents=True)
        shutil.copy2("skillrace_next/verification/GUIDE.md", workspace / "GUIDE.md")
        shutil.copy2(skill_dir / "SKILL.md", input_dir / "skill" / "SKILL.md")
        shutil.copy2(test.prompt_path, input_dir / "prompt.txt")
        shutil.copytree(test.environment_directory, input_dir / "environment", dirs_exist_ok=True)
        shutil.copytree(record.artifact_path, input_dir / "artifact")
        shutil.copy2(record.trace_path, input_dir / "trace.jsonl")
        shutil.copy2(record.tool_log_path, input_dir / "tool_outputs.jsonl")
        atomic_write_json(input_dir / "run.json", record.to_dict())
        shutil.copy2(test.nl_check_path, input_dir / "nl_checks.json")
        bundle = author_checks(workspace, config)
        manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
        by_id = {item["check_id"]: item for item in manifest["checks"]}
        checked = execute_checks(
            RunningContainer(record.container_id, f"part1-{method}", record.image_id),
            record.artifact_path,
            bundle,
            Path(output) / "results",
        )
        results_by_run[record.run_id] = checked
        enriched = []
        for item in checked.results:
            declaration = by_id[item["check_id"]]
            enriched.append(
                {
                    **item,
                    "property_group": item["property_id"],
                    "failing_check_signature": f"{item['check_id']}:{item['diagnostic']}",
                    "root_cause_category": declaration["root_cause_category"],
                }
            )
        return enriched

    def update(method, state, run, results, output):
        record = records[run["run_id"]]
        if method == "random":
            return {"observed_run_ids": [record.run_id]}
        if method == "verigrey":
            sequence = normalize_tool_sequence(record.trace_path)
            updated = update_verigrey({}, sequence)
            updated["observed_run_ids"] = [record.run_id]
            return updated
        episodes, receipt = create_episodes(record, config, Path(output) / "episodes")
        tree, merge_cache = merge_episodes(
            empty_tree(),
            episodes,
            record.run_id,
            [],
            {},
            config,
            Path(output) / "tree",
            run_meta={
                "trace_path": str(record.trace_path),
                "artifact_path": str(record.artifact_path),
            },
        )
        return {
            "observed_run_ids": [record.run_id],
            "episodes": episodes,
            "tree": tree,
            "tree_merge_cache": merge_cache,
            "episode_receipt_path": str(receipt),
        }

    def confirm(candidate, output):
        assert discovery_complete == list(config.methods)
        return False

    def forbidden_patch(candidate, output):
        raise AssertionError("no unconfirmed candidate may be patched")

    result = run_part1(
        s0,
        config,
        evidence / "campaign",
        propose=propose,
        execute=execute,
        check=check,
        update_state=update,
        confirm=confirm,
        patch=forbidden_patch,
    )

    assert discovery_complete == list(config.methods)
    assert len(records) == 3
    assert all(record.model_id == model for record in records.values())
    assert all(record.skill_version_id == "S0" for record in records.values())
    assert len(results_by_run) == 3
    assert len(result["patches"]) == 0
    for method in config.methods:
        state = json.loads((evidence / "campaign" / "methods" / method / "state.json").read_text())
        assert len(state["observed_run_ids"]) == 1
        if method == "skillrace":
            assert state["episodes"]
            assert state["tree"]["schema"] == "behavior-tree/2"
            assert isinstance(state["tree_merge_cache"], dict)
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
