from __future__ import annotations

import copy
import errno
import json
import pathlib
import subprocess
import sys

import pytest

from skillrace.io_utils import canonical_json_hash, file_hash
from skillrace.rq3_isolation import (
    Phase1IsolationError,
    build_phase1_confinement,
    validate_phase1_confinement_record,
)
from skillrace.rq3_pipeline import (
    CampaignLaunchRequest,
    _campaign_launch,
    _default_confirmation_executor,
    _default_campaign_runner,
    _run_revision_phase,
    run_rq3_scenario,
)
from skillrace.rq3_confirmation import ConfirmationRequest
from skillrace.rq3 import ManifestMismatchError
from skillrace.closeai import chat
from tests.test_rq3_campaign_adapter import _write_real_campaign2


def _confinement(
    tmp_path: pathlib.Path,
    inner_argv: list[str],
    *,
    environ: dict[str, str] | None = None,
):
    public = tmp_path / "artifact" / "public-work"
    output = tmp_path / "artifact" / "campaigns" / "random"
    public.mkdir(parents=True)
    output.mkdir(parents=True)
    (public / "SKILL.md").write_text("# Public skill\n", encoding="utf-8")
    launch = build_phase1_confinement(
        inner_argv=inner_argv,
        cwd=public,
        public_roots=(public,),
        output_root=output,
        ledger_path=output / "provider-ledger" / "cost.jsonl",
        environ=environ or {},
        require_docker=False,
    )
    return launch, public, output


def test_phase1_subprocess_cannot_read_absolute_hidden_test_path(tmp_path):
    hidden = tmp_path / "scenarios" / "demo" / "tests" / "t1" / "secret.txt"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("hidden sentinel", encoding="utf-8")
    script = (
        "import errno,pathlib,sys; p=pathlib.Path(sys.argv[1]); "
        "\ntry: p.read_bytes()"
        "\nexcept OSError as e: raise SystemExit(0 if e.errno in (errno.ENOENT, errno.EACCES) else 8)"
        "\nraise SystemExit(9)"
    )
    launch, _, _ = _confinement(
        tmp_path,
        [sys.executable, "-c", script, str(hidden.resolve())],
    )

    process = subprocess.run(
        launch.command,
        env=launch.environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert process.returncode == 0, process.stdout + process.stderr
    assert hidden.read_text(encoding="utf-8") == "hidden sentinel"


def test_revision_subprocess_cannot_read_absolute_hidden_test_path(tmp_path):
    hidden = tmp_path / "scenarios" / "demo" / "tests" / "t2" / "secret.txt"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("hidden revision sentinel", encoding="utf-8")
    base = tmp_path / "artifact" / "public-stage" / "base_skill"
    feedback = tmp_path / "artifact" / "feedback"
    revisions = tmp_path / "artifact" / "revisions"
    base.mkdir(parents=True)
    feedback.mkdir(parents=True)
    revisions.mkdir(parents=True)
    (base / "SKILL.md").write_text("# Base\n", encoding="utf-8")
    (feedback / "random.json").write_text("{}\n", encoding="utf-8")
    script = (
        "import errno,pathlib,sys; p=pathlib.Path(sys.argv[1]); "
        "\ntry: p.read_bytes()"
        "\nexcept OSError as e: raise SystemExit(0 if e.errno in (errno.ENOENT, errno.EACCES) else 8)"
        "\nraise SystemExit(9)"
    )
    launch = build_phase1_confinement(
        inner_argv=[sys.executable, "-c", script, str(hidden.resolve())],
        cwd=base,
        public_roots=(base, feedback),
        output_root=revisions,
        ledger_path=revisions / "provider-ledger" / "cost.jsonl",
        environ={},
        require_docker=False,
        role="revision",
    )

    process = subprocess.run(
        launch.command,
        env=launch.environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert process.returncode == 0, process.stdout + process.stderr
    assert launch.record["role"] == "revision"
    assert hidden.read_text(encoding="utf-8") == "hidden revision sentinel"


def test_revision_ignores_docker_config_that_would_mount_hidden_directory(tmp_path):
    hidden = tmp_path / "scenarios" / "demo" / "tests" / "docker-config" / "secret.txt"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("must remain hidden", encoding="utf-8")
    base = tmp_path / "artifact" / "base"
    feedback = tmp_path / "artifact" / "feedback"
    revisions = tmp_path / "artifact" / "revisions"
    for path in (base, feedback, revisions):
        path.mkdir(parents=True)
    (base / "SKILL.md").write_text("# Base\n", encoding="utf-8")
    script = (
        "import errno,pathlib,sys; p=pathlib.Path(sys.argv[1]); "
        "\ntry: p.read_bytes()"
        "\nexcept OSError as e: raise SystemExit(0 if e.errno in (errno.ENOENT, errno.EACCES) else 8)"
        "\nraise SystemExit(9)"
    )
    launch = build_phase1_confinement(
        inner_argv=[sys.executable, "-c", script, str(hidden.resolve())],
        cwd=base,
        public_roots=(base, feedback),
        output_root=revisions,
        ledger_path=revisions / "provider-ledger" / "cost.jsonl",
        environ={"DOCKER_CONFIG": str(hidden.parent)},
        require_docker=False,
        role="revision",
    )

    process = subprocess.run(
        launch.command,
        env=launch.environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert process.returncode == 0, process.stdout + process.stderr
    assert "DOCKER_CONFIG" not in launch.environment
    assert all(
        mount["source"] != str(hidden.parent.resolve())
        for mount in launch.record["filesystem"]["mounts"]
    )


def test_untrusted_certificate_override_cannot_smuggle_hidden_file(tmp_path):
    hidden = tmp_path / "scenarios" / "demo" / "tests" / "fake-ca.pem"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("hidden as a fake certificate", encoding="utf-8")
    public = tmp_path / "artifact" / "public"
    output = tmp_path / "artifact" / "output"
    public.mkdir(parents=True)
    output.mkdir(parents=True)

    with pytest.raises(Phase1IsolationError, match="certificate.*trusted runtime"):
        build_phase1_confinement(
            inner_argv=[sys.executable, "-c", "raise SystemExit(0)"],
            cwd=public,
            public_roots=(public,),
            output_root=output,
            ledger_path=output / "provider-ledger" / "cost.jsonl",
            environ={"SSL_CERT_FILE": str(hidden)},
            require_docker=False,
        )


def test_campaign_rejects_docker_config_outside_host_docker_directory(tmp_path):
    hidden_config = tmp_path / "scenarios" / "demo" / "tests" / "docker-config"
    public = tmp_path / "artifact" / "public"
    output = tmp_path / "artifact" / "output"
    hidden_config.mkdir(parents=True)
    public.mkdir(parents=True)
    output.mkdir(parents=True)

    with pytest.raises(Phase1IsolationError, match="Docker config.*trusted"):
        build_phase1_confinement(
            inner_argv=[sys.executable, "-c", "raise SystemExit(0)"],
            cwd=public,
            public_roots=(public,),
            output_root=output,
            ledger_path=output / "provider-ledger" / "cost.jsonl",
            environ={"DOCKER_CONFIG": str(hidden_config)},
            require_docker=True,
        )


def test_campaign_rejects_remote_docker_daemon_authority(tmp_path):
    public = tmp_path / "artifact" / "public"
    output = tmp_path / "artifact" / "output"
    public.mkdir(parents=True)
    output.mkdir(parents=True)

    with pytest.raises(Phase1IsolationError, match="only a Unix Docker socket"):
        build_phase1_confinement(
            inner_argv=[sys.executable, "-c", "raise SystemExit(0)"],
            cwd=public,
            public_roots=(public,),
            output_root=output,
            ledger_path=output / "provider-ledger" / "cost.jsonl",
            environ={"DOCKER_HOST": "tcp://127.0.0.1:2375"},
            require_docker=True,
        )


def test_phase1_mounts_public_read_only_and_only_output_and_ledger_writable(tmp_path):
    result_path = tmp_path / "artifact" / "campaigns" / "random" / "probe.json"
    ledger_probe = (
        tmp_path
        / "artifact"
        / "campaigns"
        / "random"
        / "provider-ledger"
        / "probe.txt"
    )
    public_file = tmp_path / "artifact" / "public-work" / "SKILL.md"
    script = """
import errno
import json
import os
import pathlib
import sys

public_file, result_path, ledger_probe = map(pathlib.Path, sys.argv[1:])
try:
    public_file.write_text("tampered", encoding="utf-8")
except OSError as error:
    public_errno = error.errno
else:
    public_errno = None
result_path.write_text(json.dumps({
    "public": public_file.read_text(encoding="utf-8"),
    "public_write_errno": public_errno,
    "environment_names": sorted(os.environ),
    "home": os.environ.get("HOME"),
    "ledger": os.environ.get("SKILLRACE_LEDGER"),
}), encoding="utf-8")
ledger_probe.parent.mkdir(parents=True, exist_ok=True)
ledger_probe.write_text("durable", encoding="utf-8")
"""
    launch, _, output = _confinement(
        tmp_path,
        [
            sys.executable,
            "-c",
            script,
            str(public_file),
            str(result_path),
            str(ledger_probe),
        ],
        environ={
            "CLOSE_API_KEY": "do-not-record-me",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HOSTILE_UNRELATED_SECRET": "must-not-cross",
        },
    )

    process = subprocess.run(
        launch.command,
        env=launch.environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["public"] == "# Public skill\n"
    assert result["public_write_errno"] in {errno.EROFS, errno.EACCES}
    assert ledger_probe.read_text(encoding="utf-8") == "durable"
    assert result["home"] == "/tmp/skillrace-home"
    assert result["ledger"] == str(
        output / "provider-ledger" / "cost.jsonl"
    )
    assert "CLOSE_API_KEY" in result["environment_names"]
    assert "HTTP_PROXY" in result["environment_names"]
    assert "HOSTILE_UNRELATED_SECRET" not in result["environment_names"]
    assert "do-not-record-me" not in json.dumps(launch.record)


def test_phase1_confinement_record_is_self_authenticating_and_tamper_evident(tmp_path):
    launch, _, _ = _confinement(
        tmp_path,
        [sys.executable, "-c", "raise SystemExit(0)"],
    )

    assert validate_phase1_confinement_record(launch.record) == launch.record
    assert launch.record["enforced"] is True
    assert launch.record["filesystem"]["host_root"] == "absent"
    assert launch.record["filesystem"]["unlisted_paths"] == "absent"
    assert launch.record["network"]["mode"] == "host-shared"
    assert all(
        mount["mode"] == "ro"
        for mount in launch.record["filesystem"]["mounts"]
        if mount["purpose"] in {"public-input", "python-package", "python-runtime"}
    )

    tampered = copy.deepcopy(launch.record)
    tampered["filesystem"]["host_root"] = "mounted"
    with pytest.raises(Phase1IsolationError, match="hash"):
        validate_phase1_confinement_record(tampered)

    extra_mount = copy.deepcopy(launch.record)
    insert_at = extra_mount["command"].index("--chdir")
    extra_mount["command"][insert_at:insert_at] = ["--ro-bind", "/", "/host"]
    extra_mount["policy_hash"] = canonical_json_hash(
        {key: value for key, value in extra_mount.items() if key != "policy_hash"}
    )
    with pytest.raises(Phase1IsolationError, match="unrecorded|host root"):
        validate_phase1_confinement_record(extra_mount)

    extra_namespace = copy.deepcopy(launch.record)
    insert_at = extra_namespace["command"].index("--chdir")
    extra_namespace["command"].insert(insert_at, "--unshare-net")
    extra_namespace["policy_hash"] = canonical_json_hash(
        {key: value for key, value in extra_namespace.items() if key != "policy_hash"}
    )
    with pytest.raises(Phase1IsolationError, match="exact frozen command"):
        validate_phase1_confinement_record(extra_namespace)


def test_phase1_confinement_fails_closed_when_bubblewrap_is_missing(tmp_path):
    public = tmp_path / "public"
    output = tmp_path / "output"
    public.mkdir()
    output.mkdir()

    with pytest.raises(Phase1IsolationError, match="bubblewrap"):
        build_phase1_confinement(
            inner_argv=[sys.executable, "-c", "raise SystemExit(0)"],
            cwd=public,
            public_roots=(public,),
            output_root=output,
            ledger_path=output / "ledger.jsonl",
            environ={},
            require_docker=False,
            bwrap_path=tmp_path / "missing-bwrap",
        )


def test_production_campaign_launch_records_and_executes_exact_confinement(
    tmp_path, monkeypatch
):
    work = tmp_path / "artifact" / "public-work"
    output = tmp_path / "artifact" / "campaigns" / "random"
    work.mkdir(parents=True)
    for name, content in (
        ("SKILL.md", "# Public skill\n"),
        ("properties.json", "[]\n"),
        ("protocol.json", "{}\n"),
    ):
        (work / name).write_text(content, encoding="utf-8")
    base_hash = file_hash(work / "SKILL.md")
    protocol_hash = canonical_json_hash(
        {
            "schema": "campaign-protocol/1",
            "protocol_id": "skillrace-issta-main-v1",
            "status": "frozen",
            "model": "qwen3.6-flash",
            "budget": 30,
            "bootstrap_count": 10,
            "max_generation_attempts_per_execution": 5,
            "seed_generator": {
                "batch_size": 5,
                "temperature": 0.9,
                "build_retries": 4,
            },
            "greybox_level": "L1",
            "random_seed": 20260711,
        }
    )
    request = CampaignLaunchRequest(
        method="random",
        skill_name="demo",
        skill_dir=work,
        base_image="skillrace/demo:base",
        properties_path=work / "properties.json",
        protocol_path=work / "protocol.json",
        output_dir=output,
        wall_clock=120,
        protocol_hash=protocol_hash,
        base_skill_hash=base_hash,
        base_package_hash="b" * 64,
        public_stage_hash="c" * 64,
    )
    observed = []

    def fake_execute(launch):
        observed.append(launch)
        _write_real_campaign2(
            output,
            "random",
            base_hash=base_hash,
            base_package_hash="b" * 64,
            public_stage_hash="c" * 64,
        )
        return subprocess.CompletedProcess(launch.command, 0, "", "")

    monkeypatch.setattr("skillrace.rq3_pipeline._execute_phase1_confinement", fake_execute)
    campaign_path = _campaign_launch(request, _default_campaign_runner)

    assert campaign_path == output / "campaign.json"
    assert len(observed) == 1
    saved = json.loads((output / "rq3-launch.json").read_text(encoding="utf-8"))
    assert saved["confinement"] == observed[0].record
    assert validate_phase1_confinement_record(saved["confinement"])
    assert saved["confinement"]["policy_hash"] == canonical_json_hash(
        {
            key: value
            for key, value in saved["confinement"].items()
            if key != "policy_hash"
        }
    )

    saved["confinement"]["filesystem"]["host_root"] = "mounted"
    (output / "rq3-launch.json").write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(ManifestMismatchError, match="identity mismatch|hash mismatch"):
        _campaign_launch(request, _default_campaign_runner)


def test_rq3_frozen_feedback_default_is_3600_bytes():
    assert run_rq3_scenario.__kwdefaults__["feedback_max_bytes"] == 3600


def test_rq3_rejects_nonfrozen_feedback_budget_before_any_execution(tmp_path):
    scenarios = tmp_path / "scenarios"
    scenario = scenarios / "demo"
    scenarios.mkdir()
    with pytest.raises(
        ManifestMismatchError, match="feedback budget must be exactly 3600"
    ):
        run_rq3_scenario(
            scenario_dir=scenario,
            scenarios_root=scenarios,
            out_dir=tmp_path / "artifact",
            protocol_path=tmp_path / "missing-protocol.json",
            feedback_max_bytes=24000,
        )


def test_production_revision_route_is_a_confined_child_not_an_in_process_call(
    tmp_path, monkeypatch
):
    base = tmp_path / "artifact" / "public-stage" / "base_skill"
    feedback = tmp_path / "artifact" / "feedback"
    revisions = tmp_path / "artifact" / "revisions"
    base.mkdir(parents=True)
    feedback.mkdir(parents=True)
    (base / "SKILL.md").write_text("# Base\n", encoding="utf-8")
    feedback_paths = {}
    for producer in ("random", "greybox", "skillrace"):
        path = feedback / f"{producer}.json"
        path.write_text("{}\n", encoding="utf-8")
        feedback_paths[producer] = path
    observed = []

    def fake_execute(launch):
        observed.append(launch)
        return subprocess.CompletedProcess(launch.command, 73, "", "stopped before API")

    monkeypatch.setattr("skillrace.rq3_pipeline._execute_phase1_confinement", fake_execute)
    with pytest.raises(RuntimeError, match="stopped before API"):
        _run_revision_phase(
            base_skill_dir=base,
            feedback_paths=feedback_paths,
            output_dir=revisions,
            revision_chat=chat,
        )

    assert len(observed) == 1
    assert observed[0].record["role"] == "revision"
    saved = json.loads(
        (revisions / "rq3-revision-launch.json").read_text(encoding="utf-8")
    )
    assert saved["execution_mode"] == "production-confined-child"
    assert saved["confinement"] == observed[0].record
    assert saved["accessible_artifact_roots"] == [
        str(base.resolve()),
        str(feedback.resolve()),
        str(revisions.resolve()),
    ]


def test_production_confirmation_route_is_a_confined_child(tmp_path, monkeypatch):
    campaign = tmp_path / "artifact" / "campaigns" / "skillrace"
    case = campaign / "cases" / "candidate-1"
    skill = tmp_path / "artifact" / "public-work"
    run_dir = (
        tmp_path
        / "artifact"
        / "confirmations"
        / "skillrace"
        / "clusters"
        / ("a" * 24)
        / "agent"
    )
    case.mkdir(parents=True)
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Public skill\n", encoding="utf-8")
    request = ConfirmationRequest(
        cluster_id="a" * 24,
        property_id="public-property",
        failure_signature="b" * 64,
        failure_summary="public failure",
        representative_execution_id="e0001",
        representative_attempt_id="e0001-a00",
        representative_candidate_id="candidate-1",
        case=str(case),
        run_dir=run_dir,
    )
    observed = []

    def fake_execute(launch):
        observed.append(launch)
        return subprocess.CompletedProcess(launch.command, 74, "", "confirmation stopped")

    monkeypatch.setattr("skillrace.rq3_pipeline._execute_phase1_confinement", fake_execute)
    with pytest.raises(RuntimeError, match="confirmation stopped"):
        _default_confirmation_executor(
            request,
            campaign_root=campaign,
            skill_dir=skill,
            model="qwen3.6-flash",
            wall_clock=120,
        )

    assert len(observed) == 1
    assert observed[0].record["role"] == "confirmation"
    saved = json.loads(
        (run_dir / "rq3-confirmation-launch.json").read_text(encoding="utf-8")
    )
    assert saved["confinement"] == observed[0].record
