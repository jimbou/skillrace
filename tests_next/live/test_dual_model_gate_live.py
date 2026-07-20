from datetime import UTC, datetime
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from skillrace_next.runtime.pi import (
    PI_RUNTIME_IMAGE,
    PiRequest,
    direct_provider_preflight,
    run_pi,
)
from skillrace_next.storage import atomic_write_json


pytestmark = pytest.mark.live


def run_slice(
    component: str,
    model: str,
    evidence: Path,
) -> tuple[Path, int]:
    secret = os.environ.get("LAB_KEY_UNLIMITED", "")
    component_root = Path("out/live-contracts") / component / model
    component_root.mkdir(parents=True, exist_ok=True)
    before = {path.name for path in component_root.iterdir() if path.is_dir()}
    selector = "deepseek" if model == "deepseek-v4-flash" else "qwen"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        f"tests_next/live/test_{component}_tiny_live.py",
        "--live",
        "-k",
        selector,
        "-v",
        "-s",
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=2400,
    )
    (evidence / f"{component}.stdout.txt").write_text(
        completed.stdout.replace(secret, "[REDACTED]"), encoding="utf-8"
    )
    (evidence / f"{component}.stderr.txt").write_text(
        completed.stderr.replace(secret, "[REDACTED]"), encoding="utf-8"
    )
    created = sorted(
        path
        for path in component_root.iterdir()
        if path.is_dir() and path.name not in before
    )
    if len(created) != 1:
        raise RuntimeError(f"{component} slice did not create exactly one evidence run")
    return created[0], completed.returncode


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "qwen3.6-flash"])
def test_final_lab_model_gate_runs_fresh_preflights_and_bounded_slices(
    model: str, live_evidence_root: Path
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the dual-model gate")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "dual-model-gate" / model / run_id
    workspace = evidence / "preflight-workspace"
    workspace.mkdir(parents=True)
    (workspace / "input.txt").write_text("dual-model-gate\n", encoding="utf-8")
    prompt = evidence / "pi-prompt.txt"
    prompt.write_text(
        "Read /workspace/input.txt, then write exactly DUAL_MODEL_PI_OK followed by one "
        "newline to /workspace/output.txt. Perform both tool calls and stop.\n",
        encoding="utf-8",
    )
    gate = {
        "schema": "skillrace-dual-model-gate/1",
        "provider": "lab",
        "model": model,
        "status": "blocked",
        "direct_receipt": None,
        "pi_receipt": None,
        "part1_evidence": None,
        "part2_evidence": None,
    }
    try:
        direct = direct_provider_preflight("lab", model, evidence / "direct-preflight")
        gate["direct_receipt"] = str(direct.receipt_path)
        if direct.status != "completed":
            raise RuntimeError(f"direct preflight failed: {direct.status}")
        pi = run_pi(
            PiRequest(
                operation_id=f"dual-model-gate.{model}.{run_id}",
                provider="lab",
                model=model,
                prompt_path=prompt,
                output_dir=evidence / "pi-preflight",
                image=PI_RUNTIME_IMAGE,
                allowed_tools=("read", "write"),
                max_turns=4,
                timeout_seconds=240,
                mounts=((workspace, "/workspace", "rw"),),
            )
        )
        gate["pi_receipt"] = str(pi.receipt_path)
        if pi.status != "completed":
            raise RuntimeError(f"Pi preflight failed: {pi.status}")
        if (workspace / "output.txt").read_text(encoding="utf-8") != "DUAL_MODEL_PI_OK\n":
            raise RuntimeError("Pi preflight artifact is incorrect")
        part1_evidence, part1_status = run_slice("part1", model, evidence)
        gate["part1_evidence"] = str(part1_evidence)
        if part1_status != 0:
            raise RuntimeError(f"part1 slice failed with {part1_status}")
        part2_evidence, part2_status = run_slice("part2", model, evidence)
        gate["part2_evidence"] = str(part2_evidence)
        if part2_status != 0:
            raise RuntimeError(f"part2 slice failed with {part2_status}")
        gate["status"] = "completed"
    except Exception as error:
        gate["error"] = f"{type(error).__name__}: {error}"[-1000:]
        atomic_write_json(evidence / "gate.json", gate)
        raise
    atomic_write_json(evidence / "gate.json", gate)
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
