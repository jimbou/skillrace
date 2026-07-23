from __future__ import annotations

import json
import pathlib
import subprocess

from skillrace.io_utils import canonical_json_hash
from skillrace.pi_patcher import make_pi_patcher
from skillrace.repair_validation import FailureRepairRequest
from skillrace.revise_skill import package_hash


ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_guided_runner_uses_sdk_and_enforces_read_before_one_skill_mutation():
    runner = ROOT / "images/pi-base/guided_patch.mjs"

    syntax = subprocess.run(
        ["node", "--check", str(runner)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert syntax.returncode == 0, syntax.stderr
    source = runner.read_text(encoding="utf-8")
    for required in (
        "createAgentSession",
        "DefaultResourceLoader",
        'tools: allowedTools',
        'event.toolName === "read"',
        'event.toolName === "edit" || event.toolName === "write"',
        "requiredReads",
        "!requiredReads.has(toolPath)",
        'event.toolName === "grep" && requiredReads.size > 0',
        "mutationCount",
        "session.abort()",
        "Both required inputs are now complete; make the single SKILL.md edit now",
        'pi.setActiveTools(["edit", "write"])',
        "created.extensionsResult.errors",
        'pi.on("after_provider_response"',
        "const repairPolicy = (pi) =>",
        "extensionFactories: [repairPolicy]",
        "noExtensions: true",
        "noSkills: true",
        "noPromptTemplates: true",
        "noContextFiles: true",
    ):
        assert required in source


def test_pi_patcher_stages_guided_read_then_single_edit(
    tmp_path,
):
    original = tmp_path / "original"
    original.mkdir()
    (original / "SKILL.md").write_text("# Original\n", encoding="utf-8")
    request = FailureRepairRequest(
        method="skillrace", skill_name="demo", execution_id="e1", attempt_id="a1",
        candidate_id="c1", case_dir=tmp_path / "case", original_skill_dir=original,
        original_skill_hash=package_hash(original), failed_property_ids=("p",),
        failure_signatures=("a" * 64,), run_dir=tmp_path / "run",
        output_dir=tmp_path / "out", repair_id="repair-one",
    )
    payload = {
        "schema": "skillrace-failure-repair-evidence/1",
        "original_skill_hash": request.original_skill_hash,
        "failure_core": {"task": "exact task"},
        "method_evidence": {"reasoning_episodes": [{"reasoning": "wrong branch"}]},
    }
    evidence = {"reviser_payload": payload, "evidence_hash": canonical_json_hash(payload)}
    calls = []
    staged = {}

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        mounts = [argv[index + 1] for index, item in enumerate(argv) if item == "-v"]
        skill_mount = next(item for item in mounts if item.endswith(":/workspace:rw"))
        skill = pathlib.Path(skill_mount.split(":", 1)[0])
        context_mount = next(
            item for item in mounts if item.endswith(":/evidence/repair-context.json:ro")
        )
        prompt_mount = next(
            item for item in mounts if item.endswith(":/evidence/repair-prompt.txt:ro")
        )
        staged["context"] = pathlib.Path(context_mount.split(":", 1)[0]).read_text()
        staged["prompt"] = pathlib.Path(prompt_mount.split(":", 1)[0]).read_text()
        (skill / "SKILL.md").write_text("# Fixed by Pi\n", encoding="utf-8")
        accounting_mount = next(item for item in mounts if item.endswith(":/accounting:rw"))
        accounting = pathlib.Path(accounting_mount.split(":", 1)[0])
        (accounting / "2026-07-14-guided-session.jsonl").write_text(
            json.dumps({"message": {"role": "assistant", "usage": {
                "input": 12, "output": 4, "cacheRead": 30, "totalTokens": 46
            }}}) + "\n",
            encoding="utf-8",
        )
        (accounting / "guided-summary.json").write_text(json.dumps({
            "status": "completed", "turn_count": 1, "tool_call_count": 3,
            "mutation_count": 1, "required_reads_remaining": 0,
            "blocked_call_count": 0,
        }))
        (accounting / "guided-events.jsonl").write_text("\n".join(
            json.dumps({"type": "tool_call", "tool": tool})
            for tool in ("read", "read", "edit")
        ) + "\n")
        return subprocess.CompletedProcess(argv, 0, stdout="done", stderr="")

    patcher = make_pi_patcher(
        model="glm-4.5-flash", timeout_seconds=120,
        image="skillrace/pi-base:test", run_fn=fake_run,
        cleanup_fn=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0),
    )
    work = tmp_path / "work"
    result = patcher(request, evidence, work)

    assert result["status"] == "completed"
    argv, kwargs = calls[0]
    environment = {
        argv[index + 1]
        for index, item in enumerate(argv)
        if item == "-e"
    }
    assert "PI_ALLOWED_TOOLS=read,grep,edit,write" in environment
    assert "PI_THINKING_LEVEL=medium" in environment
    assert "PI_MAX_TURNS=10" in environment
    assert "PI_REPAIR_SKILL_PATH=/workspace/SKILL.md" in environment
    assert "PI_REPAIR_CONTEXT_PATH=/evidence/repair-context.json" in environment
    assert "bash" not in " ".join(argv)
    assert argv[-2:] == ["node", "/runtime/guided_patch.mjs"]
    assert kwargs["timeout"] == 120
    assert any(item.endswith(":/evidence/repair-context.json:ro") for item in argv)
    assert any(item.endswith(":/runtime/guided_patch.mjs:ro") for item in argv)
    context = json.loads(staged["context"])
    assert context["common"]["failure_core"] == {"task": "exact task"}
    assert context["method_evidence"] == {
        "reasoning_episodes": [{"reasoning": "wrong branch"}]
    }
    prompt = staged["prompt"]
    assert "/workspace/SKILL.md" in prompt
    assert "/evidence/repair-context.json" in prompt
    assert "read" in prompt.lower() and "edit" in prompt.lower()
    assert "# Original" not in prompt
    assert "wrong branch" not in prompt
    assert not (work / "accounting" / "session.jsonl").exists()
    assert result["input_tokens"] == 12 and result["output_tokens"] == 4
    assert result["cache_read_tokens"] == 30
    assert result["turns"] == 1
    assert result["pi_tool_call_count"] == 3
    assert result["pi_mutation_count"] == 1
    assert result["pi_required_reads_remaining"] == 0
    assert result["pi_blocked_call_count"] == 0


def test_pi_patcher_keeps_valid_edit_when_provider_fails_only_after_write(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    (original / "SKILL.md").write_text("# Original\n", encoding="utf-8")
    request = FailureRepairRequest(
        method="skillrace", skill_name="demo", execution_id="e1", attempt_id="a1",
        candidate_id="c1", case_dir=tmp_path, original_skill_dir=original,
        original_skill_hash=package_hash(original), failed_property_ids=("p",),
        failure_signatures=("a" * 64,), run_dir=tmp_path,
        output_dir=tmp_path / "out", repair_id="repair-one",
    )
    payload = {"schema": "skillrace-failure-repair-evidence/1",
               "original_skill_hash": request.original_skill_hash,
               "failure_core": {}, "method_evidence": {}}
    evidence = {"reviser_payload": payload, "evidence_hash": canonical_json_hash(payload)}

    def failed_after_write(argv, **_kwargs):
        mount = next(argv[i + 1] for i, item in enumerate(argv) if item == "-v" and argv[i + 1].endswith(":/workspace:rw"))
        (pathlib.Path(mount.split(":", 1)[0]) / "SKILL.md").write_text("# Fixed\n")
        return subprocess.CompletedProcess(argv, 1, "", "provider failed after tool result")

    patcher = make_pi_patcher(
        model="glm-4.5-flash", image="skillrace/pi-base:test",
        run_fn=failed_after_write,
        cleanup_fn=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0),
    )
    result = patcher(request, evidence, tmp_path / "work")
    assert result["status"] == "completed"


def test_pi_patcher_snapshots_usage_before_timeout_cleanup_can_remove_session(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    (original / "SKILL.md").write_text("# Original\n", encoding="utf-8")
    request = FailureRepairRequest(
        method="skillrace", skill_name="demo", execution_id="e1", attempt_id="a1",
        candidate_id="c1", case_dir=tmp_path, original_skill_dir=original,
        original_skill_hash=package_hash(original), failed_property_ids=("p",),
        failure_signatures=("a" * 64,), run_dir=tmp_path,
        output_dir=tmp_path / "out", repair_id="repair-timeout",
    )
    payload = {"schema": "skillrace-failure-repair-evidence/1",
               "original_skill_hash": request.original_skill_hash,
               "failure_core": {}, "method_evidence": {}}
    evidence = {"reviser_payload": payload, "evidence_hash": canonical_json_hash(payload)}
    accounting = None

    def timeout_after_usage(argv, **kwargs):
        nonlocal accounting
        mount = next(
            argv[i + 1]
            for i, item in enumerate(argv)
            if item == "-v" and argv[i + 1].endswith(":/accounting:rw")
        )
        accounting = pathlib.Path(mount.split(":", 1)[0])
        (accounting / "generated-session.jsonl").write_text(
            json.dumps({"message": {"role": "assistant", "usage": {
                "input": 120, "output": 40, "cacheRead": 10
            }}}) + "\n"
        )
        (accounting / "guided-events.jsonl").write_text(
            json.dumps({"type": "turn_end", "turn": 1}) + "\n"
        )
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    def destructive_cleanup(*args, **kwargs):
        assert accounting is not None
        for artifact in accounting.iterdir():
            artifact.unlink()
        return subprocess.CompletedProcess(args, 0)

    patcher = make_pi_patcher(
        model="glm-4.5-flash", image="skillrace/pi-base:test",
        run_fn=timeout_after_usage, cleanup_fn=destructive_cleanup,
    )

    result = patcher(request, evidence, tmp_path / "work")

    assert result["status"] == "timeout"
    assert result["input_tokens"] == 120
    assert result["output_tokens"] == 40
    assert result["cache_read_tokens"] == 10
    assert result["turns"] == 1
    assert result["pi_last_event_type"] == "turn_end"
    assert result["cost_provider_credits"] > 0
