from pathlib import Path
import stat

from skillrace_next.runtime.artifacts import (
    freeze_artifact,
    verify_artifact_unchanged,
)


def test_freeze_artifact_preserves_partial_tree_and_stable_hash(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    nested = artifact / "nested"
    nested.mkdir(parents=True)
    partial = nested / "partial.txt"
    partial.write_text("unfinished output\n", encoding="utf-8")

    frozen = freeze_artifact(artifact, checker_uid=65534)

    assert frozen.path == artifact
    assert frozen.checker_uid == 65534
    assert partial.read_text(encoding="utf-8") == "unfinished output\n"
    assert freeze_artifact(artifact, checker_uid=65534).tree_hash == frozen.tree_hash
    assert verify_artifact_unchanged(frozen)


def test_freeze_removes_write_bits_for_a_different_uid(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    output = artifact / "result.txt"
    output.write_text("complete\n", encoding="utf-8")

    freeze_artifact(artifact, checker_uid=65534)

    write_bits = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
    assert artifact.stat().st_mode & write_bits == 0
    assert output.stat().st_mode & write_bits == 0


def test_verify_artifact_unchanged_detects_content_mutation(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    output = artifact / "result.txt"
    output.write_text("before\n", encoding="utf-8")
    frozen = freeze_artifact(artifact, checker_uid=65534)

    output.chmod(0o644)
    output.write_text("after\n", encoding="utf-8")

    assert not verify_artifact_unchanged(frozen)


def test_freeze_preserves_and_hashes_a_broken_symlink(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    bin_dir = artifact / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    link = bin_dir / "python"
    link.symlink_to("python3")

    frozen = freeze_artifact(artifact, checker_uid=65534)

    assert link.is_symlink()
    assert link.readlink() == Path("python3")
    bin_dir.chmod(0o755)
    link.unlink()
    link.symlink_to("python3.12")
    assert not verify_artifact_unchanged(frozen)
