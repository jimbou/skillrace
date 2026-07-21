from dataclasses import replace
import json
from pathlib import Path
import shutil
from typing import Any

from ..methods import random as random_method
from ..methods import skillrace as skillrace_method
from ..methods import verigrey as verigrey_method
from ..records import CheckBundle, CheckResults, ExperimentConfig, RunRecord, SkillVersion, TestCase
from ..runtime.docker import RunningContainer, remove_container
from ..storage import atomic_write_json, tree_hash
from ..verification.codex import author_checks
from ..verification.executor import execute_checks
from .part1 import run_part1
from .part2 import run_part2
from .stages import (
    accept_patch,
    build_patch_evidence,
    generate_base_skill,
    patch_skill,
    replay as exact_replay,
    run_agent,
    validate_nl_checks,
    validate_test,
)


def _root_tree() -> dict[str, Any]:
    return {
        "schema": "skillrace-reasoning-tree/1",
        "nodes": [
            {
                "node_id": "root",
                "purpose": "root",
                "outcome": "root",
                "member_run_ids": [],
                "member_episode_ids": [],
                "reach_status": "reached",
                "failure_ids": [],
            }
        ],
        "edges": [],
    }


def _case(value: TestCase) -> dict[str, Any]:
    return {
        "test_id": value.test_id,
        "case": value,
        "validation_status": value.validation_status,
        "validation_diagnostic": value.validation_diagnostic,
    }


def _seed_test(
    method: str,
    skill: SkillVersion,
    properties: list[dict[str, Any]],
    config: ExperimentConfig,
    output: Path,
) -> dict[str, Any]:
    proposed = random_method.propose_valid_test(
        skill, properties, config, output
    )
    return _case(replace(proposed, origin_method=method))


def _select_test(
    method: str,
    state: dict[str, Any],
    skill: SkillVersion,
    properties: list[dict[str, Any]],
    config: ExperimentConfig,
    output: Path,
) -> dict[str, Any]:
    if method == "random":
        if state:
            raise ValueError("Random cannot receive accumulated state")
        return _seed_test(method, skill, properties, config, output)
    if method == "verigrey":
        if not state.get("transition_counts"):
            return _seed_test(method, skill, properties, config, output)
        proposal_config = replace(config, output_root=output)
        return _case(
            verigrey_method.propose_test(
                state, skill, properties, proposal_config
            )
        )
    if method == "skillrace":
        tree = state.get("tree")
        if not isinstance(tree, dict) or skillrace_method.select_unreached_branch(tree) is None:
            return _seed_test(method, skill, properties, config, output)
        proposal_config = replace(config, output_root=output)
        return _case(
            skillrace_method.propose_test(
                tree, skill, properties, proposal_config
            )
        )
    raise ValueError(f"unknown method: {method}")


def _run(
    method: str,
    skill: SkillVersion,
    test: dict[str, Any],
    config: ExperimentConfig,
    output: Path,
) -> RunRecord:
    record = run_agent(skill, test["case"], config, output)
    if record.termination_status not in {"completed", "agent_timeout"}:
        cleanup = remove_container(
            RunningContainer(
                record.container_id,
                f"skillrace-run-{record.run_id}",
                record.image_id,
            )
        )
        atomic_write_json(
            output / "runtime" / "cleanup.json",
            {
                "success": cleanup.success,
                "removed": cleanup.removed,
                "stderr": cleanup.stderr,
            },
        )
        raise RuntimeError(
            f"{method} weak agent failed: {record.termination_status}"
        )
    return record


def _verify(
    skill: SkillVersion,
    test: TestCase,
    record: RunRecord,
    config: ExperimentConfig,
    output: Path,
) -> tuple[CheckBundle, CheckResults, dict[str, Any]]:
    workspace = output / "verifier"
    input_dir = workspace / "input"
    verifier_output = workspace / "output"
    (input_dir / "skill").mkdir(parents=True)
    (input_dir / "environment").mkdir()
    verifier_output.mkdir(parents=True)
    shutil.copy2(
        Path(__file__).parents[1] / "verification" / "GUIDE.md",
        workspace / "GUIDE.md",
    )
    shutil.copy2(skill.directory_path / "SKILL.md", input_dir / "skill" / "SKILL.md")
    shutil.copy2(test.prompt_path, input_dir / "prompt.txt")
    shutil.copytree(test.environment_directory, input_dir / "environment", dirs_exist_ok=True)
    shutil.copytree(record.artifact_path, input_dir / "artifact")
    shutil.copy2(record.trace_path, input_dir / "trace.jsonl")
    shutil.copy2(record.tool_log_path, input_dir / "tool_outputs.jsonl")
    shutil.copy2(test.nl_check_path, input_dir / "nl_checks.json")
    atomic_write_json(input_dir / "run.json", record.to_dict())
    running = RunningContainer(
        record.container_id,
        f"skillrace-run-{record.run_id}",
        record.image_id,
    )
    try:
        bundle = author_checks(workspace, config)
    except BaseException:
        cleanup = remove_container(running)
        atomic_write_json(
            output / "cleanup.json",
            {
                "success": cleanup.success,
                "removed": cleanup.removed,
                "stderr": cleanup.stderr,
            },
        )
        raise
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    results = execute_checks(
        running,
        record.artifact_path,
        bundle,
        output / "results",
    )
    return bundle, results, manifest


def _updated_state(
    method: str,
    state: dict[str, Any],
    record: RunRecord,
    results: list[dict[str, Any]],
    config: ExperimentConfig,
    output: Path,
) -> dict[str, Any]:
    if method == "random":
        return {}
    if method == "verigrey":
        sequence = verigrey_method.normalize_tool_sequence(record.trace_path)
        return verigrey_method.update_state(state, sequence) if sequence else state
    episodes, _ = skillrace_method.create_episodes(
        record, config, output / "episodes"
    )
    failures = [
        {
            "failure_id": item["check_id"],
            "episode_id": episodes[-1]["episode_id"],
        }
        for item in results
        if item.get("status") == "fail"
    ]
    tree = skillrace_method.merge_episodes(
        state.get("tree", _root_tree()),
        episodes,
        record.run_id,
        failures,
        config,
        output / "tree",
    )
    return {"episodes": episodes, "tree": tree, "branch": tree["nodes"][-1]}


def _patch(
    method: str,
    state: dict[str, Any],
    current: SkillVersion,
    test: dict[str, Any],
    record: RunRecord,
    bundle: CheckBundle,
    results: CheckResults,
    config: ExperimentConfig,
    output: Path,
) -> tuple[dict[str, Any], SkillVersion | None]:
    evidence, _ = build_patch_evidence(
        method,
        state,
        current,
        test["case"],
        record,
        bundle,
        results,
        output / "evidence",
    )
    attempt = patch_skill(
        current,
        evidence,
        method,
        config,
        output / "attempt",
    )
    receipt = json.loads(attempt.cost_receipt_path.read_text(encoding="utf-8"))
    cost = receipt.get("usage", {}).get("total_tokens", 0)
    if attempt.patch_status != "patched":
        return (
            {
                "patch_attempt_id": attempt.patch_attempt_id,
                "patch_status": attempt.patch_status,
                "model_id": attempt.model_id,
                "backend": "pi",
                "cost": cost,
            },
            None,
        )
    candidate_dir = output / "attempt" / "candidate"
    candidate = SkillVersion(
        skill_id=current.skill_id,
        version_id="candidate",
        parent_version_id=current.version_id,
        directory_path=candidate_dir,
        tree_hash=tree_hash(candidate_dir),
        creation_role="patcher",
        model_id=config.model_id,
        receipt_path=attempt.cost_receipt_path,
    )
    return (
        {
            "patch_attempt_id": attempt.patch_attempt_id,
            "candidate_skill": candidate,
            "patch_status": attempt.patch_status,
            "model_id": attempt.model_id,
            "backend": "pi",
            "cost": cost,
        },
        candidate,
    )


def run_part1_campaign(
    config: ExperimentConfig,
    s0_dir: Path,
    s0_receipt: Path,
    skill_id: str,
    property_path: Path,
    output: Path,
) -> dict[str, object]:
    if config.part != "part1":
        raise ValueError("Part I campaign requires a part1 config")
    if not (s0_dir / "SKILL.md").is_file() or not s0_receipt.is_file():
        raise ValueError("Part I S0 or its receipt is missing")
    properties = validate_nl_checks(property_path)
    s0 = SkillVersion(
        skill_id=skill_id,
        version_id="S0",
        parent_version_id=None,
        directory_path=s0_dir,
        tree_hash=tree_hash(s0_dir),
        creation_role="input",
        model_id=config.model_id,
        receipt_path=s0_receipt,
    )
    records: dict[str, RunRecord] = {}
    tests: dict[str, dict[str, Any]] = {}
    checked: dict[str, tuple[CheckBundle, CheckResults]] = {}
    run_states: dict[str, dict[str, Any]] = {}

    def propose(method, state, skill, slot, destination):
        return _select_test(
            method, state, skill, properties, config, Path(destination)
        )

    def execute(method, skill, test, slot, destination):
        record = _run(method, skill, test, config, Path(destination))
        records[record.run_id] = record
        tests[record.run_id] = test
        return {
            "run_id": record.run_id,
            "test_id": record.test_id,
            "method": method,
            "model_id": record.model_id,
            "skill_hash": skill.tree_hash,
            "cost": record.cost_totals.get("total_tokens", 0),
        }

    def check(method, run, test, destination):
        record = records[run["run_id"]]
        bundle, results, manifest = _verify(
            s0, test["case"], record, config, Path(destination)
        )
        checked[record.run_id] = (bundle, results)
        declarations = {item["check_id"]: item for item in manifest["checks"]}
        return [
            {
                **item,
                "property_group": item["property_id"],
                "failing_check_signature": f"{item['check_id']}:{item['diagnostic']}",
                "root_cause_category": declarations[item["check_id"]][
                    "root_cause_category"
                ],
            }
            for item in results.results
        ]

    def update(method, state, run, results, destination):
        updated = _updated_state(
            method,
            state,
            records[run["run_id"]],
            results,
            config,
            Path(destination),
        )
        run_states[run["run_id"]] = updated
        return updated

    def confirm(candidate, destination):
        record = records[candidate["run_id"]]
        bundle, _ = checked[record.run_id]
        replayed = exact_replay(
            s0,
            tests[record.run_id]["case"],
            bundle,
            config,
            destination,
        )
        check_id = candidate["candidate_id"].rsplit(":", 1)[-1]
        return any(
            item["check_id"] == check_id and item["status"] == "fail"
            for item in replayed.results
        )

    def patch(candidate, destination):
        record = records[candidate["run_id"]]
        bundle, results = checked[record.run_id]
        patched, candidate_skill = _patch(
            candidate["method"],
            run_states[record.run_id],
            s0,
            tests[record.run_id],
            record,
            bundle,
            results,
            config,
            Path(destination),
        )
        decision = "rejected"
        if candidate_skill is not None:
            replayed = exact_replay(
                candidate_skill,
                tests[record.run_id]["case"],
                bundle,
                config,
                Path(destination) / "replay",
            )
            decision = accept_patch(results.results, replayed.results, [])
        return {**patched, "candidate_id": candidate["candidate_id"], "decision": decision}

    return run_part1(
        s0,
        config,
        output,
        propose=propose,
        execute=execute,
        check=check,
        update_state=update,
        confirm=confirm,
        patch=patch,
    )


def _load_heldout_case(path: Path, config: ExperimentConfig) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("held-out test record must be a JSON object")
    for name in (
        "prompt_path",
        "environment_directory",
        "nl_check_path",
        "proposal_receipt",
    ):
        value = Path(raw[name])
        if not value.is_absolute():
            raw[name] = str(path.parent / value)
    pending = replace(
        TestCase.from_dict(raw),
        validation_status="pending",
        validation_diagnostic="",
        container_image_id="",
    )
    validated = validate_test(pending, config)
    if validated.validation_status != "valid":
        raise ValueError(
            f"held-out test {validated.test_id} is invalid: "
            f"{validated.validation_diagnostic}"
        )
    return _case(validated)


def run_part2_campaign(
    config: ExperimentConfig,
    scenario_path: Path,
    property_path: Path,
    heldout_paths: list[Path],
    output: Path,
) -> dict[str, object]:
    if config.part != "part2":
        raise ValueError("Part II campaign requires a part2 config")
    if not scenario_path.is_file() or not scenario_path.read_text(encoding="utf-8").strip():
        raise ValueError("Part II scenario is missing or empty")
    if not heldout_paths:
        raise ValueError("Part II requires at least one held-out test record")
    properties = validate_nl_checks(property_path)
    s0 = generate_base_skill(scenario_path, config, output.parent / "generated-s0")
    records: dict[str, RunRecord] = {}
    run_skills: dict[str, SkillVersion] = {}
    checked: dict[str, tuple[CheckBundle, CheckResults]] = {}

    def select(method, state, current, iteration, destination):
        return _select_test(
            method, state, current, properties, config, Path(destination)
        )

    def execute(method, current, test, iteration, destination):
        record = _run(method, current, test, config, Path(destination))
        records[record.run_id] = record
        run_skills[record.run_id] = current
        return {
            "run_id": record.run_id,
            "test_id": record.test_id,
            "model_id": record.model_id,
            "skill_version_id": record.skill_version_id,
            "cost": record.cost_totals.get("total_tokens", 0),
        }

    def check(method, run, test, destination):
        record = records[run["run_id"]]
        bundle, results, _ = _verify(
            run_skills[record.run_id],
            test["case"],
            record,
            config,
            Path(destination),
        )
        checked[record.run_id] = (bundle, results)
        test["bundle"] = bundle
        return {
            "check_results_id": results.results_id,
            "results": list(results.results),
            "cost": 0,
        }

    def update(method, state, run, result, destination):
        return _updated_state(
            method,
            state,
            records[run["run_id"]],
            result["results"],
            config,
            Path(destination),
        )

    def patch(method, state, current, test, run, result, destination):
        record = records[run["run_id"]]
        bundle, results = checked[record.run_id]
        patched, _ = _patch(
            method,
            state,
            current,
            test,
            record,
            bundle,
            results,
            config,
            Path(destination),
        )
        return patched

    def replay(method, candidate, test, destination):
        results = exact_replay(
            candidate,
            test["case"],
            test["bundle"],
            config,
            destination,
        )
        run_value = json.loads(
            (Path(destination) / "run" / "run.json").read_text(encoding="utf-8")
        )
        return {
            "check_results_id": results.results_id,
            "results": list(results.results),
            "cost": run_value["cost_totals"].get("total_tokens", 0),
        }

    def load_heldout():
        return [_load_heldout_case(path, config) for path in heldout_paths]

    def evaluate(label, skill, test, repetition, destination):
        record = _run(label, skill, test, config, Path(destination) / "run")
        _, results, _ = _verify(
            skill,
            test["case"],
            record,
            config,
            Path(destination) / "checks",
        )
        return {
            "run_id": record.run_id,
            "model_id": record.model_id,
            "passed": all(item["status"] == "pass" for item in results.results),
            "cost": record.cost_totals.get("total_tokens", 0),
            "results_id": results.results_id,
        }

    return run_part2(
        s0,
        config,
        output,
        select=select,
        execute=execute,
        check=check,
        update_state=update,
        patch=patch,
        replay=replay,
        load_heldout=load_heldout,
        evaluate=evaluate,
    )
