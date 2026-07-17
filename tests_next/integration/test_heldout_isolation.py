from dataclasses import replace
from pathlib import Path

from skillrace_next.pipeline.part2 import run_part2
from tests_next.unit.test_random_method import skill_version
from tests_next.unit.test_test_cases import config_for


def test_heldout_paths_are_loaded_only_after_all_development_iterations(
    tmp_path: Path,
) -> None:
    s0 = skill_version(tmp_path)
    config = replace(
        config_for(tmp_path),
        part="part2",
        methods=("random", "verigrey", "skillrace"),
        iteration_budget=2,
        heldout_repetitions=2,
    )
    events = []
    heldout_path = tmp_path / "private-heldout" / "prompt.txt"

    def select(method, state, current, iteration, output):
        assert not heldout_path.exists()
        events.append(("dev", method, iteration))
        return {"test_id": f"dev-{method}-{iteration}"}

    def execute(method, current, test, iteration, output):
        assert not heldout_path.exists()
        return {
            "run_id": f"run-{method}-{iteration}",
            "test_id": test["test_id"],
            "model_id": config.model_id,
            "skill_version_id": current.version_id,
            "cost": 1,
        }

    def check(method, run, test, output):
        return {
            "check_results_id": f"checks-{run['run_id']}",
            "results": [{"check_id": "P1-C1", "status": "pass"}],
            "cost": 0,
        }

    def update(method, state, run, checked, output):
        return state

    def forbidden_patch(*args):
        raise AssertionError("passing development runs must not be patched")

    def forbidden_replay(*args):
        raise AssertionError("passing development runs must not be replayed")

    def load_heldout():
        events.append(("load-heldout",))
        heldout_path.parent.mkdir()
        heldout_path.write_text("hidden\n", encoding="utf-8")
        return [{"test_id": "held", "path": heldout_path}]

    def evaluate(label, skill, test, repetition, output):
        assert test["path"] == heldout_path
        events.append(("heldout", label, repetition))
        return {
            "run_id": f"held-{label}-{repetition}",
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
        patch=forbidden_patch,
        replay=forbidden_replay,
        load_heldout=load_heldout,
        evaluate=evaluate,
    )

    assert events[:6] == [
        ("dev", "random", 0),
        ("dev", "random", 1),
        ("dev", "verigrey", 0),
        ("dev", "verigrey", 1),
        ("dev", "skillrace", 0),
        ("dev", "skillrace", 1),
    ]
    assert events[6] == ("load-heldout",)
    assert len(result["heldout_evaluations"]) == 4 * config.heldout_repetitions
