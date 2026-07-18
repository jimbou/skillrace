from dataclasses import replace
import json
from pathlib import Path

from skillrace_next.pipeline.part2 import run_part2
from skillrace_next.records import ImprovementStep, SkillVersion
from skillrace_next.storage import tree_hash
from tests_next.unit.test_random_method import skill_version
from tests_next.unit.test_test_cases import config_for, pending_test


def candidate_skill(current: SkillVersion, output: Path, marker: str) -> SkillVersion:
    directory = output / "candidate"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        (current.directory_path / "SKILL.md").read_text(encoding="utf-8")
        + f"\n{marker}\n",
        encoding="utf-8",
    )
    receipt = output / "receipt.json"
    receipt.write_text("{}\n", encoding="utf-8")
    return replace(
        current,
        version_id="candidate",
        parent_version_id=current.version_id,
        directory_path=directory,
        tree_hash=tree_hash(directory),
        receipt_path=receipt,
    )


def test_accepted_versions_carry_forward_and_rejected_versions_are_discarded(
    tmp_path: Path,
) -> None:
    s0 = skill_version(tmp_path)
    config = replace(
        config_for(tmp_path),
        part="part2",
        methods=("random", "verigrey", "skillrace"),
        iteration_budget=2,
        heldout_repetitions=1,
    )
    events = []
    inputs = {method: [] for method in config.methods}

    def select(method, state, current, iteration, output):
        events.append(("select", method, iteration, current.version_id))
        return {"test_id": f"dev-{iteration}"}

    def execute(method, current, test, iteration, output):
        inputs[method].append((current.version_id, current.tree_hash))
        events.append(("execute", method, iteration, current.version_id))
        return {
            "run_id": f"{method}-run-{iteration}",
            "test_id": test["test_id"],
            "model_id": config.model_id,
            "skill_version_id": current.version_id,
            "cost": 1,
        }

    def check(method, run, test, output):
        return {
            "check_results_id": f"checks-{run['run_id']}",
            "results": [{"check_id": "P1-C1", "status": "fail"}],
            "cost": 0,
        }

    def update(method, state, run, checked, output):
        return {"runs": state.get("runs", 0) + 1}

    def patch(method, state, current, test, run, checked, output):
        candidate = candidate_skill(current, Path(output), f"{method}-{test['test_id']}")
        return {
            "patch_attempt_id": f"patch-{method}-{test['test_id']}",
            "candidate_skill": candidate,
            "patch_status": "patched",
            "model_id": config.model_id,
            "backend": "pi",
            "cost": 2,
        }

    def replay(method, candidate, test, output):
        events.append(("replay", method, test["test_id"], candidate.tree_hash))
        iteration = int(test["test_id"].split("-")[-1])
        status = "pass" if iteration == 0 else "fail"
        return {
            "check_results_id": f"replay-{method}-{test['test_id']}",
            "results": [{"check_id": "P1-C1", "status": status}],
            "cost": 1,
        }

    def load_heldout():
        events.append(("load-heldout",))
        return [{"test_id": "held-1", "path": tmp_path / "heldout" / "prompt.txt"}]

    def evaluate(label, skill, test, repetition, output):
        events.append(("evaluate", label, skill.version_id, repetition))
        return {
            "run_id": f"held-{label}",
            "model_id": config.model_id,
            "passed": True,
            "cost": 1,
        }

    result = run_part2(
        s0,
        config,
        tmp_path / "part2",
        select=select,
        execute=execute,
        check=check,
        update_state=update,
        patch=patch,
        replay=replay,
        load_heldout=load_heldout,
        evaluate=evaluate,
    )

    for method in config.methods:
        assert inputs[method][0][0] == "S0"
        assert inputs[method][0][1] == s0.tree_hash
        assert inputs[method][1][0] == "S1"
        assert result["final_skills"][method]["version_id"] == "S1"
        assert [step["decision"] for step in result["steps"] if step["method"] == method] == [
            "accepted",
            "rejected",
        ]
    assert [event[1] for event in events if event[0] == "evaluate"] == [
        "s0",
        "random",
        "verigrey",
        "skillrace",
    ]
    assert result["summary"]["accepted_revisions"] == {
        "random": 1,
        "verigrey": 1,
        "skillrace": 1,
    }
    assert result["summary"]["rejected_revisions"] == {
        "random": 1,
        "verigrey": 1,
        "skillrace": 1,
    }
    saved = json.loads(
        (tmp_path / "part2" / "methods" / "random" / "iterations" / "0" / "improvement-step.json").read_text()
    )
    assert ImprovementStep.from_dict(saved).decision == "accepted"


def test_second_invalid_development_proposal_is_a_missed_slot(
    tmp_path: Path,
) -> None:
    s0 = skill_version(tmp_path)
    config = replace(
        config_for(tmp_path),
        part="part2",
        methods=("random",),
        iteration_budget=1,
        heldout_repetitions=1,
    )
    invalid = replace(
        pending_test(tmp_path),
        validation_status="invalid_test",
        validation_diagnostic="replacement sanity check failed",
    )

    def select(method, state, current, iteration, output):
        return invalid

    def must_not_run(*args, **kwargs):
        raise AssertionError("invalid proposals must not spend an agent run")

    def load_heldout():
        return [{"test_id": "held"}]

    def evaluate(label, skill, test, repetition, output):
        return {
            "run_id": f"held-{label}",
            "model_id": config.model_id,
            "passed": True,
            "cost": 0,
        }

    result = run_part2(
        s0,
        config,
        tmp_path / "part2-invalid",
        select=select,
        execute=must_not_run,
        check=must_not_run,
        update_state=must_not_run,
        patch=must_not_run,
        replay=must_not_run,
        load_heldout=load_heldout,
        evaluate=evaluate,
    )

    assert result["steps"] == []
    assert result["missed_slots"] == [
        {
            "method": "random",
            "iteration": 0,
            "test_id": "test-1",
            "status": "invalid_test",
            "diagnostic": "replacement sanity check failed",
        }
    ]
    assert result["summary"]["invalid_proposal_count"] == {"random": 1}
    assert result["final_skills"]["random"]["version_id"] == "S0"
