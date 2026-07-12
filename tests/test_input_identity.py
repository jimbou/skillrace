from __future__ import annotations

import os

from skillrace.input_identity import skill_input_tree_hash


def test_skill_input_tree_hash_binds_empty_directory_entries(tmp_path):
    skill = tmp_path / "skill"
    skill.mkdir()
    without_directory = skill_input_tree_hash(skill)

    (skill / "empty").mkdir()

    assert skill_input_tree_hash(skill) != without_directory


def test_skill_input_tree_hash_binds_directory_modes(tmp_path):
    skill = tmp_path / "skill"
    directory = skill / "scripts"
    directory.mkdir(parents=True)
    directory.chmod(0o700)
    private = skill_input_tree_hash(skill)

    directory.chmod(0o755)

    assert skill_input_tree_hash(skill) != private


def test_skill_input_tree_hash_rejects_directory_symlink_entries(tmp_path):
    skill = tmp_path / "skill"
    skill.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    (skill / "linked").symlink_to(
        os.path.relpath(target, skill), target_is_directory=True
    )

    try:
        skill_input_tree_hash(skill)
    except ValueError as error:
        assert "symlink" in str(error)
    else:
        raise AssertionError("directory symlink was accepted as an effective skill input")


def test_skill_input_tree_hash_rejects_file_symlinks_even_when_target_is_in_root(tmp_path):
    skill = tmp_path / "skill"
    skill.mkdir()
    target = skill / "target.txt"
    target.write_text("trusted\n")
    (skill / "linked.txt").symlink_to("target.txt")

    try:
        skill_input_tree_hash(skill)
    except ValueError as error:
        assert "symlink" in str(error)
    else:
        raise AssertionError("file symlink was accepted as an effective skill input")


def test_cache_and_output_named_directories_are_effective_skill_inputs(tmp_path):
    skill = tmp_path / "skill"
    skill.mkdir()
    for name in (".cache", "cache", "out", "output"):
        directory = skill / name
        directory.mkdir()
        source = directory / "effective.txt"
        source.write_text("one\n")
        first = skill_input_tree_hash(skill)
        source.write_text("two\n")
        assert skill_input_tree_hash(skill) != first
