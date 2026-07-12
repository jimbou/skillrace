from __future__ import annotations

import json
import pathlib

import pytest

from skillrace.rq3 import (
    LeakageError,
    assert_no_hidden_material,
    stage_public_scenario,
)


def _scenario(root: pathlib.Path) -> pathlib.Path:
    source = root / "scenario-one"
    (source / "base_skill").mkdir(parents=True)
    (source / "campaign").mkdir()
    (source / "tests" / "t1" / "checks").mkdir(parents=True)
    (source / "scenario.md").write_text("public purpose\n", encoding="utf-8")
    (source / "base_skill" / "SKILL.md").write_text("# Public skill\n", encoding="utf-8")
    (source / "campaign" / "properties.json").write_text(
        json.dumps({"properties": ["public behavior"]}), encoding="utf-8"
    )
    (source / "campaign" / "generation.json").write_text(
        json.dumps({"budget": 30}), encoding="utf-8"
    )
    (source / "tests" / "t1" / "candidate.json").write_text(
        json.dumps({"prompt": "HIDDEN_SENTINEL_PROMPT_91f72a"}), encoding="utf-8"
    )
    (source / "tests" / "t1" / "checks" / "pass.sh").write_text(
        "#!/bin/sh\n# HIDDEN_SENTINEL_CHECK_f5c190\nexit 0\n", encoding="utf-8"
    )
    return source


def test_public_stage_physically_contains_only_allowlisted_entries(tmp_path):
    source = _scenario(tmp_path)
    staged = stage_public_scenario(source, tmp_path / "staged")

    assert {path.name for path in staged.iterdir()} == {
        "scenario.md",
        "base_skill",
        "campaign",
        "public-stage.json",
    }
    assert not (staged / "tests").exists()
    combined = b"\n".join(path.read_bytes() for path in staged.rglob("*") if path.is_file())
    assert b"HIDDEN_SENTINEL" not in combined
    assert str(source.resolve()).encode() not in combined


def test_public_stage_rejects_symlinked_allowlisted_content(tmp_path):
    source = _scenario(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("not public", encoding="utf-8")
    (source / "campaign" / "escape.json").symlink_to(outside)

    with pytest.raises(LeakageError, match="symlink"):
        stage_public_scenario(source, tmp_path / "staged")


def test_sentinel_audit_detects_prompt_check_path_and_hash_leakage(tmp_path):
    source = _scenario(tmp_path)
    hidden = source / "tests"
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()

    for name, leaked in (
        ("prompt.log", b"HIDDEN_SENTINEL_PROMPT_91f72a"),
        ("check.log", b"HIDDEN_SENTINEL_CHECK_f5c190"),
        ("path.log", str(hidden.resolve()).encode()),
    ):
        path = artifacts / name
        path.write_bytes(leaked)
        with pytest.raises(LeakageError, match="hidden"):
            assert_no_hidden_material(hidden, [path])
        path.unlink()

    import hashlib

    hidden_hash = hashlib.sha256(
        (hidden / "t1" / "candidate.json").read_bytes()
    ).hexdigest()
    (artifacts / "hash.log").write_text(hidden_hash, encoding="utf-8")
    with pytest.raises(LeakageError, match="hidden"):
        assert_no_hidden_material(hidden, [artifacts])


def test_sentinel_audit_rejects_artifact_symlinks(tmp_path):
    source = _scenario(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "linked.log").symlink_to(source / "tests" / "t1" / "candidate.json")

    with pytest.raises(LeakageError, match="symlink"):
        assert_no_hidden_material(source / "tests", [artifacts])
