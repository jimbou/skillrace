from __future__ import annotations

import json

import pytest

import skillrace.generator as generator
import skillrace.greybox as greybox
import skillrace.guards as guards
import skillrace.sanity as sanity_module
from skillrace.sanity import (
    CandidateSanityRejection,
    SanityInfrastructureError,
    docker_execute,
    run_candidate_sanity,
    validate_sanity_spec,
)


VALID = {
    "required_paths": ["/workspace/pyproject.toml"],
    "required_tools": ["python3", "pytest"],
    "task_probe": {
        "command": "python3 -m pytest --collect-only -q",
        "allowed_exit_codes": [0, 1],
    },
    "unsolved_check": "python3 -m pytest -q >/dev/null 2>&1; test $? -ne 0",
}


def test_sanity_schema_accepts_explicit_mechanical_contract_without_mutating_input():
    original = json.loads(json.dumps(VALID))
    validated = validate_sanity_spec(VALID)

    assert validated == original
    assert validated is not VALID


@pytest.mark.parametrize(
    "bad",
    [
        {**VALID, "required_paths": ["relative/path"]},
        {**VALID, "required_paths": ["/workspace/../etc/passwd"]},
        {**VALID, "required_tools": ["python3; touch /tmp/pwned"]},
        {**VALID, "task_probe": {"command": "python3 app.py"}},
        {**VALID, "task_probe": {"command": "x\x00y", "allowed_exit_codes": [0]}},
        {**VALID, "task_probe": {"command": "x", "allowed_exit_codes": [256]}},
        {**VALID, "task_probe": {"command": "x", "allowed_exit_codes": [125]}},
        {**VALID, "unsolved_check": 42},
        {**VALID, "surprise": "not reviewable"},
    ],
)
def test_sanity_schema_rejects_unsafe_or_ambiguous_specs(bad):
    with pytest.raises(ValueError):
        validate_sanity_spec(bad)


def test_gate_runs_ordered_checks_in_the_candidate_image_only():
    calls = []

    def execute(image, command):
        calls.append((image, command))
        return 0, "ok"

    report = run_candidate_sanity("skillrace/candidate:built", VALID, execute=execute)

    assert report["valid"] is True
    assert [item["name"] for item in report["checks"]] == [
        "required-paths",
        "required-tools",
        "task-probe",
        "unsolved",
    ]
    assert len(calls) == 4
    assert {image for image, _ in calls} == {"skillrace/candidate:built"}


def test_gate_stops_at_first_rejection_and_reports_allowed_codes():
    calls = []

    def execute(image, command):
        calls.append(command)
        if command == VALID["task_probe"]["command"]:
            return 2, "collection exploded"
        return 0, "ok"

    report = run_candidate_sanity("candidate@sha256:abc", VALID, execute=execute)

    assert report["valid"] is False
    assert report["rejection"] == "task-probe"
    assert report["checks"][-1]["allowed_exit_codes"] == [0, 1]
    assert VALID["unsolved_check"] not in calls


def test_unsolved_condition_is_optional_but_explicitly_recorded():
    spec = {**VALID, "unsolved_check": None}
    report = run_candidate_sanity(
        "candidate:built", spec, execute=lambda image, command: (0, "ok")
    )

    assert report["valid"] is True
    assert report["checks"][-1] == {
        "name": "unsolved",
        "status": "not-configured",
    }


def test_default_executor_overrides_image_entrypoint_and_adds_isolation(monkeypatch):
    seen = []

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return Result()

    monkeypatch.setattr(sanity_module.subprocess, "run", fake_run)

    assert docker_execute("candidate:built", "true") == (0, "ok")
    argv = seen[0]
    assert "--network=none" in argv
    assert "--cap-drop=ALL" in argv
    assert argv[argv.index("--entrypoint") + 1] == "bash"
    assert argv[-2] == "-lc"
    assert argv[-1] == "true"


def test_realizer_has_one_shared_four_part_output_including_sanity(monkeypatch):
    monkeypatch.setattr(
        generator,
        "chat",
        lambda *args, **kwargs: {
            "content": json.dumps(
                {"prompt": "fix it", "tail": "RUN true", "sanity": VALID}
            ),
            "cost_provider_credits": 0.25,
        },
    )

    prompt, tail, sanity, cost = generator.realize("ctx", "task", "env", "model")

    assert (prompt, tail, sanity, cost) == ("fix it", "RUN true", VALID, 0.25)
    assert greybox.realize is generator.realize
    assert guards.realize is generator.realize


def test_invalid_shell_syntax_rejects_before_any_candidate_command_runs():
    calls = []
    invalid = {
        **VALID,
        "task_probe": {"command": "if then", "allowed_exit_codes": [0]},
    }
    with pytest.raises(CandidateSanityRejection, match="syntax"):
        run_candidate_sanity(
            "candidate:built",
            invalid,
            execute=lambda image, command: calls.append(command) or (0, "ok"),
        )
    assert calls == []


@pytest.mark.parametrize("failure", ["timeout", "rc125"])
def test_docker_timeout_or_rc125_is_typed_as_infrastructure(failure):
    def execute(image, command):
        if failure == "timeout":
            raise SanityInfrastructureError("docker timed out")
        return 125, "docker daemon unavailable"

    with pytest.raises(SanityInfrastructureError):
        run_candidate_sanity("candidate:built", VALID, execute=execute)
