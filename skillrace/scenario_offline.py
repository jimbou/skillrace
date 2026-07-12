"""Repeatable host-only validation for scenario checks and stored oracle overlays."""

from __future__ import annotations

import dataclasses
import argparse
import codecs
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence

from .scenario_audit import overlay_delete_paths


@dataclasses.dataclass(frozen=True)
class HeredocError:
    script: pathlib.Path
    index: int
    detail: str


@dataclasses.dataclass(frozen=True)
class HeredocReport:
    script_count: int
    heredoc_count: int
    errors: tuple[HeredocError, ...]


@dataclasses.dataclass(frozen=True)
class OfflineFailure:
    test_id: str
    variant: str
    criterion: str
    detail: str


@dataclasses.dataclass(frozen=True)
class OfflineReport:
    test_count: int
    starting_rejected: int
    reference_passed: int
    negative_assignments: int
    failures: tuple[OfflineFailure, ...]


_HEREDOC = re.compile(
    r"<<-?\s*(?P<quote>['\"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P=quote)\s*$"
)
_PYTHON_COMMAND = re.compile(r"(?:^|[\s/])python(?:3(?:\.\d+)*)?(?:\s|$)")


def extract_python_heredocs(source: str) -> tuple[str, ...]:
    """Extract shell heredocs fed directly to a Python interpreter."""
    lines = source.splitlines()
    snippets: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = _HEREDOC.search(line)
        if match is None or _PYTHON_COMMAND.search(line[: match.start()]) is None:
            index += 1
            continue
        tag = match.group("tag")
        body: list[str] = []
        index += 1
        while index < len(lines) and lines[index] != tag:
            body.append(lines[index])
            index += 1
        if index == len(lines):
            # Bash syntax validation reports this too, but retain a Python-gate error.
            body.append(f"raise SyntaxError('unterminated shell heredoc {tag}')")
        snippets.append("\n".join(body) + "\n")
        index += 1
    return tuple(snippets)


def audit_python_heredocs(root: str | pathlib.Path) -> HeredocReport:
    scripts = sorted(pathlib.Path(root).resolve().glob("*/tests/t*/checks/*.sh"))
    errors: list[HeredocError] = []
    count = 0
    for script in scripts:
        for index, snippet in enumerate(
            extract_python_heredocs(script.read_text(encoding="utf-8")), start=1
        ):
            count += 1
            try:
                compile(snippet, f"{script}:python-heredoc-{index}", "exec")
            except SyntaxError as error:
                errors.append(
                    HeredocError(
                        script=script,
                        index=index,
                        detail=f"line {error.lineno}: {error.msg}",
                    )
                )
    return HeredocReport(len(scripts), count, tuple(errors))


def offline_audit_root(root: str | pathlib.Path) -> OfflineReport:
    reports = [
        offline_audit_test(test_dir)
        for test_dir in sorted(pathlib.Path(root).resolve().glob("*/tests/t*"))
    ]
    return OfflineReport(
        test_count=len(reports),
        starting_rejected=sum(report.starting_rejected for report in reports),
        reference_passed=sum(report.reference_passed for report in reports),
        negative_assignments=sum(report.negative_assignments for report in reports),
        failures=tuple(failure for report in reports for failure in report.failures),
    )


def _safe_destination(root: pathlib.Path, relative: str) -> pathlib.Path:
    pure = pathlib.PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts or "." in pure.parts:
        raise ValueError(f"unsafe fixture path: {relative!r}")
    destination = (root / pathlib.Path(*pure.parts)).resolve()
    if root.resolve() not in destination.parents:
        raise ValueError(f"fixture path escapes workspace: {relative!r}")
    return destination


def _write_fixture(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _decode_printf_format(value: str) -> str:
    # Scenario Dockerfiles deliberately use ASCII backslash escapes in printf formats.
    return codecs.decode(value.encode("ascii"), "unicode_escape")


def materialize_fixture(dockerfile: pathlib.Path, workspace: pathlib.Path) -> None:
    """Interpret the benchmark's tiny, allowlisted Dockerfile fixture language."""
    lines = dockerfile.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line.startswith(("FROM ", "WORKDIR ")):
            index += 1
            continue
        if not line.startswith("RUN "):
            raise ValueError(f"unsupported Dockerfile instruction in {dockerfile}: {line}")
        command = line[4:]
        cat_match = re.fullmatch(
            r"cat > (?P<path>[A-Za-z0-9_.-]+) <<'(?P<tag>[A-Za-z0-9_]+)'",
            command,
        )
        if cat_match:
            tag = cat_match.group("tag")
            body: list[str] = []
            index += 1
            while index < len(lines) and lines[index] != tag:
                body.append(lines[index])
                index += 1
            if index == len(lines):
                raise ValueError(f"unterminated fixture heredoc {tag} in {dockerfile}")
            _write_fixture(
                _safe_destination(workspace, cat_match.group("path")),
                "\n".join(body) + "\n",
            )
            index += 1
            continue
        parts = shlex.split(command, posix=True)
        if not parts:
            raise ValueError(f"empty RUN command in {dockerfile}")
        if parts[0] == "printf":
            if ">" not in parts:
                raise ValueError(f"printf fixture lacks destination: {dockerfile}")
            redirect = parts.index(">")
            if redirect < 2 or redirect + 2 != len(parts):
                raise ValueError(f"unsupported printf fixture: {command}")
            fmt = _decode_printf_format(parts[1])
            values = tuple(parts[2:redirect])
            content = fmt % values if values else fmt
            _write_fixture(_safe_destination(workspace, parts[-1]), content)
        elif parts[0] == ":" and len(parts) == 3 and parts[1] == ">":
            _write_fixture(_safe_destination(workspace, parts[2]), "")
        elif parts[:2] == ["python3", "-c"] and len(parts) == 3:
            # The committed fixture program runs isolated, with no inherited Python
            # configuration, and only inside the disposable workspace.
            environment = {
                "HOME": str(workspace),
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "LANG": "C.UTF-8",
                "PYTHONNOUSERSITE": "1",
            }
            result = subprocess.run(
                [sys.executable, "-I", "-c", parts[2]],
                cwd=workspace,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            if result.returncode:
                raise RuntimeError(
                    f"fixture program failed in {dockerfile}: "
                    f"{(result.stdout + result.stderr)[-1000:]}"
                )
        else:
            raise ValueError(f"unsupported RUN command in {dockerfile}: {command}")
        index += 1


def apply_overlay(overlay: pathlib.Path, workspace: pathlib.Path) -> None:
    """Copy a stored overlay without symlinks or path traversal, then tombstone files."""
    for source in sorted(overlay.rglob("*")):
        if source.is_symlink():
            raise ValueError(f"overlay symlink is forbidden: {source}")
        if not source.is_file() or source.name == ".skillrace-delete":
            continue
        relative = source.relative_to(overlay).as_posix()
        destination = _safe_destination(workspace, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    for relative in overlay_delete_paths(overlay):
        destination = _safe_destination(workspace, relative)
        if destination.is_dir():
            shutil.rmtree(destination)
        elif destination.exists():
            destination.unlink()


def _run_check(
    script: pathlib.Path, workspace: pathlib.Path, staging: pathlib.Path
) -> subprocess.CompletedProcess[str]:
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / script.name
    source = script.read_text(encoding="utf-8")
    source = source.replace("/workspace", str(workspace))
    source = source.replace("/check/oracle", str(staging))
    source = source.replace("/__SKILLRACE_TRUSTED_PYTHON__", sys.executable)
    staged.write_text(source, encoding="utf-8")
    # Preserve a virtual-environment shim instead of resolving it to /usr/bin.
    python_dir = str(pathlib.Path(sys.executable).absolute().parent)
    environment = {
        "HOME": str(workspace),
        "PATH": python_dir + os.pathsep + "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONNOUSERSITE": "1",
    }
    return subprocess.run(
        ["bash", str(staged)],
        cwd=workspace,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    output = (result.stdout + result.stderr).strip()
    return f"exit={result.returncode}; {output[-1000:]}"


def offline_audit_test(test_dir: str | pathlib.Path) -> OfflineReport:
    test_dir = pathlib.Path(test_dir).resolve()
    manifest = json.loads((test_dir / "test.json").read_text(encoding="utf-8"))
    test_id = manifest["test_id"]
    criteria = {row["id"]: row for row in manifest["criteria"]}
    failures: list[OfflineFailure] = []
    starting_rejected = 0
    reference_passed = 0
    assignments = 0

    with tempfile.TemporaryDirectory(prefix="skillrace-offline-start-") as temporary:
        root = pathlib.Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        materialize_fixture(test_dir / "Dockerfile", workspace)
        statuses = [
            _run_check(test_dir / row["script"], workspace, root / "check").returncode
            for row in criteria.values()
        ]
        if any(status != 0 for status in statuses):
            starting_rejected = 1
        else:
            failures.append(
                OfflineFailure(test_id, "starting", "*", "all starting checks passed")
            )

    with tempfile.TemporaryDirectory(prefix="skillrace-offline-reference-") as temporary:
        root = pathlib.Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        materialize_fixture(test_dir / "Dockerfile", workspace)
        apply_overlay(test_dir / manifest["reference_overlay"], workspace)
        reference_failed = False
        for criterion_id, row in criteria.items():
            result = _run_check(test_dir / row["script"], workspace, root / "check")
            if result.returncode:
                reference_failed = True
                failures.append(
                    OfflineFailure(test_id, "reference", criterion_id, _detail(result))
                )
        if not reference_failed:
            reference_passed = 1

    negatives = {row["id"]: row for row in manifest["negative_implementations"]}
    for negative_id, negative in negatives.items():
        assigned = [
            (criterion_id, row)
            for criterion_id, row in criteria.items()
            if negative_id in row["negative_ids"]
        ]
        if not assigned:
            failures.append(
                OfflineFailure(test_id, negative_id, "*", "negative is not assigned")
            )
            continue
        with tempfile.TemporaryDirectory(prefix="skillrace-offline-negative-") as temporary:
            root = pathlib.Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            materialize_fixture(test_dir / "Dockerfile", workspace)
            apply_overlay(test_dir / negative["overlay"], workspace)
            for criterion_id, row in assigned:
                assignments += 1
                result = _run_check(test_dir / row["script"], workspace, root / "check")
                if result.returncode == 0:
                    failures.append(
                        OfflineFailure(
                            test_id,
                            negative_id,
                            criterion_id,
                            "assigned negative survived",
                        )
                    )
    return OfflineReport(
        test_count=1,
        starting_rejected=starting_rejected,
        reference_passed=reference_passed,
        negative_assignments=assignments,
        failures=tuple(failures),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--root", type=pathlib.Path)
    target.add_argument("--test", type=pathlib.Path)
    arguments = parser.parse_args(argv)
    if arguments.test is not None:
        report = offline_audit_test(arguments.test)
        heredocs = audit_python_heredocs(arguments.test.parents[2])
    else:
        report = offline_audit_root(arguments.root)
        heredocs = audit_python_heredocs(arguments.root)
    payload = {
        "tests": report.test_count,
        "starting_rejected": report.starting_rejected,
        "references_passed": report.reference_passed,
        "negative_assignments": report.negative_assignments,
        "python_heredocs": heredocs.heredoc_count,
        "heredoc_errors": [dataclasses.asdict(error) for error in heredocs.errors],
        "failures": [dataclasses.asdict(failure) for failure in report.failures],
    }
    print(json.dumps(payload, indent=2, default=str))
    return int(bool(report.failures or heredocs.errors))


if __name__ == "__main__":
    raise SystemExit(main())
