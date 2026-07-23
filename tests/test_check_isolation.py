from __future__ import annotations

import json
import subprocess
import uuid

import pytest

import skillrace.check_properties as checker
import skillrace.compile_checks as compiler


BASE_IMAGE = "skillrace/skillgen-base:0.73.1-construction"


def _docker_available() -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", BASE_IMAGE], capture_output=True
    )
    return result.returncode == 0


def test_check_container_command_is_networkless_and_resource_bounded():
    command = checker.check_container_command("check-name", "sha256:image")
    assert command[:3] == ["docker", "run", "-d"]
    assert "--network=none" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert "--pids-limit=256" in command
    assert command[command.index("--entrypoint") + 1] == "/bin/sleep"
    assert command[-1] == "300"


@pytest.mark.skipif(not _docker_available(), reason="Docker D1 base image unavailable")
def test_each_check_gets_a_fresh_snapshot_and_cannot_contaminate_the_next(tmp_path):
    origin = f"skillrace-check-origin-{uuid.uuid4().hex[:10]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            origin,
            "--entrypoint",
            "/bin/sleep",
            BASE_IMAGE,
            "300",
        ],
        check=True,
        capture_output=True,
    )
    snapshot = None
    try:
        snapshot = checker.snapshot_container_for_checks(origin)
        mutator = tmp_path / "mutator.sh"
        mutator.write_text(
            "#!/usr/bin/env bash\ntouch /workspace/check-contamination\nexit 0\n"
        )
        # Crash-safe atomic writes intentionally produce owner-only files. Docker
        # preserves the host UID on copy; a capability-dropped container root then
        # cannot read a UID-1000 mode-0600 script.
        mutator.chmod(0o600)
        observer = tmp_path / "observer.sh"
        observer.write_text(
            "#!/usr/bin/env bash\ntest ! -e /workspace/check-contamination\n"
        )
        observer.chmod(0o600)

        assert checker.run_script_isolated(mutator, snapshot, timeout_seconds=5)[0]
        assert checker.run_script_isolated(observer, snapshot, timeout_seconds=5)[0]
        unchanged = subprocess.run(
            ["docker", "exec", origin, "test", "!", "-e", "/workspace/check-contamination"]
        )
        assert unchanged.returncode == 0
    finally:
        subprocess.run(["docker", "rm", "-f", origin], capture_output=True)
        if snapshot:
            subprocess.run(["docker", "rmi", "-f", snapshot], capture_output=True)


@pytest.mark.skipif(not _docker_available(), reason="Docker D1 base image unavailable")
def test_hung_check_is_inconclusive_and_child_is_removed(tmp_path):
    origin = f"skillrace-check-origin-{uuid.uuid4().hex[:10]}"
    subprocess.run(
        ["docker", "run", "-d", "--name", origin, BASE_IMAGE, "sleep", "300"],
        check=True,
        capture_output=True,
    )
    snapshot = None
    try:
        snapshot = checker.snapshot_container_for_checks(origin)
        script = tmp_path / "hang.sh"
        script.write_text("#!/usr/bin/env bash\nsleep 30\n")
        holds, detail = checker.run_script_isolated(
            script, snapshot, timeout_seconds=0.1
        )
        assert holds is None
        assert "timeout" in detail
        dangling = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "name=skillrace-property-check-",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert dangling.stdout.strip() == ""
    finally:
        subprocess.run(["docker", "rm", "-f", origin], capture_output=True)
        if snapshot:
            subprocess.run(["docker", "rmi", "-f", snapshot], capture_output=True)


@pytest.mark.skipif(not _docker_available(), reason="Docker D1 base image unavailable")
def test_python_checker_exit_codes_map_to_three_state_verdicts(tmp_path):
    origin = f"skillrace-check-origin-{uuid.uuid4().hex[:10]}"
    subprocess.run(
        ["docker", "run", "-d", "--name", origin, BASE_IMAGE, "sleep", "300"],
        check=True,
        capture_output=True,
    )
    snapshot = None
    try:
        snapshot = checker.snapshot_container_for_checks(origin)
        expected = {0: True, 1: False, 2: None}
        for exit_code, holds_expected in expected.items():
            script = tmp_path / f"exit-{exit_code}.py"
            script.write_text(
                f"import sys\nprint('exit {exit_code}')\nsys.exit({exit_code})\n"
            )
            holds, detail = checker.run_script_isolated(
                script, snapshot, timeout_seconds=5
            )
            assert holds is holds_expected
            assert f"exit={exit_code}" in detail
    finally:
        subprocess.run(["docker", "rm", "-f", origin], capture_output=True)
        if snapshot:
            subprocess.run(["docker", "rmi", "-f", snapshot], capture_output=True)


@pytest.mark.parametrize(
    ("script", "message"),
    [
        ("#!/usr/bin/env bash\ncurl https://example.com\n", "network"),
        (
            "#!/usr/bin/env bash\ngrep force /check/trace.jsonl\n",
            "structurally parse",
        ),
        ("#!/usr/bin/env bash\ndocker run alpine true\n", "privileged"),
    ],
)
def test_compiler_policy_rejects_unsafe_or_raw_trace_checks(script, message):
    ok, error = compiler.validate_script_policy(script, tools=["bash", "python3"])
    assert not ok
    assert message in error


def test_compiler_policy_accepts_structural_toolcall_parsing():
    script = """#!/usr/bin/env bash
python3 - <<'PY'
import json
for line in open('/check/trace.jsonl'):
    for block in json.loads(line).get('message', {}).get('content', []):
        if block.get('type') == 'toolCall':
            pass
PY
"""
    assert compiler.validate_script_policy(script, tools=["bash", "python3"]) == (
        True,
        "",
    )


def test_compile_case_retries_policy_failure_once_then_rejects_no_oracle(
    tmp_path, monkeypatch
):
    case = tmp_path / "case"
    case.mkdir()
    candidate = {
        "candidate_id": "c1",
        "prompt": "inspect the trace",
        "containerfile": "FROM base@sha256:one\n",
        "base_image": "base@sha256:one",
        "skill": "demo",
    }
    (case / "candidate.json").write_text(json.dumps(candidate))
    (case / "Dockerfile").write_text(candidate["containerfile"])
    monkeypatch.setattr(compiler, "inspect_image_digest", lambda _image: "sha256:image")
    monkeypatch.setattr(
        compiler, "probe_initial_env", lambda _image: (["bash", "python3"], [])
    )
    author_calls = []

    def fake_author(*_args, **_kwargs):
        author_calls.append(True)
        return "#!/usr/bin/env bash\ngrep edit /check/trace.jsonl\n", 0.1

    monkeypatch.setattr(compiler, "author_check", fake_author)
    monkeypatch.setattr(
        compiler,
        "audit_checks",
        lambda **_kwargs: (
            [
                {
                    "property_id": "p1",
                    "decision": "accept",
                    "reason": "supported",
                }
            ],
            0.0,
            {
                "operation_id": "offline-audit",
                "model": "model",
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cost_provider_credits": 0.0,
                "terminal_receipt_sha256": "a" * 64,
                "call_terminal_receipt_sha256": "b" * 64,
            },
        ),
    )
    writes = []
    real_atomic = compiler.atomic_write_text

    def recording_atomic(path, text):
        writes.append(path)
        real_atomic(path, text)

    monkeypatch.setattr(compiler, "atomic_write_text", recording_atomic)
    with pytest.raises(RuntimeError, match="no usable property checkers"):
        compiler.compile_case(
            case,
            [{"id": "p1", "reads": "trace", "nl": "inspect real tool calls"}],
            "model",
            image="candidate:built",
        )

    assert author_calls == [True, True]
    assert writes == [case / "checks" / "p1.sh", case / "checks" / "p1.sh"]
def test_legacy_bash_heredoc_warning_is_not_valid_syntax(tmp_path):
    script = tmp_path / "broken.sh"
    script.write_text(
        "#!/usr/bin/env bash\npython3 <<'EOF'\nprint('ok')\nEOF extra\n"
    )

    valid, error = checker._syntax_ok(script)

    assert valid is False
    assert "here-document" in error


def test_change_scoped_property_requires_workspace_diff_evidence():
    script = "#!/usr/bin/env bash\ngrep -q waitFor /workspace/api.test.js\n"
    property_text = (
        "Every synchronization wait introduced or changed for the task observes "
        "the actual fresh condition."
    )

    assert compiler.validate_script_policy(
        script, tools=["bash", "grep"], reads="state", property_nl=property_text
    ) == (False, "change-scoped property must inspect /check/workspace.diff")

    with_diff = script + "grep -q waitFor /check/workspace.diff\n"
    assert compiler.validate_script_policy(
        with_diff,
        tools=["bash", "grep"],
        reads="state",
        property_nl=property_text,
    ) == (True, "")


def test_final_state_property_word_changed_does_not_require_diff():
    script = "#!/usr/bin/env bash\ntest -f /workspace/output.csv\n"
    property_text = (
        "Columns and row ordering are preserved or changed only as requested."
    )

    assert compiler.validate_script_policy(
        script, tools=["bash", "test"], reads="state", property_nl=property_text
    ) == (True, "")
