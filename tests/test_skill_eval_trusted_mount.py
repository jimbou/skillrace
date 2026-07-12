from __future__ import annotations

import json

import skillrace.skill_eval as skill_eval


def test_hidden_eval_does_not_bake_revised_skill_into_candidate_image(tmp_path):
    test = tmp_path / "test"
    test.mkdir()
    (test / "Dockerfile").write_text("FROM demo:base\nRUN true\n")
    (test / "candidate.json").write_text(
        json.dumps({"base_image": "demo:base", "prompt": "task"})
    )
    skill = tmp_path / "revised-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("trusted revision")

    case = skill_eval.derive_case(test, "demo", skill, tmp_path / "case")

    assert "/skills" not in (case / "Dockerfile").read_text()
    assert not (case / "skill").exists()


def test_hidden_eval_runner_passes_revised_skill_as_trusted_host_mount(
    tmp_path, monkeypatch
):
    calls = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        skill_eval.subprocess,
        "run",
        lambda argv, **kwargs: calls.append(argv) or Result(),
    )
    case = tmp_path / "case"
    run = tmp_path / "run"
    checks = tmp_path / "checks"
    skill = tmp_path / "skill"
    for path in (case, checks, skill):
        path.mkdir()
    skill_eval.run_test(case, run, "qwen3.6-flash", 30, checks, skill)

    runner = calls[0]
    assert runner[runner.index("--skill-dir") + 1] == str(skill)
