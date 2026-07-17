import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

import pytest

from skillrace_next.records import CheckBundle
from skillrace_next.runtime.docker import (
    ContainerSpec,
    remove_container,
    start_task_container,
)
from skillrace_next.storage import atomic_write_json, tree_hash
from skillrace_next.verification.executor import execute_checks


@pytest.fixture(scope="module")
def task_image() -> tuple[str, str]:
    tag = "skillrace-next/task-fixture:test"
    fixture = Path("tests_next/fixtures/task").resolve()
    subprocess.run(
        ["docker", "build", "--network=none", "-q", "-t", tag, str(fixture)],
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    inspected = subprocess.run(
        ["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return tag, inspected.stdout.strip()


def start_container(task_image: tuple[str, str], artifact: Path):
    tag, image_id = task_image
    spec = ContainerSpec(
        name="skillrace-next-check-" + uuid.uuid4().hex[:12],
        image=tag,
        image_id=image_id,
        mounts=((artifact, "/workspace", "rw"),),
        network="none",
        cpus="1",
        memory="256m",
        working_directory="/workspace",
    )
    return start_task_container(spec)


def write_checker(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")


def make_bundle(
    root: Path,
    artifact_hash: str,
    declarations: list[tuple[str, int]],
) -> CheckBundle:
    checks_dir = root / "checks"
    checks_dir.mkdir(parents=True)
    checks: list[dict[str, object]] = []
    scripts: list[Path] = []
    for check_id, timeout in declarations:
        script = checks_dir / f"{check_id}.py"
        scripts.append(script)
        checks.append(
            {
                "check_id": check_id,
                "property_id": check_id.split("-")[0],
                "script": f"checks/{check_id}.py",
                "argv": [
                    "python3",
                    f"/tmp/skillrace-checks/checks/{check_id}.py",
                    "/workspace",
                ],
                "timeout_seconds": timeout,
                "purpose": f"Exercise {check_id}",
                "pass_condition": "Fixture-specific condition holds",
                "failure_condition": "Fixture-specific condition does not hold",
                "root_cause_category": "format_contract",
            }
        )
    manifest = {
        "schema": "skillrace-check-bundle/1",
        "run_id": "integration-check-run",
        "artifact_hash": artifact_hash,
        "checks": checks,
        "uncovered": [],
    }
    manifest_path = root / "check_manifest.json"
    atomic_write_json(manifest_path, manifest)
    receipt = root / "codex-events.jsonl"
    receipt.write_text("{}\n", encoding="utf-8")
    return CheckBundle(
        bundle_id="bundle-integration",
        run_id="integration-check-run",
        artifact_hash=artifact_hash,
        input_hashes={"artifact": artifact_hash},
        manifest_path=manifest_path,
        script_paths=tuple(scripts),
        codex_receipt_path=receipt,
    )


def assert_container_removed(container_id: str) -> None:
    inspected = subprocess.run(
        ["docker", "inspect", container_id],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert inspected.returncode != 0


def test_execute_checks_maps_results_and_removes_container(
    tmp_path: Path, task_image: tuple[str, str]
) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "result.txt").write_text("ok\n", encoding="utf-8")
    bundle = make_bundle(
        tmp_path / "bundle",
        tree_hash(artifact),
        [
            ("P1-C1", 5),
            ("P2-C1", 5),
            ("P3-C1", 5),
            ("P4-C1", 5),
            ("P5-C1", 1),
        ],
    )
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    manifest["checks"][1]["argv"][1] = "checks/P2-C1.py"
    atomic_write_json(bundle.manifest_path, manifest)
    write_checker(
        bundle.script_paths[0],
        "import json, os\n"
        "from pathlib import Path\n"
        "scratch = Path(os.environ['TMPDIR']) / 'probe.txt'\n"
        "scratch.write_text('ok', encoding='utf-8')\n"
        "valid = os.getuid() == 65534 and scratch.read_text() == 'ok'\n"
        "print(json.dumps({'diagnostic': 'restricted checker and scratch verified', "
        "'evidence_paths': []}))\n"
        "raise SystemExit(0 if valid else 1)\n",
    )
    write_checker(
        bundle.script_paths[1],
        "import json\n"
        "print(json.dumps({'diagnostic': 'fixture failure', 'evidence_paths': []}))\n"
        "raise SystemExit(1)\n",
    )
    write_checker(
        bundle.script_paths[2],
        "import json\n"
        "print(json.dumps({'diagnostic': 'fixture inconclusive', 'evidence_paths': []}))\n"
        "raise SystemExit(2)\n",
    )
    write_checker(bundle.script_paths[3], "print('not-json')\nraise SystemExit(1)\n")
    write_checker(bundle.script_paths[4], "import time\ntime.sleep(5)\n")
    running = start_container(task_image, artifact)

    try:
        results = execute_checks(running, artifact, bundle, tmp_path / "results")
    except BaseException:
        remove_container(running)
        raise

    assert results.artifact_unchanged
    assert results.artifact_hash_before == results.artifact_hash_after
    assert [item["status"] for item in results.results] == [
        "pass",
        "fail",
        "inconclusive",
        "inconclusive",
        "inconclusive",
    ]
    assert (tmp_path / "results" / "check_results.json").is_file()
    assert len(list((tmp_path / "results" / "outputs").glob("*.stdout"))) == 5
    assert len(list((tmp_path / "results" / "outputs").glob("*.stderr"))) == 5
    assert_container_removed(running.container_id)


def test_artifact_mutation_invalidates_every_checker_outcome(
    tmp_path: Path, task_image: tuple[str, str]
) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    target = artifact / "result.txt"
    target.write_text("before\n", encoding="utf-8")
    bundle = make_bundle(
        tmp_path / "bundle",
        tree_hash(artifact),
        [("P1-C1", 5)],
    )
    write_checker(
        bundle.script_paths[0],
        "import json, time\n"
        "time.sleep(2)\n"
        "print(json.dumps({'diagnostic': 'would pass', 'evidence_paths': []}))\n",
    )
    running = start_container(task_image, artifact)

    def mutate_artifact() -> None:
        time.sleep(1)
        target.chmod(0o644)
        target.write_text("after\n", encoding="utf-8")

    mutation = threading.Thread(target=mutate_artifact)
    mutation.start()
    try:
        results = execute_checks(running, artifact, bundle, tmp_path / "results")
    except BaseException:
        remove_container(running)
        raise
    finally:
        mutation.join(timeout=5)

    assert not results.artifact_unchanged
    assert results.artifact_hash_before != results.artifact_hash_after
    assert results.results[0]["status"] == "inconclusive"
    assert "artifact changed" in results.results[0]["diagnostic"]
    stored = json.loads(results.results_path.read_text(encoding="utf-8"))
    assert stored["artifact_unchanged"] is False
    assert stored["results"][0]["status"] == "inconclusive"
    assert_container_removed(running.container_id)


def test_bundle_artifact_hash_mismatch_is_inconclusive(
    tmp_path: Path, task_image: tuple[str, str]
) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "result.txt").write_text("current\n", encoding="utf-8")
    bundle = make_bundle(tmp_path / "bundle", "0" * 64, [("P1-C1", 5)])
    write_checker(
        bundle.script_paths[0],
        "import json\n"
        "print(json.dumps({'diagnostic': 'would pass', 'evidence_paths': []}))\n",
    )
    running = start_container(task_image, artifact)

    try:
        results = execute_checks(running, artifact, bundle, tmp_path / "results")
    except BaseException:
        remove_container(running)
        raise

    assert results.artifact_unchanged
    assert results.results[0]["status"] == "inconclusive"
    assert "artifact hash" in results.results[0]["diagnostic"]
    assert_container_removed(running.container_id)
