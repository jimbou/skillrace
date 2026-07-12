import json
import inspect
import shlex
import subprocess

import pytest

from skillrace.run_case import (
    AGENT_STARTED_MARKER,
    agent_started_from_logs,
    build_agent_command,
    build_agent_exec_args,
    build_agent_exec_environment,
    build_container_start_args,
    build_plain_exec_args,
    build_workspace_cleanup_command,
    build_workspace_setup_command,
    finalize_run,
    preserve_status_script,
    validate_skill_identifier,
)
import skillrace.run_case as runner


def test_cleanup_runs_without_masking_agent_failure(tmp_path):
    marker = tmp_path / "cleanup-ran"
    script = preserve_status_script("exit 23", f"touch {shlex.quote(str(marker))}")
    result = subprocess.run(["bash", "-c", script], check=False)
    assert result.returncode == 23
    assert marker.exists()


def test_cleanup_failure_does_not_turn_agent_success_into_failure(tmp_path):
    marker = tmp_path / "cleanup-ran"
    cleanup = f"touch {shlex.quote(str(marker))}; false"
    script = preserve_status_script("true", cleanup)
    result = subprocess.run(["bash", "-c", script], check=False)
    assert result.returncode == 0
    assert marker.exists()


def test_cleanup_failure_does_not_replace_agent_failure_status():
    script = preserve_status_script("exit 42", "exit 9")
    result = subprocess.run(["bash", "-c", script], check=False)
    assert result.returncode == 42


def test_nonzero_status_is_propagated_after_complete_manifest_exists(tmp_path):
    path = tmp_path / "run.json"
    manifest = {
        "run_id": "run-test",
        "termination": {"reason": "error", "rc": 23},
    }
    with pytest.raises(SystemExit) as stopped:
        finalize_run(path, manifest, 23)
    assert stopped.value.code == 23
    assert json.loads(path.read_text()) == manifest


def test_agent_command_quotes_special_model_as_one_shell_token():
    model = "qwen'; touch /tmp/skillrace-injected #"
    command = build_agent_command(model, "safe-skill")
    pi_tokens = shlex.split("--provider" + command.rsplit("pi --provider", 1)[1])
    assert pi_tokens[pi_tokens.index("--model") + 1] == model
    assert pi_tokens[pi_tokens.index("--skill") + 1] == "/trusted-skill"
    assert shlex.quote(model) in command


@pytest.mark.parametrize(
    "skill",
    ["", "../escape", "nested/skill", "space skill", "safe; touch /tmp/pwned", "Uppercase"],
)
def test_skill_identifier_rejects_shell_metacharacters_and_paths(skill):
    with pytest.raises(ValueError, match="skill identifier"):
        validate_skill_identifier(skill)


def _write_executable(path, body):
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)


def test_missing_pi_never_publishes_started_marker(tmp_path):
    workspace = tmp_path / "workspace"
    logs = tmp_path / "logs"
    binaries = tmp_path / "bin"
    workspace.mkdir()
    logs.mkdir()
    binaries.mkdir()
    _write_executable(binaries / "git", "exit 0")
    marker = logs / AGENT_STARTED_MARKER
    command = build_agent_command(
        "model",
        "safe-skill",
        workspace=workspace,
        started_marker=marker,
        git_executable=binaries / "git",
        pi_executable=binaries / "pi",
    )

    result = subprocess.run(
        ["/bin/bash", "-c", command],
        env={"PATH": str(binaries)},
        check=False,
    )

    assert result.returncode == 127
    assert agent_started_from_logs(logs) is False


def test_pi_error_after_marker_is_a_started_execution(tmp_path):
    workspace = tmp_path / "workspace"
    logs = tmp_path / "logs"
    binaries = tmp_path / "bin"
    workspace.mkdir()
    logs.mkdir()
    binaries.mkdir()
    _write_executable(binaries / "git", "exit 0")
    _write_executable(binaries / "pi", "exit 23")
    marker = logs / AGENT_STARTED_MARKER
    command = build_agent_command(
        "model",
        "safe-skill",
        workspace=workspace,
        started_marker=marker,
        git_executable=binaries / "git",
        pi_executable=binaries / "pi",
    )

    result = subprocess.run(
        ["/bin/bash", "-c", command],
        env={"PATH": str(binaries)},
        check=False,
    )

    assert result.returncode == 23
    assert agent_started_from_logs(logs) is True


def test_agent_marker_is_after_setup_and_pi_verification():
    command = build_agent_command("model", "safe-skill")
    assert command.index("test -x /usr/local/bin/pi") < command.index("agent-started")
    assert command.index("agent-started") < command.index("/usr/local/bin/pi --provider")
    assert "git " not in command


def test_runner_manifest_derives_agent_started_from_container_marker():
    source = inspect.getsource(runner.main)
    assert "agent_started = True" not in source
    assert "agent_started_from_logs(logs)" in source
    assert '"agent_started": agent_started' in source


def test_long_lived_container_has_no_secret_and_cannot_run_candidate_hooks(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("trusted")
    args = build_container_start_args(
        "run-id", tmp_path / "logs", "candidate:built", skill_dir
    )
    joined = " ".join(args)
    assert "CLOSE_API_KEY" not in joined and "PI_PROMPT" not in joined
    assert args[args.index("--entrypoint") + 1] == "/bin/sleep"
    assert "--no-healthcheck" in args
    assert f"{skill_dir.resolve()}:/trusted-skill:ro" in args


def test_secret_and_prompt_are_scoped_to_trusted_agent_exec_only():
    args = build_agent_exec_args("run-id", "trusted command")
    environment = build_agent_exec_environment(
        "secret-key", "repair it", base_environment={"PATH": "/bin"}
    )
    assert args[:2] == ["docker", "exec"]
    assert "CLOSE_API_KEY" in args and "PI_PROMPT" in args
    assert "secret-key" not in repr(args)
    assert "repair it" not in repr(args)
    assert environment["CLOSE_API_KEY"] == "secret-key"
    assert environment["PI_PROMPT"] == "repair it"
    assert environment["PATH"] == "/bin"
    assert args[-3:] == ["/bin/bash", "-c", "trusted command"]


def test_workspace_git_setup_and_cleanup_use_separate_secret_free_execs():
    setup = build_workspace_setup_command()
    cleanup = build_workspace_cleanup_command()
    assert "/usr/bin/git" in setup and "/usr/bin/git" in cleanup
    for command in (setup, cleanup):
        args = build_plain_exec_args("run-id", command)
        joined = " ".join(args)
        assert "CLOSE_API_KEY" not in joined and "PI_PROMPT" not in joined
        assert args[-3:] == ["/bin/bash", "-c", command]
