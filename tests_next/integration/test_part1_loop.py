from dataclasses import replace
import json
from pathlib import Path

from skillrace_next.pipeline.part1 import run_part1
from tests_next.unit.test_random_method import skill_version
from tests_next.unit.test_test_cases import config_for, pending_test


def test_three_method_loop_groups_before_patching_and_keeps_s0_immutable(
    tmp_path: Path,
) -> None:
    s0 = skill_version(tmp_path)
    config = config_for(tmp_path)
    config = replace(
        config,
        methods=("random", "verigrey", "skillrace"),
        iteration_budget=1,
    )
    events = []
    seen_hashes = []

    def propose(method, state, skill, slot, output):
        events.append(("propose", method))
        seen_hashes.append(skill.tree_hash)
        return {"test_id": f"{method}-test", "method": method}

    def execute(method, skill, test, slot, output):
        events.append(("run", method))
        seen_hashes.append(skill.tree_hash)
        return {
            "run_id": f"{method}-run",
            "test_id": test["test_id"],
            "method": method,
            "model_id": "deepseek-v3.2",
            "skill_hash": skill.tree_hash,
            "cost": 10,
        }

    def check(method, run, test, output):
        events.append(("check", method))
        if method == "skillrace":
            return []
        return [
            {
                "check_id": "P1-C1",
                "property_id": "P1",
                "property_group": "output",
                "status": "fail",
                "diagnostic": "wrong bytes",
                "failing_check_signature": "P1-C1:wrong-bytes",
                "root_cause_category": "format_contract",
            }
        ]

    def update(method, state, run, results, output):
        events.append(("update", method))
        return {"observed_runs": state.get("observed_runs", 0) + 1}

    def confirm(candidate, output):
        events.append(("confirm", candidate["candidate_id"]))
        assert sum(event[0] == "run" for event in events) == 3
        return True

    def patch(candidate, output):
        events.append(("patch", candidate["candidate_id"]))
        return {
            "candidate_id": candidate["candidate_id"],
            "decision": "accepted",
            "model_id": "deepseek-v3.2",
            "backend": "pi",
            "cost": 5,
        }

    result = run_part1(
        s0,
        config,
        tmp_path / "part1",
        propose=propose,
        execute=execute,
        check=check,
        update_state=update,
        confirm=confirm,
        patch=patch,
    )

    assert seen_hashes == [s0.tree_hash] * 6
    assert sum(event[0] == "run" for event in events) == 3
    assert [event[1] for event in events if event[0] == "run"] == list(config.methods)
    first_patch = next(index for index, event in enumerate(events) if event[0] == "patch")
    assert all(event[0] != "run" for event in events[first_patch + 1 :])
    assert len(result["raw_candidates"]) == 2
    assert len(result["confirmed_bugs"]) == 1
    assert len(result["patches"]) == 1
    assert result["patches"][0]["backend"] == "pi"
    assert result["summary"]["raw_candidates"] == 2
    assert result["summary"]["confirmed_distinct_bugs"] == 1
    assert result["summary"]["confirmed_repaired_bugs"] == 1
    for method in config.methods:
        state = json.loads(
            (tmp_path / "part1" / "methods" / method / "state.json").read_text()
        )
        assert state == {"observed_runs": 1}


def test_second_invalid_proposal_records_missed_slot_without_agent_run(
    tmp_path: Path,
) -> None:
    s0 = skill_version(tmp_path)
    config = replace(
        config_for(tmp_path), methods=("random",), iteration_budget=1
    )
    invalid = replace(
        pending_test(tmp_path),
        validation_status="invalid_test",
        validation_diagnostic="replacement Docker build failed",
    )

    def propose(method, state, skill, slot, output):
        return invalid

    def must_not_run(*args, **kwargs):
        raise AssertionError("invalid proposals must not spend an agent run")

    result = run_part1(
        s0,
        config,
        tmp_path / "part1-invalid",
        propose=propose,
        execute=must_not_run,
        check=must_not_run,
        update_state=must_not_run,
        confirm=must_not_run,
        patch=must_not_run,
    )

    assert result["missed_slots"] == [
        {
            "method": "random",
            "slot": 0,
            "test_id": "test-1",
            "status": "invalid_test",
            "diagnostic": "replacement Docker build failed",
        }
    ]
    assert result["summary"]["invalid_proposal_count"] == {"random": 1}
    assert json.loads(
        (
            tmp_path
            / "part1-invalid"
            / "methods"
            / "random"
            / "runs"
            / "0"
            / "missed-slot.json"
        ).read_text(encoding="utf-8")
    ) == result["missed_slots"][0]
