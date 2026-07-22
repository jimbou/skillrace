import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from skillrace_next.storage import file_hash
from skillrace_next.study_images import (
    DEFAULT_SOURCE_ROOT,
    PART1_SELECTION,
    PART2_SELECTION,
    build_study_images,
    capability_for_image,
    validate_image_sources,
)


def test_capability_lookup_binds_the_exact_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "skillrace-study-base-images/1",
                "images": [
                    {
                        "image_tag": (
                            "skillrace-next/study-part1-data-transform:2026-07-22"
                        ),
                        "capability_text": (
                            "Python 3.12, pytest, numpy, and pandas are installed."
                        ),
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    context = capability_for_image(
        "skillrace-next/study-part1-data-transform:2026-07-22", manifest
    )

    assert context.image_tag.endswith("data-transform:2026-07-22")
    assert "numpy" in context.text
    assert context.manifest_hash == file_hash(manifest)


def test_capability_lookup_rejects_an_unknown_study_image(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"schema":"skillrace-study-base-images/1","images":[]}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exactly one capability"):
        capability_for_image(
            "skillrace-next/study-part1-unknown:2026-07-22", manifest
        )


def test_capability_lookup_has_an_explicit_temporary_fixture_record() -> None:
    context = capability_for_image("skillrace-next/task-fixture:test")

    assert "Python 3" in context.text
    assert "Node.js" in context.text
    assert context.manifest_hash == "fixture"


def write_selection_files(root: Path) -> tuple[Path, Path]:
    part1 = root / "part1-selection.json"
    part2 = root / "part2-selection.json"
    part1.write_text(
        json.dumps({"selected": [{"skill_id": "alpha"}, {"skill_id": "beta"}]})
        + "\n",
        encoding="utf-8",
    )
    part2.write_text(
        json.dumps({"scenarios": [{"scenario_id": "gamma"}]}) + "\n",
        encoding="utf-8",
    )
    return part1, part2


def write_image_source(root: Path, part: str, context_id: str) -> None:
    directory = root / part / context_id
    directory.mkdir(parents=True)
    tag = f"skillrace-next/study-{part}-{context_id}:2026-07-22"
    (directory / "Dockerfile").write_text(
        "FROM python:3.12.13-bookworm AS checker-python\n"
        "RUN python -m pip install --no-cache-dir pytest==9.1.1\n"
        "FROM skillrace/pi-runtime:0.73.1\n"
        "COPY --from=checker-python /usr/local /usr/local\n"
        "WORKDIR /workspace\n",
        encoding="utf-8",
    )
    (directory / "capabilities.json").write_text(
        json.dumps(
            {
                "schema": "skillrace-study-base-image-source/1",
                "part": part,
                "context_id": context_id,
                "image_tag": tag,
                "base_image": "skillrace/pi-runtime:0.73.1",
                "capability_text": (
                    "Python 3.12 and pytest are installed. The root task agent may "
                    "install additional packages online within the unchanged task budget."
                ),
                "probes": [
                    "python3 --version",
                    "python3 -m pytest --version",
                    "node --version",
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_validate_image_sources_requires_exact_selection_coverage(
    tmp_path: Path,
) -> None:
    source = tmp_path / "images"
    part1, part2 = write_selection_files(tmp_path)
    for part, context_id in (
        ("part1", "alpha"),
        ("part1", "beta"),
        ("part2", "gamma"),
    ):
        write_image_source(source, part, context_id)

    records = validate_image_sources(source, part1, part2)

    assert [(item["part"], item["context_id"]) for item in records] == [
        ("part1", "alpha"),
        ("part1", "beta"),
        ("part2", "gamma"),
    ]

    write_image_source(source, "part2", "extra")
    with pytest.raises(ValueError, match="coverage"):
        validate_image_sources(source, part1, part2)


def test_validate_image_sources_rejects_task_fixture_content(tmp_path: Path) -> None:
    source = tmp_path / "images"
    part1, part2 = write_selection_files(tmp_path)
    for part, context_id in (
        ("part1", "alpha"),
        ("part1", "beta"),
        ("part2", "gamma"),
    ):
        write_image_source(source, part, context_id)
    dockerfile = source / "part1" / "alpha" / "Dockerfile"
    dockerfile.write_text(
        dockerfile.read_text(encoding="utf-8")
        + "RUN printf 'answer' > /workspace/result.txt\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="task fixture"):
        validate_image_sources(source, part1, part2)


def test_build_study_images_is_strictly_sequential(tmp_path: Path) -> None:
    source = tmp_path / "images"
    part1, part2 = write_selection_files(tmp_path)
    for part, context_id in (
        ("part1", "alpha"),
        ("part1", "beta"),
        ("part2", "gamma"),
    ):
        write_image_source(source, part, context_id)
    commands: list[list[str]] = []

    def runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        stdout = "sha256:" + str(len(commands)) if command[1:3] == ["image", "inspect"] else "ok"
        return subprocess.CompletedProcess(command, 0, stdout + "\n", "")

    manifest = build_study_images(
        source,
        tmp_path / "evidence",
        "run-1",
        part1_selection=part1,
        part2_selection=part2,
        command_runner=runner,
    )

    assert [command[1] for command in commands] == [
        "build",
        "image",
        "run",
        "build",
        "image",
        "run",
        "build",
        "image",
        "run",
    ]
    frozen = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(frozen["images"]) == 3
    assert all(item["image_id"].startswith("sha256:") for item in frozen["images"])


def test_build_study_images_can_start_late_without_writing_final_manifest(
    tmp_path: Path,
) -> None:
    source = tmp_path / "images"
    part1, part2 = write_selection_files(tmp_path)
    for part, context_id in (
        ("part1", "alpha"),
        ("part1", "beta"),
        ("part2", "gamma"),
    ):
        write_image_source(source, part, context_id)
    commands: list[list[str]] = []

    def runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        stdout = "sha256:resume" if command[1:3] == ["image", "inspect"] else "ok"
        return subprocess.CompletedProcess(command, 0, stdout + "\n", "")

    partial = build_study_images(
        source,
        tmp_path / "evidence",
        "run-1",
        start_ordinal=2,
        part1_selection=part1,
        part2_selection=part2,
        command_runner=runner,
    )

    assert [command[1] for command in commands] == [
        "build",
        "image",
        "run",
        "build",
        "image",
        "run",
    ]
    assert "study-part1-beta" in commands[0][3]
    assert partial.name == "partial-manifest.json"
    summary = json.loads(partial.read_text(encoding="utf-8"))
    assert summary["schema"] == "skillrace-study-base-images-partial/1"
    assert summary["start_ordinal"] == 2
    assert summary["image_count"] == 2
    assert not (source / "manifest.json").exists()
    assert not (tmp_path / "evidence" / "run-1" / "01-part1-alpha").exists()


def test_build_study_images_stops_without_manifest_after_build_failure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "images"
    part1, part2 = write_selection_files(tmp_path)
    for part, context_id in (
        ("part1", "alpha"),
        ("part1", "beta"),
        ("part2", "gamma"),
    ):
        write_image_source(source, part, context_id)
    commands: list[list[str]] = []

    def runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 1, "", "build failed")

    with pytest.raises(RuntimeError, match="Docker build failed"):
        build_study_images(
            source,
            tmp_path / "evidence",
            "run-1",
            part1_selection=part1,
            part2_selection=part2,
            command_runner=runner,
        )

    assert len(commands) == 1
    assert not (source / "manifest.json").exists()


def test_build_study_images_preserves_partial_output_after_build_timeout(
    tmp_path: Path,
) -> None:
    source = tmp_path / "images"
    part1, part2 = write_selection_files(tmp_path)
    for part, context_id in (
        ("part1", "alpha"),
        ("part1", "beta"),
        ("part2", "gamma"),
    ):
        write_image_source(source, part, context_id)

    def runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert kwargs["timeout"] == 7200
        raise subprocess.TimeoutExpired(
            command,
            kwargs["timeout"],
            output="partial build output\n",
            stderr="mirror stalled\n",
        )

    evidence = tmp_path / "evidence"
    with pytest.raises(RuntimeError, match="Docker build timed out.*part1/alpha"):
        build_study_images(
            source,
            evidence,
            "run-1",
            part1_selection=part1,
            part2_selection=part2,
            command_runner=runner,
        )

    context = evidence / "run-1" / "01-part1-alpha"
    assert (context / "build.stdout.txt").read_text(encoding="utf-8") == (
        "partial build output\n"
    )
    assert (context / "build.stderr.txt").read_text(encoding="utf-8") == (
        "mirror stalled\n"
    )
    failed = json.loads((context / "receipt.json").read_text(encoding="utf-8"))
    assert failed["status"] == "failed"
    assert failed["failure"] == "build_timeout"
    assert not (source / "manifest.json").exists()


def test_repository_sources_cover_all_selected_contexts_with_required_tools() -> None:
    records = validate_image_sources(
        DEFAULT_SOURCE_ROOT, PART1_SELECTION, PART2_SELECTION
    )

    assert len(records) == 40
    assert len({item["image_tag"] for item in records}) == 40
    by_context = {
        (item["part"], item["context_id"]): " && ".join(item["probes"])
        for item in records
    }
    compiler = by_context[("part1", "compiler-hardening")]
    for command in ("gcc", "g++", "clang", "cmake", "readelf"):
        assert command in compiler
    for context_id in (
        "code-refactor-fowler",
        "condition-based-waiting",
        "debugging-difficult-bugs",
    ):
        probes = by_context[("part1", context_id)]
        assert "tsc" in probes
        assert "ts-node" in probes
    assert "import numpy, pandas" in by_context[("part1", "data-transform")]
    assert "import fastapi" in by_context[("part1", "fastapi-endpoint")]
    assert "sqlmodel" in by_context[("part1", "sqlmodel-orm")]
    assert "import yaml" in by_context[("part1", "yaml-config")]
    assert "g++" in by_context[("part1", "validator-agent")]
    for context_id in ("sql-queries", "sql-query-generator"):
        assert "sqlite3" in by_context[("part1", context_id)]
    assert "sqlite3" in by_context[("part2", "sqlite-query")]

    apt_contexts = {
        ("part1", "compiler-hardening"),
        ("part1", "sql-queries"),
        ("part1", "sql-query-generator"),
        ("part1", "sqlmodel-orm"),
        ("part1", "validator-agent"),
        ("part2", "sqlite-query"),
    }
    for part, context_id in apt_contexts:
        dockerfile = (
            DEFAULT_SOURCE_ROOT / part / context_id / "Dockerfile"
        ).read_text(encoding="utf-8")
        assert "https://mirrors.aliyun.com/debian-security" in dockerfile
        assert "https://mirrors.aliyun.com/debian" in dockerfile

    for context_id in ("fastapi-endpoint", "sqlmodel-orm"):
        dockerfile = (
            DEFAULT_SOURCE_ROOT / "part1" / context_id / "Dockerfile"
        ).read_text(encoding="utf-8")
        assert "--mount=type=cache,target=/root/.cache/pip" in dockerfile
        assert "--timeout 600" in dockerfile
        assert "--no-cache-dir" not in dockerfile
