from dataclasses import replace
from pathlib import Path
import shutil
from typing import Any

from ..analysis.part2 import summarize_part2
from ..pipeline.stages import accept_patch
from ..records import ExperimentConfig, ImprovementStep, SkillVersion
from ..storage import atomic_write_json, tree_hash


def _copy_skill(
    source: SkillVersion,
    directory: Path,
    version_id: str,
    parent_version_id: str | None,
) -> SkillVersion:
    shutil.copytree(source.directory_path, directory)
    copied_hash = tree_hash(directory)
    if copied_hash != source.tree_hash:
        raise RuntimeError("skill copy differs from its source")
    return replace(
        source,
        version_id=version_id,
        parent_version_id=parent_version_id,
        directory_path=directory,
        tree_hash=copied_hash,
    )


def run_part2(
    s0: SkillVersion,
    config: ExperimentConfig,
    output_dir: str | Path,
    *,
    select: Any,
    execute: Any,
    check: Any,
    update_state: Any,
    patch: Any,
    replay: Any,
    load_heldout: Any,
    evaluate: Any,
) -> dict[str, Any]:
    output = Path(output_dir)
    if output.exists():
        raise ValueError("Part II output already exists")
    if config.part != "part2":
        raise ValueError("Part II requires a part2 config")
    if tree_hash(s0.directory_path) != s0.tree_hash:
        raise ValueError("S0 hash does not match its directory")
    output.mkdir(parents=True)
    stage_costs: dict[str, float | int] = {
        "agent": 0,
        "patch": 0,
        "replay": 0,
        "heldout": 0,
    }
    all_steps: list[dict[str, Any]] = []
    missed_slots: list[dict[str, Any]] = []
    invalid_counts = {method: 0 for method in config.methods}
    final_skills: dict[str, SkillVersion] = {}

    for method in config.methods:
        method_dir = output / "methods" / method
        skills_dir = method_dir / "skills"
        skills_dir.mkdir(parents=True)
        current = _copy_skill(s0, skills_dir / "S0", "S0", None)
        state: dict[str, Any] = {}
        retained_tests: list[dict[str, Any]] = []
        accepted_count = 0
        for iteration in range(config.iteration_budget):
            step_dir = method_dir / "iterations" / str(iteration)
            test = select(method, state, current, iteration, step_dir / "selection")
            if isinstance(test, dict):
                validation_status = test.get("validation_status")
                test_id = test.get("test_id", "")
                validation_diagnostic = test.get("validation_diagnostic", "")
            else:
                validation_status = getattr(test, "validation_status", None)
                test_id = getattr(test, "test_id", "")
                validation_diagnostic = getattr(test, "validation_diagnostic", "")
            if validation_status == "invalid_test":
                missed = {
                    "method": method,
                    "iteration": iteration,
                    "test_id": test_id,
                    "status": "invalid_test",
                    "diagnostic": validation_diagnostic,
                }
                missed_slots.append(missed)
                invalid_counts[method] += 1
                atomic_write_json(step_dir / "missed-slot.json", missed)
                continue
            run = execute(method, current, test, iteration, step_dir / "execution")
            if run.get("model_id") != config.model_id:
                raise ValueError("non-verifier model differs from track model")
            if run.get("skill_version_id") != current.version_id:
                raise ValueError("development run used the wrong skill version")
            stage_costs["agent"] += run.get("cost", 0)
            checked = check(method, run, test, step_dir / "checks")
            stage_costs["agent"] += checked.get("cost", 0)
            state = update_state(
                method, state, run, checked, step_dir / "state-update"
            )
            atomic_write_json(method_dir / "state.json", state)
            failed = any(
                item.get("status") == "fail" for item in checked["results"]
            )
            decision = "retained"
            patch_attempt_id = None
            regression_results: list[dict[str, Any]] = []
            input_version = current.version_id
            if failed:
                patched = patch(
                    method,
                    state,
                    current,
                    test,
                    run,
                    checked,
                    step_dir / "patch",
                )
                patch_attempt_id = patched.get("patch_attempt_id")
                stage_costs["patch"] += patched.get("cost", 0)
                if (
                    patched.get("patch_status") == "patched"
                    and patched.get("model_id") == config.model_id
                    and patched.get("backend") == "pi"
                ):
                    candidate = patched["candidate_skill"]
                    if candidate.tree_hash != tree_hash(candidate.directory_path):
                        raise ValueError("candidate skill hash does not match")
                    current_replay = replay(
                        method, candidate, test, step_dir / "replay-current"
                    )
                    stage_costs["replay"] += current_replay.get("cost", 0)
                    regressions = []
                    for retained in retained_tests:
                        replayed = replay(
                            method,
                            candidate,
                            retained,
                            step_dir / "regressions" / retained["test_id"],
                        )
                        stage_costs["replay"] += replayed.get("cost", 0)
                        regressions.append(replayed["results"])
                        regression_results.append(
                            {
                                "test_id": retained["test_id"],
                                "check_results_id": replayed["check_results_id"],
                                "results": replayed["results"],
                            }
                        )
                    decision = accept_patch(
                        checked["results"], current_replay["results"], regressions
                    )
                    if decision == "accepted":
                        accepted_count += 1
                        version_id = f"S{accepted_count}"
                        current = _copy_skill(
                            candidate,
                            skills_dir / version_id,
                            version_id,
                            input_version,
                        )
                        if all(item["test_id"] != test["test_id"] for item in retained_tests):
                            retained_tests.append(test)
                else:
                    decision = "rejected"
            elif all(item["test_id"] != test["test_id"] for item in retained_tests):
                retained_tests.append(test)

            step_record = ImprovementStep(
                iteration=iteration,
                input_skill_version_id=input_version,
                test_id=test["test_id"],
                run_id=run["run_id"],
                check_results_id=checked["check_results_id"],
                patch_attempt_id=patch_attempt_id,
                decision=decision,
                resulting_skill_version_id=current.version_id,
                regression_results=tuple(regression_results),
            ).to_dict()
            step = {"method": method, **step_record}
            all_steps.append(step)
            atomic_write_json(step_dir / "improvement-step.json", step_record)
        final_skills[method] = current

    heldout_tests = load_heldout()
    if not heldout_tests:
        raise ValueError("held-out suite must be nonempty")
    heldout_rows: list[dict[str, Any]] = []
    evaluated = {"s0": s0, **final_skills}
    for label, skill in evaluated.items():
        for test in heldout_tests:
            for repetition in range(config.heldout_repetitions):
                evaluation = evaluate(
                    label,
                    skill,
                    test,
                    repetition,
                    output / "heldout" / label / test["test_id"] / str(repetition),
                )
                if evaluation.get("model_id") != config.model_id:
                    raise ValueError("held-out model differs from track model")
                stage_costs["heldout"] += evaluation.get("cost", 0)
                heldout_rows.append(
                    {
                        **evaluation,
                        "method": label,
                        "test_id": test["test_id"],
                        "repetition": repetition,
                    }
                )

    summary = summarize_part2(
        config.methods, heldout_rows, all_steps, stage_costs
    )
    summary["invalid_proposal_count"] = invalid_counts
    result = {
        "schema": "skillrace-part2/1",
        "s0_hash": s0.tree_hash,
        "final_skills": {
            method: skill.to_dict() for method, skill in final_skills.items()
        },
        "steps": all_steps,
        "missed_slots": missed_slots,
        "heldout_evaluations": heldout_rows,
        "summary": summary,
    }
    atomic_write_json(output / "summary.json", result)
    return result
