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
from ..storage import atomic_write_json, canonical_json_hash, file_hash, tree_hash
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
        if not state:
            state.update(
                verigrey_method.initialize_corpus(
                    skill,
                    properties,
                    config,
                    output / "initial-corpus",
                )
            )
        return _case(
            verigrey_method.select_test(
                state,
                skill,
                properties,
                config,
                output / "proposal",
            )
        )
    if method == "skillrace":
        if not state:
            plan = skillrace_method.create_diversity_plan(
                skill,
                properties,
                config,
                output / "diversity-plan",
            )
            state.update(
                {
                    "schema": "skillrace-campaign-state/1",
                    "phase": "initial_seeds",
                    "execution_count": 0,
                    "plan": plan,
                    "tree": _root_tree(),
                    "current_selection": None,
                    "observations": [],
                }
            )
        if state.get("schema") != "skillrace-campaign-state/1":
            raise ValueError("SkillRACE campaign state is invalid")
        if state.get("current_selection") is not None:
            raise ValueError("SkillRACE selection has not been observed")
        execution_count = state["execution_count"]
        if execution_count >= config.iteration_budget:
            raise ValueError("SkillRACE execution budget exhausted")
        if execution_count < 10:
            description = state["plan"]["descriptions"][execution_count]
            proposed = skillrace_method.materialize_initial_test(
                state["plan"],
                execution_count,
                skill,
                properties,
                config,
                output / "initial-seed",
            )
            if proposed.validation_status == "valid":
                state["current_selection"] = {
                    "phase": "initial_seed",
                    "seed_index": execution_count + 1,
                    "seed_id": description["seed_id"],
                    "test_id": proposed.test_id,
                }
            return _case(proposed)
        if state.get("phase") != "branch":
            raise ValueError("SkillRACE branch phase is not active")
        tree = state["tree"]
        proposal_config = replace(config, output_root=output)
        proposed = skillrace_method.propose_test(
            tree, skill, properties, proposal_config
        )
        if proposed.validation_status == "valid":
            receipt = json.loads(proposed.proposal_receipt.read_text(encoding="utf-8"))
            target_edge_id = receipt.get("target_edge_id")
            if not isinstance(target_edge_id, str) or not target_edge_id:
                raise ValueError("SkillRACE proposal receipt lacks its selected edge")
            state["current_selection"] = {
                "phase": "branch",
                "target_edge_id": target_edge_id,
                "test_id": proposed.test_id,
            }
        return _case(proposed)
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
        return verigrey_method.observe_execution(state, sequence)
    if state.get("schema") != "skillrace-campaign-state/1":
        raise ValueError("SkillRACE campaign state is invalid")
    selection = state.get("current_selection")
    if not isinstance(selection, dict):
        raise ValueError("SkillRACE execution has no current selection")
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
    execution_count = state["execution_count"] + 1
    observation = {
        "execution": execution_count,
        "phase": selection["phase"],
        "test_id": selection["test_id"],
        "run_id": record.run_id,
        "episode_ids": [episode["episode_id"] for episode in episodes],
    }
    if selection["phase"] == "initial_seed":
        observation.update(
            {
                "seed_index": selection["seed_index"],
                "seed_id": selection["seed_id"],
            }
        )
    else:
        observation["target_edge_id"] = selection["target_edge_id"]
    return {
        **state,
        "phase": "branch" if execution_count >= 10 else "initial_seeds",
        "execution_count": execution_count,
        "tree": tree,
        "current_selection": None,
        "observations": [*state["observations"], observation],
    }


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
    receipt = json.loads(validated.proposal_receipt.read_text(encoding="utf-8"))
    if receipt.get("schema") != "skillrace-part2-heldout-receipt/1":
        raise ValueError("held-out receipt schema is invalid")
    nl_checks = validate_nl_checks(validated.nl_check_path)
    property_ids = [item["property_id"] for item in nl_checks]
    property_source = receipt.get("property_source")
    source_checks = receipt.get("source_checks")
    included_property_ids = (
        property_source.get("included_property_ids")
        if isinstance(property_source, dict)
        else None
    )
    if (
        not isinstance(included_property_ids, list)
        or len(included_property_ids) != len(property_ids)
        or not all(isinstance(value, str) and value for value in included_property_ids)
        or not isinstance(source_checks, list)
        or not source_checks
    ):
        raise ValueError("held-out source checks do not match NL properties")
    frozen_checks: list[dict[str, Any]] = []
    receipt_root = validated.proposal_receipt.parent.resolve()
    for source in source_checks:
        if not isinstance(source, dict):
            raise ValueError("held-out source check record is invalid")
        criterion_id = source.get("criterion_id")
        prepared_path = source.get("prepared_path")
        prepared_hash = source.get("prepared_hash")
        if not all(
            isinstance(value, str) and value
            for value in (criterion_id, prepared_path, prepared_hash)
        ):
            raise ValueError("held-out source check provenance is incomplete")
        script = (receipt_root / prepared_path).resolve()
        if not script.is_relative_to(receipt_root) or not script.is_file():
            raise ValueError("held-out source check path is invalid")
        if file_hash(script) != prepared_hash:
            raise ValueError("held-out source check hash differs")
        frozen_checks.append(
            {
                "criterion_id": criterion_id,
                "script_path": script,
                "script_hash": prepared_hash,
            }
        )
    loaded = _case(validated)
    loaded["property_ids"] = property_ids
    loaded["predefined_checks"] = frozen_checks
    loaded["source_receipt_hash"] = file_hash(validated.proposal_receipt)
    return loaded


def _bind_heldout_bundle(
    test: dict[str, Any],
    record: RunRecord,
    config: ExperimentConfig,
    output: Path,
) -> CheckBundle:
    predefined = test.get("predefined_checks")
    if not isinstance(predefined, list) or not predefined:
        raise ValueError("held-out test has no predefined checks")
    scripts_dir = output / "checks"
    scripts_dir.mkdir(parents=True)
    manifest_checks: list[dict[str, Any]] = []
    script_paths: list[Path] = []
    source_hashes: dict[str, str] = {}
    copied_source_names: list[str] = []
    for index, source in enumerate(predefined, 1):
        copied_source = scripts_dir / f"source-check-{index}.sh"
        shutil.copyfile(source["script_path"], copied_source)
        if file_hash(copied_source) != source["script_hash"]:
            raise ValueError("copied held-out source check hash differs")
        script_paths.append(copied_source)
        copied_source_names.append(copied_source.name)
        source_hashes[f"source_check_{index}"] = source["script_hash"]
    property_ids = test.get("property_ids")
    if not isinstance(property_ids, list) or not property_ids:
        raise ValueError("held-out test has no declared properties")
    for property_id in property_ids:
        check_id = f"{property_id}-C1"
        wrapper = scripts_dir / f"{check_id}.py"
        wrapper.write_text(
            "import json\n"
            "import os\n"
            "from pathlib import Path\n"
            "import shutil\n"
            "import subprocess\n"
            "import tempfile\n"
            f"source_names = {copied_source_names!r}\n"
            "details = []\n"
            "failed = False\n"
            "for source_name in source_names:\n"
            "    source = Path(__file__).with_name(source_name)\n"
            "    check_root = Path(tempfile.mkdtemp(\n"
            f"        prefix={check_id + '-'!r}, dir=os.environ['TMPDIR']\n"
            "    ))\n"
            "    workspace = check_root / 'workspace'\n"
            "    shutil.copytree('/workspace', workspace)\n"
            "    workspace.chmod(workspace.stat().st_mode | 0o700)\n"
            "    for path in workspace.rglob('*'):\n"
            "        path.chmod(path.stat().st_mode | "
            "(0o700 if path.is_dir() else 0o200))\n"
            "    adapted = check_root / source_name\n"
            "    adapted.write_text(\n"
            "        source.read_text(encoding='utf-8').replace(\n"
            "            '/workspace', str(workspace)\n"
            "        ),\n"
            "        encoding='utf-8',\n"
            "    )\n"
            "    completed = subprocess.run(\n"
            "        ['bash', str(adapted)], cwd=workspace, "
            "capture_output=True, text=True\n"
            "    )\n"
            "    detail = (completed.stdout + completed.stderr).strip()\n"
            "    details.append(f'{source_name}: exit {completed.returncode}' + "
            "(f'\\n{detail}' if detail else ''))\n"
            "    failed = failed or completed.returncode != 0\n"
            "diagnostic = '\\n'.join(details)[-4000:]\n"
            "print(json.dumps({'diagnostic': diagnostic, 'evidence_paths': []}, "
            "sort_keys=True))\n"
            "raise SystemExit(1 if failed else 0)\n",
            encoding="utf-8",
        )
        script_paths.append(wrapper)
        manifest_checks.append(
            {
                "check_id": check_id,
                "property_id": property_id,
                "script": f"checks/{wrapper.name}",
                "argv": ["python3", f"checks/{wrapper.name}", "/workspace"],
                "timeout_seconds": config.timeouts["check"],
                "purpose": (
                    "Apply the complete frozen held-out checker set to "
                    f"property {property_id}."
                ),
                "pass_condition": "The frozen source check exits zero.",
                "failure_condition": "The frozen source check exits nonzero.",
                "root_cause_category": "validation_missing",
            }
        )
    manifest = {
        "schema": "skillrace-check-bundle/1",
        "run_id": record.run_id,
        "artifact_hash": record.artifact_hash,
        "checks": manifest_checks,
        "uncovered": [],
    }
    manifest_path = output / "check_manifest.json"
    atomic_write_json(manifest_path, manifest)
    receipt_path = output / "predefined-check-receipt.jsonl"
    atomic_write_json(
        receipt_path,
        {
            "source": "frozen Part II held-out receipt",
            "source_receipt_hash": test["source_receipt_hash"],
            "source_check_hashes": source_hashes,
            "workspace_mode": "disposable-copy-with-workspace-path-rebinding",
            "codex_used": False,
        },
    )
    return CheckBundle(
        bundle_id="bundle-" + canonical_json_hash(manifest),
        run_id=record.run_id,
        artifact_hash=record.artifact_hash,
        input_hashes={
            "artifact": record.artifact_hash,
            "nl_checks": file_hash(test["case"].nl_check_path),
            "source_receipt": test["source_receipt_hash"],
            **source_hashes,
        },
        manifest_path=manifest_path,
        script_paths=tuple(script_paths),
        codex_receipt_path=receipt_path,
    )


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
        destination = Path(destination)
        record = _run(label, skill, test, config, destination / "run")
        running = RunningContainer(
            record.container_id,
            f"skillrace-run-{record.run_id}",
            record.image_id,
        )
        try:
            bundle = _bind_heldout_bundle(
                test, record, config, destination / "checks" / "bundle"
            )
        except BaseException:
            cleanup = remove_container(running)
            atomic_write_json(
                destination / "checks" / "cleanup.json",
                {
                    "success": cleanup.success,
                    "removed": cleanup.removed,
                    "stderr": cleanup.stderr,
                },
            )
            raise
        atomic_write_json(
            destination / "checks" / "check-bundle.json", bundle.to_dict()
        )
        results = execute_checks(
            running,
            record.artifact_path,
            bundle,
            destination / "checks" / "results",
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
