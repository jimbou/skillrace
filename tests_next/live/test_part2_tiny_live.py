from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

from skillrace_next.methods.skillrace import create_episodes, merge_episodes
from skillrace_next.methods.verigrey import normalize_tool_sequence, update_state as update_verigrey
from skillrace_next.pipeline.part2 import run_part2
from skillrace_next.pipeline.stages import (
    build_patch_evidence,
    generate_base_skill,
    patch_skill,
    replay as exact_replay,
    run_agent,
    validate_test,
)
from skillrace_next.records import CheckBundle, SkillVersion, TestCase as CaseRecord
from skillrace_next.runtime.docker import RunningContainer
from skillrace_next.storage import atomic_write_json, canonical_json_hash, file_hash, tree_hash
from skillrace_next.verification.executor import execute_checks
from tests_next.live.test_tree_merge_live import live_config


pytestmark = pytest.mark.live


def root_tree() -> dict[str, object]:
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


def make_case(
    evidence: Path,
    config,
    name: str,
    method: str,
    values: tuple[int, ...],
    expected: str,
    *,
    forced_failure: bool = False,
) -> dict[str, object]:
    case = evidence / "development-tests" / name
    environment = case / "environment"
    environment.mkdir(parents=True)
    (environment / "values.txt").write_text(
        "".join(f"{value}\n" for value in values), encoding="utf-8"
    )
    (environment / "Dockerfile").write_text(
        f"FROM {config.docker_image}\nCOPY values.txt /input/values.txt\nWORKDIR /workspace\n",
        encoding="utf-8",
    )
    atomic_write_json(environment / "sanity.json", {"status": "pass"})
    prompt = case / "prompt.txt"
    prompt.write_text(
        "Using the installed skill, read the integers in /input/values.txt, calculate "
        "their median, and write only the resulting number with no surrounding whitespace to "
        "/workspace/result.txt. Read the result back before stopping.\n",
        encoding="utf-8",
    )
    checks = case / "nl_checks.json"
    description = (
        "This bounded rejection-control property deliberately remains failing so a "
        "nonrepair is rejected."
        if forced_failure
        else f"result.txt contains exactly the standard mathematical median {expected}."
    )
    atomic_write_json(checks, [{"property_id": "P1", "description": description}])
    proposal = case / "proposal.json"
    atomic_write_json(proposal, {"source": "predefined Part II fixture", "method": method})
    pending = CaseRecord(
        test_id=name,
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
    validated = validate_test(pending, config)
    assert validated.validation_status == "valid"
    script = case / "P1-C1.py"
    if forced_failure:
        script.write_text(
            "import json, sys\n"
            "print(json.dumps({'diagnostic': 'bounded rejection control remains failing', "
            "'evidence_paths': []}))\n"
            "sys.exit(1)\n",
            encoding="utf-8",
        )
    else:
        failure_hint = (
            "; the lower-middle convention is wrong for this even-count task"
            if name == "random-standard-even"
            else ""
        )
        script.write_text(
            "import json, pathlib, sys\n"
            f"expected = {repr(expected.encode())}\n"
            f"failure_hint = {failure_hint!r}\n"
            "path = pathlib.Path(sys.argv[1]) / 'result.txt'\n"
            "actual = path.read_bytes() if path.is_file() else None\n"
            "ok = actual == expected\n"
            "diagnostic = ('exact median bytes match' if ok else "
            "f'expected exact median bytes {expected!r}, observed {actual!r}{failure_hint}')\n"
            "print(json.dumps({'diagnostic': diagnostic, "
            "'evidence_paths': ['result.txt'] if path.is_file() else []}))\n"
            "sys.exit(0 if ok else 1)\n",
            encoding="utf-8",
        )
    receipt = case / "predefined-check-receipt.jsonl"
    receipt.write_text(
        json.dumps({"source": "predefined", "codex_used": False}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "test_id": name,
        "case": validated,
        "script": script,
        "receipt": receipt,
        "forced_failure": forced_failure,
    }


def bind_bundle(test: dict[str, object], run, output: Path) -> CheckBundle:
    checks_dir = output / "checks"
    checks_dir.mkdir(parents=True)
    copied_script = checks_dir / "P1-C1.py"
    shutil.copy2(test["script"], copied_script)
    manifest = {
        "schema": "skillrace-check-bundle/1",
        "run_id": run.run_id,
        "artifact_hash": run.artifact_hash,
        "checks": [
            {
                "check_id": "P1-C1",
                "property_id": "P1",
                "script": "checks/P1-C1.py",
                "argv": ["python3", "checks/P1-C1.py", "/workspace"],
                "timeout_seconds": 10,
                "purpose": "Execute the frozen Part II property check.",
                "pass_condition": "The predefined property is satisfied.",
                "failure_condition": "The predefined property is not satisfied.",
                "root_cause_category": "validation_missing",
            }
        ],
        "uncovered": [],
    }
    manifest_path = output / "check_manifest.json"
    atomic_write_json(manifest_path, manifest)
    return CheckBundle(
        bundle_id="bundle-" + canonical_json_hash(manifest),
        run_id=run.run_id,
        artifact_hash=run.artifact_hash,
        input_hashes={
            "artifact": run.artifact_hash,
            "nl_checks": file_hash(test["case"].nl_check_path),
        },
        manifest_path=manifest_path,
        script_paths=(copied_script,),
        codex_receipt_path=test["receipt"],
    )


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "qwen3.6-flash"])
def test_tiny_real_part2_carries_accepted_skill_and_discards_rejected_patch(
    model: str, live_evidence_root: Path,
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the tiny Part II contract")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "part2" / model / run_id
    evidence.mkdir(parents=True)
    config = replace(
        live_config(
            evidence,
            {
                "skill_generator": 6,
                "weak_agent": 4,
                "patcher": 10,
                "segmenter": 4,
                "tree_alignment": 4,
            },
        ),
        experiment_id="tiny-real-part2",
        part="part2",
        methods=("random", "verigrey", "skillrace"),
        iteration_budget=2,
        heldout_repetitions=1,
        network_policy="host",
        provider="lab",
        model_id=model,
        timeouts={
            **live_config(evidence, {}).timeouts,
            "pi": 240,
            "patch": 240,
        },
    )
    image_id = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", config.docker_image],
        check=True,
        capture_output=True,
        text=True,
        timeout=config.timeouts["docker"],
    ).stdout.strip()
    atomic_write_json(
        evidence / "docker-preflight.json",
        {"base_image": config.docker_image, "base_image_id": image_id},
    )
    scenario = evidence / "scenario.md"
    scenario.write_text(
        "Create a median-calculation skill for a project whose initial convention is: "
        "sort numeric values; for odd counts use the middle value; for even counts use "
        "the lower of the two middle values rather than averaging. Always write only the "
        "number requested by the task and verify the file.\n",
        encoding="utf-8",
    )
    s0 = generate_base_skill(scenario, config, evidence / "generated-s0")
    initial_skill = (s0.directory_path / "SKILL.md").read_text(encoding="utf-8")
    assert "median" in initial_skill.lower()

    cases = {
        "random": [
            make_case(
                evidence,
                config,
                "random-standard-even",
                "random",
                (1, 2, 100, 200),
                "51",
            ),
            make_case(
                evidence,
                config,
                "random-rejection-control",
                "random",
                (1, 5, 9),
                "5",
                forced_failure=True,
            ),
        ],
        "verigrey": [
            make_case(evidence, config, "verigrey-odd-1", "verigrey", (1, 5, 9), "5"),
            make_case(evidence, config, "verigrey-odd-2", "verigrey", (3, 7, 11), "7"),
        ],
        "skillrace": [
            make_case(evidence, config, "skillrace-odd-1", "skillrace", (2, 6, 10), "6"),
            make_case(evidence, config, "skillrace-odd-2", "skillrace", (4, 8, 12), "8"),
        ],
    }
    records = {}
    checked_records = {}
    patch_attempts = []
    heldout_created = evidence / "heldout-tests"

    def select(method, state, current, iteration, output):
        assert not heldout_created.exists()
        return cases[method][iteration]

    def execute(method, current, test, iteration, output):
        record = run_agent(current, test["case"], config, output)
        records[record.run_id] = record
        return {
            "run_id": record.run_id,
            "test_id": record.test_id,
            "model_id": record.model_id,
            "skill_version_id": record.skill_version_id,
            "cost": record.cost_totals.get("total_tokens", 0),
        }

    def check(method, run, test, output):
        record = records[run["run_id"]]
        bundle = bind_bundle(test, record, Path(output) / "bundle")
        test["bundle"] = bundle
        results = execute_checks(
            RunningContainer(record.container_id, f"part2-{method}", record.image_id),
            record.artifact_path,
            bundle,
            Path(output) / "results",
        )
        checked_records[record.run_id] = (bundle, results)
        return {
            "check_results_id": results.results_id,
            "results": list(results.results),
            "cost": 0,
        }

    def update(method, state, run, checked, output):
        record = records[run["run_id"]]
        if method == "random":
            return {}
        if method == "verigrey":
            return update_verigrey(state, normalize_tool_sequence(record.trace_path))
        episodes, receipt = create_episodes(record, config, Path(output) / "episodes")
        failures = [
            {"failure_id": item["check_id"], "episode_id": episodes[-1]["episode_id"]}
            for item in checked["results"]
            if item["status"] == "fail"
        ]
        tree = merge_episodes(
            state.get("tree", root_tree()),
            episodes,
            record.run_id,
            failures,
            config,
            Path(output) / "tree",
        )
        return {
            "episodes": episodes,
            "tree": tree,
            "branch": tree["nodes"][-1],
        }

    def patch(method, state, current, test, run, checked, output):
        record = records[run["run_id"]]
        bundle, results = checked_records[record.run_id]
        patch_evidence, _ = build_patch_evidence(
            method,
            state,
            current,
            test["case"],
            record,
            bundle,
            results,
            Path(output) / "evidence",
        )
        attempt = patch_skill(
            current,
            patch_evidence,
            method,
            config,
            Path(output) / "attempt",
        )
        patch_attempts.append(attempt)
        candidate_dir = Path(output) / "attempt" / "candidate"
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
        receipt = json.loads(attempt.cost_receipt_path.read_text(encoding="utf-8"))
        return {
            "patch_attempt_id": attempt.patch_attempt_id,
            "candidate_skill": candidate,
            "patch_status": attempt.patch_status,
            "model_id": attempt.model_id,
            "backend": "pi",
            "cost": receipt.get("usage", {}).get("total_tokens", 0),
        }

    def replay(method, candidate, test, output):
        results = exact_replay(
            candidate,
            test["case"],
            test["bundle"],
            config,
            output,
        )
        run_value = json.loads((Path(output) / "run" / "run.json").read_text())
        return {
            "check_results_id": results.results_id,
            "results": list(results.results),
            "cost": run_value["cost_totals"].get("total_tokens", 0),
        }

    def load_heldout():
        return [
            make_case(
                evidence,
                config,
                "heldout-standard-even",
                "heldout",
                (10, 20),
                "15",
            )
        ]

    def evaluate(label, skill, test, repetition, output):
        record = run_agent(skill, test["case"], config, Path(output) / "run")
        bundle = bind_bundle(test, record, Path(output) / "bundle")
        results = execute_checks(
            RunningContainer(record.container_id, f"part2-heldout-{label}", record.image_id),
            record.artifact_path,
            bundle,
            Path(output) / "results",
        )
        return {
            "run_id": record.run_id,
            "model_id": record.model_id,
            "passed": all(item["status"] == "pass" for item in results.results),
            "cost": record.cost_totals.get("total_tokens", 0),
            "results_id": results.results_id,
        }

    result = run_part2(
        s0,
        config,
        evidence / "campaign",
        select=select,
        execute=execute,
        check=check,
        update_state=update,
        patch=patch,
        replay=replay,
        load_heldout=load_heldout,
        evaluate=evaluate,
    )

    random_steps = [step for step in result["steps"] if step["method"] == "random"]
    assert [step["decision"] for step in random_steps] == ["accepted", "rejected"]
    assert random_steps[1]["input_skill_version_id"] == "S1"
    assert random_steps[1]["resulting_skill_version_id"] == "S1"
    assert result["final_skills"]["random"]["version_id"] == "S1"
    assert result["final_skills"]["verigrey"]["version_id"] == "S0"
    assert result["final_skills"]["skillrace"]["version_id"] == "S0"
    assert len(result["steps"]) == 6
    assert len(result["heldout_evaluations"]) == 4
    assert [row["method"] for row in result["heldout_evaluations"]] == [
        "s0",
        "random",
        "verigrey",
        "skillrace",
    ]
    assert len(patch_attempts) == 2
    assert all(attempt.model_id == model for attempt in patch_attempts)
    assert not list(evidence.rglob("codex-events.jsonl"))
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
