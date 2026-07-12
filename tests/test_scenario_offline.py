from __future__ import annotations

import hashlib
import json
from pathlib import Path
import os
import subprocess
import sys

from skillrace.scenario_offline import (
    audit_python_heredocs,
    offline_audit_root,
)


ROOT = Path(__file__).parents[1] / "scenarios"


def test_every_python_heredoc_in_all_checks_compiles():
    report = audit_python_heredocs(ROOT)
    assert report.script_count == 192
    assert report.heredoc_count > 0
    assert report.errors == ()


def test_offline_reference_and_assigned_negative_matrix():
    report = offline_audit_root(ROOT)
    assert report.test_count == 100
    assert report.starting_rejected == 100
    assert report.reference_passed == 100
    assert report.negative_assignments >= 193
    assert report.failures == ()


def test_no_test_contains_byte_identical_criterion_scripts():
    duplicates = []
    for test_dir in sorted(ROOT.glob("*/tests/t*")):
        by_hash: dict[str, list[str]] = {}
        for script in sorted((test_dir / "checks").glob("*.sh")):
            digest = hashlib.sha256(script.read_bytes()).hexdigest()
            by_hash.setdefault(digest, []).append(script.name)
        duplicates.extend(
            (str(test_dir), tuple(names))
            for names in by_hash.values()
            if len(names) > 1
        )
    assert duplicates == []


def test_csv_missing_input_probes_are_public_and_kill_silent_behavior():
    for candidate in sorted(ROOT.glob("csv-stats/tests/t*/candidate.json")):
        prompt = json.loads(candidate.read_text(encoding="utf-8"))["prompt"]
        assert "missing file" in prompt
        assert "requested column is absent" in prompt
        assert "exit non-zero" in prompt
    t1 = ROOT / "csv-stats/tests/t1"
    t2 = ROOT / "csv-stats/tests/t2"
    assert "--column absent" in (t1 / "checks/count.sh").read_text(encoding="utf-8")
    assert "missing.csv" in (t2 / "checks/count-present.sh").read_text(encoding="utf-8")
    m1 = json.loads((t1 / "test.json").read_text(encoding="utf-8"))
    m2 = json.loads((t2 / "test.json").read_text(encoding="utf-8"))
    c1 = next(row for row in m1["criteria"] if row["id"] == "count")
    c2 = next(row for row in m2["criteria"] if row["id"] == "count-present")
    assert "silent-missing-column" in c1["negative_ids"]
    assert "silent-missing-file" in c2["negative_ids"]


def test_fix_integrity_rejects_pytest_package_shadowing_and_uses_isolation():
    for test_dir in sorted(ROOT.glob("fix-failing-test/tests/t*")):
        integrity = (test_dir / "checks/tests-unedited.sh").read_text(encoding="utf-8")
        suite = (test_dir / "checks/suite-passes.sh").read_text(encoding="utf-8")
        assert "'pytest'" in integrity and "'_pytest'" in integrity
        assert "sitecustomize.py" in integrity and "usercustomize.py" in integrity
        assert " -I -m pytest " in suite
        assert "env -i" in suite
        manifest = json.loads((test_dir / "test.json").read_text(encoding="utf-8"))
        negatives = {row["id"]: row for row in manifest["negative_implementations"]}
        assert "pytest-shadow" in negatives
        integrity_criterion = next(
            row for row in manifest["criteria"] if row["id"] == "tests-unedited"
        )
        suite_criterion = next(
            row for row in manifest["criteria"] if row["id"] == "suite-passes"
        )
        assert "pytest-shadow" in integrity_criterion["negative_ids"]
        assert "pytest-shadow" in suite_criterion["negative_ids"]
        overlay = test_dir / negatives["pytest-shadow"]["overlay"]
        package = overlay / "pytest"
        assert (package / "__init__.py").is_file()
        assert (package / "__main__.py").is_file()
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        resolved = subprocess.run(
            [
                sys.executable,
                "-c",
                "import importlib.util; print(importlib.util.find_spec('pytest').origin)",
            ],
            cwd=overlay,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert resolved.returncode == 0
        assert resolved.stdout.strip() == str(package / "__init__.py")
        spoofed = subprocess.run(
            [sys.executable, "-m", "pytest"],
            cwd=overlay,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert spoofed.returncode == 0
        assert " passed in " in spoofed.stdout
