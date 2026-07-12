from __future__ import annotations

import json
import subprocess
import uuid

import pytest

import skillrace.check_properties as checker
import skillrace.compile_checks as compiler


BASE_IMAGE = "skillrace/skillgen-base:latest"


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
    assert command[-2:] == ["sleep", "300"]


@pytest.mark.skipif(not _docker_available(), reason="Docker D1 base image unavailable")
def test_each_check_gets_a_fresh_snapshot_and_cannot_contaminate_the_next(tmp_path):
    origin = f"skillrace-check-origin-{uuid.uuid4().hex[:10]}"
    subprocess.run(
        ["docker", "run", "-d", "--name", origin, BASE_IMAGE, "sleep", "300"],
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
        observer = tmp_path / "observer.sh"
        observer.write_text(
            "#!/usr/bin/env bash\ntest ! -e /workspace/check-contamination\n"
        )

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


def test_compile_case_repairs_policy_failure_and_writes_script_atomically(
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
    responses = iter(
        [
            ("#!/usr/bin/env bash\ngrep edit /check/trace.jsonl\n", 0.1),
            (
                "#!/usr/bin/env bash\npython3 - <<'PY'\n"
                "import json\n"
                "for line in open('/check/trace.jsonl'):\n"
                "    blocks = json.loads(line).get('message', {}).get('content', [])\n"
                "    calls = [b for b in blocks if b.get('type') == 'toolCall']\n"
                "PY\n",
                0.2,
            ),
        ]
    )
    repairs = []

    def fake_author(*_args, fix=None, **_kwargs):
        repairs.append(fix)
        return next(responses)

    monkeypatch.setattr(compiler, "author_check", fake_author)
    writes = []
    real_atomic = compiler.atomic_write_text

    def recording_atomic(path, text):
        writes.append(path)
        real_atomic(path, text)

    monkeypatch.setattr(compiler, "atomic_write_text", recording_atomic)
    manifest, cost = compiler.compile_case(
        case,
        [{"id": "p1", "reads": "trace", "nl": "inspect real tool calls"}],
        "model",
        image="candidate:built",
    )

    assert repairs[0] is None
    assert "structurally parse" in repairs[1][1]
    assert cost == pytest.approx(0.3)
    assert writes == [case / "checks" / "p1.sh", case / "checks" / "p1.sh"]
    assert manifest["checks"][0]["policy_ok"] is True
    assert manifest["execution_policy"] == compiler.CHECK_EXECUTION_POLICY
