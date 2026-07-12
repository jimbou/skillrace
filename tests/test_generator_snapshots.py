from __future__ import annotations

import json
from collections import deque

import pytest

import skillrace.loop as loop_module
from skillrace.adaptive_artifacts import (
    capture_adaptive_artifacts,
    stage_fold_artifact_version,
)
from skillrace.generator import GenerationFailure, RandomGenerator
from skillrace.greybox import GreyboxGenerator
from skillrace.input_identity import skill_input_tree_hash
from skillrace.loop import SkillRACEGenerator


def test_random_snapshot_round_trip_includes_search_state_but_never_secrets():
    generator = RandomGenerator.for_test(source="random")
    generator.digest = ["case one"]
    generator.proposed = [{"summary": "case one", "task": "t", "env": "e"}]
    generator._buf = [
        {"candidate_id": "c1", "built_image": "skillrace/c1:built"}
    ]
    generator.n_batches = 2
    generator.n_skipped = 3
    generator.cost_usd = 0.75
    generator.failure_state = {
        "type": "GenerationFailure",
        "reason": "no-buildable-candidate",
        "message": "none built",
    }
    generator.api_key = "TOP-SECRET"

    snapshot = generator.snapshot()
    json.dumps(snapshot)
    restored = RandomGenerator.for_test(source="random")
    restored.restore(snapshot)

    assert restored.snapshot() == snapshot
    assert "TOP-SECRET" not in json.dumps(snapshot)
    assert snapshot["buffered_candidates"][0]["candidate_id"] == "c1"
    assert len(snapshot["skill_input_hash"]) == 64
    assert snapshot["base_image_identity"] == "skillrace/test-skill:base"


def test_random_typed_failure_state_survives_a_failed_refill(monkeypatch):
    generator = RandomGenerator.for_test(source="random")
    monkeypatch.setattr(
        generator,
        "_refill",
        lambda: (_ for _ in ()).throw(
            GenerationFailure("nothing", reason="no-buildable-candidate")
        ),
    )
    with pytest.raises(GenerationFailure):
        generator.propose()
    assert generator.snapshot()["failure_state"] == {
        "type": "GenerationFailure",
        "reason": "no-buildable-candidate",
        "message": "nothing",
    }


def test_random_records_non_generation_exception_type_without_serializing_context(
    monkeypatch,
):
    generator = RandomGenerator.for_test(source="random")
    monkeypatch.setattr(
        generator, "_refill", lambda: (_ for _ in ()).throw(ValueError("bad JSON"))
    )
    with pytest.raises(ValueError, match="bad JSON"):
        generator.propose()
    assert generator.snapshot()["failure_state"] == {
        "type": "ValueError",
        "reason": "generation-error",
        "message": "bad JSON",
    }


def test_greybox_snapshot_restores_exact_novelty_queue_energy_and_object_identity():
    generator = GreyboxGenerator.for_test()
    zero = {
        "cand": {"candidate_id": "zero", "provenance": {}},
        "seq": [], "energy": 0, "base_energy": 0,
    }
    live = {
        "cand": {"candidate_id": "live", "provenance": {}},
        "seq": ["bash:pytest"], "energy": 1, "base_energy": 3,
    }
    generator.corpus = [zero, live]
    generator.queue = deque([live])
    generator._pending = live
    generator.d_tool = {"bash:pytest"}
    generator.d_trans = {("bash:pytest", "read:.py")}
    generator.d_seq = {("bash:pytest",), ("bash:pytest", "read:.py")}
    generator.folded_attempt_ids = ["e0000-a00"]
    generator.cost_usd = 1.25
    generator.api_key = "NEVER-SERIALIZE"

    snapshot = generator.snapshot()
    json.dumps(snapshot)
    restored = GreyboxGenerator.for_test()
    restored.restore(snapshot)

    assert restored.snapshot() == snapshot
    assert len(restored.corpus) == 2  # zero-energy seeds remain retained
    assert restored._pending is restored.corpus[1]
    assert restored.queue[0] is restored.corpus[1]
    restored._pending["energy"] = 0
    assert restored.corpus[1]["energy"] == 0
    assert "NEVER-SERIALIZE" not in json.dumps(snapshot)
    assert len(snapshot["skill_input_hash"]) == 64
    assert snapshot["base_image_identity"] == "skillrace/test-skill:base"


def test_greybox_fold_is_idempotent_by_attempt_id(monkeypatch, tmp_path):
    generator = GreyboxGenerator.for_test()
    monkeypatch.setattr(generator, "_observe", lambda run_dir: (["bash:pytest"], 2))
    candidate = {"candidate_id": "c1", "provenance": {}}

    first = generator.fold(
        candidate, tmp_path, phase="bootstrap", attempt_id="e0000-a00"
    )
    second = generator.fold(
        candidate, tmp_path, phase="bootstrap", attempt_id="e0000-a00"
    )

    assert first is second
    assert len(generator.corpus) == 1
    assert generator.stats["initial_retained"] == 1


class SeedStub:
    cost_usd = 0.0

    def snapshot(self):
        return {"schema": "seed-stub/1"}

    def restore(self, snapshot):
        assert snapshot == {"schema": "seed-stub/1"}


def test_skillrace_snapshot_tracks_artifact_identities_and_fold_is_idempotent(
    tmp_path, monkeypatch
):
    generator = SkillRACEGenerator(
        "demo", tmp_path, "demo:base", [{"id": "p1"}], "model", tmp_path,
        SeedStub(),
    )
    generator.tree_path.write_text(json.dumps({"schema": "behavior-tree/2"}))
    generator.tree_path.with_suffix(".guards.json").write_text(json.dumps({"guards": {}}))
    calls = []
    monkeypatch.setattr(
        loop_module,
        "segment_and_fold",
        lambda *args, **kwargs: calls.append((args, kwargs)) or (["new"], None, 0.2),
    )

    one = generator.fold(
        {"case_dir": str(tmp_path / "case")}, tmp_path / "run",
        attempt_id="e0000-a00",
    )
    two = generator.fold(
        {"case_dir": str(tmp_path / "case")}, tmp_path / "run",
        attempt_id="e0000-a00",
    )
    snapshot = generator.snapshot()

    assert one == two
    assert len(calls) == 1
    assert snapshot["tree_artifacts"]["tree.json"]["sha256"]
    assert json.loads(snapshot["tree_artifacts"]["tree.json"]["content"]) == {
        "schema": "behavior-tree/2"
    }
    assert snapshot["folded_attempt_ids"] == ["e0000-a00"]
    assert snapshot["target_metadata"]["last_target_parent"] is None


def test_skill_input_tree_hash_binds_relevant_bytes_paths_and_modes(tmp_path):
    skill = tmp_path / "skill"
    (skill / "repo").mkdir(parents=True)
    (skill / "scripts").mkdir()
    (skill / ".cache").mkdir()
    (skill / "out").mkdir()
    (skill / "SKILL.md").write_text("instructions\n")
    source = skill / "repo" / "app.py"
    source.write_text("print('one')\n")
    script = skill / "scripts" / "check.sh"
    script.write_text("#!/bin/sh\ntrue\n")
    script.chmod(0o755)
    (skill / ".cache" / "ignored").write_text("one")
    (skill / "out" / "ignored").write_text("one")

    original = skill_input_tree_hash(skill)
    (skill / ".cache" / "ignored").write_text("two")
    (skill / "out" / "ignored").write_text("two")
    assert skill_input_tree_hash(skill) != original

    (skill / ".cache" / "ignored").write_text("one")
    (skill / "out" / "ignored").write_text("one")
    assert skill_input_tree_hash(skill) == original

    source.write_text("print('two')\n")
    changed_bytes = skill_input_tree_hash(skill)
    assert changed_bytes != original
    source.write_text("print('one')\n")
    assert skill_input_tree_hash(skill) == original
    script.chmod(0o644)
    assert skill_input_tree_hash(skill) != original


def test_all_generator_consumers_reject_ambiguous_skill_symlinks(tmp_path):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("trusted")
    (skill / "alias").symlink_to("SKILL.md")

    with pytest.raises(ValueError, match="symlink"):
        RandomGenerator("demo", skill, "demo:base")
    with pytest.raises(ValueError, match="symlink"):
        GreyboxGenerator("demo", skill, "demo:base")

    class Seed:
        cost_usd = 0.0

        def snapshot(self):
            return {"schema": "seed/1"}

        def restore(self, snapshot):
            pass

    with pytest.raises(ValueError, match="symlink"):
        SkillRACEGenerator(
            "demo", skill, "demo:base", [], "model", tmp_path / "out", Seed()
        )


@pytest.mark.parametrize("generator_type", ["random", "greybox"])
def test_baseline_snapshot_refuses_skill_edit_or_base_identity_retarget(
    tmp_path, generator_type
):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("version one")
    cls = RandomGenerator if generator_type == "random" else GreyboxGenerator
    first = cls(
        "demo", skill, "demo:base", base_image_identity="sha256:" + "a" * 64
    )
    snapshot = first.snapshot()

    (skill / "SKILL.md").write_text("version two")
    edited = cls(
        "demo", skill, "demo:base", base_image_identity="sha256:" + "a" * 64
    )
    with pytest.raises(ValueError, match="skill input"):
        edited.restore(snapshot)

    (skill / "SKILL.md").write_text("version one")
    retargeted = cls(
        "demo", skill, "demo:base", base_image_identity="sha256:" + "b" * 64
    )
    with pytest.raises(ValueError, match="base-image identity"):
        retargeted.restore(snapshot)


def _skillrace_for_artifacts(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(exist_ok=True)
    (skill_dir / "SKILL.md").write_text("trusted")
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return SkillRACEGenerator(
        "demo", skill_dir, "demo:base", [{"id": "p1"}], "model", out,
        SeedStub(), base_image_identity="sha256:" + "a" * 64,
    )


@pytest.mark.parametrize("change", ["mutate", "delete"])
def test_skillrace_normal_restore_refuses_mutated_or_deleted_adaptive_artifact(
    tmp_path, change
):
    generator = _skillrace_for_artifacts(tmp_path)
    generator.tree_path.write_text(json.dumps({"schema": "behavior-tree/2", "v": 1}))
    generator.tree_path.with_suffix(".cache.json").write_text(json.dumps({"c": 1}))
    generator.tree_path.with_suffix(".guards.json").write_text(json.dumps({"g": 1}))
    snapshot = generator.snapshot()
    target = generator.tree_path.with_suffix(".cache.json")
    if change == "mutate":
        target.write_text(json.dumps({"c": 2}))
    else:
        target.unlink()

    with pytest.raises(ValueError, match="adaptive artifact"):
        generator.restore(snapshot)

    generator.restore_artifacts(snapshot)
    generator.restore(snapshot)
    assert generator.snapshot()["tree_artifacts"] == snapshot["tree_artifacts"]


def test_skillrace_normal_restore_refuses_unexpected_artifact_creation(tmp_path):
    generator = _skillrace_for_artifacts(tmp_path)
    snapshot = generator.snapshot()
    generator.tree_path.write_text(json.dumps({"unexpected": True}))
    with pytest.raises(ValueError, match="adaptive artifact"):
        generator.restore(snapshot)


def test_skillrace_pending_fold_accepts_only_matching_versioned_forward_state(tmp_path):
    generator = _skillrace_for_artifacts(tmp_path)
    generator.tree_path.write_text(
        json.dumps({"schema": "behavior-tree/2", "folded_attempts": {}})
    )
    generator.tree_path.with_suffix(".cache.json").write_text(json.dumps({"c": 1}))
    before = generator.snapshot()
    attempt_id = "e0000-a00"

    generator.tree_path.write_text(
        json.dumps(
            {
                "schema": "behavior-tree/2",
                "folded_attempts": {attempt_id: {"actions": [["new", "n0", "x"]]}},
            }
        )
    )
    generator.tree_path.with_suffix(".cache.json").write_text(json.dumps({"c": 2}))
    generator.publish_fold_artifact_version(attempt_id)

    restored = _skillrace_for_artifacts(tmp_path)
    assert restored.restore_for_pending_fold(before, attempt_id) == "forward"
    after = restored.snapshot()
    assert after["tree_artifacts"] != before["tree_artifacts"]

    restored.tree_path.with_suffix(".cache.json").write_text(json.dumps({"drift": True}))
    with pytest.raises(ValueError, match="forward-fold artifact"):
        restored.restore_for_pending_fold(before, attempt_id)


def test_skillrace_pending_fold_without_version_rolls_back_exact_snapshot(tmp_path):
    generator = _skillrace_for_artifacts(tmp_path)
    generator.tree_path.write_text(json.dumps({"old": True}))
    before = generator.snapshot()
    generator.tree_path.write_text(json.dumps({"arbitrary": "drift"}))
    generator.tree_path.with_suffix(".guards.json").write_text(json.dumps({"new": 1}))

    assert generator.restore_for_pending_fold(before, "e0000-a00") == "rollback"
    assert generator.snapshot()["tree_artifacts"] == before["tree_artifacts"]


def test_incomplete_forward_version_restores_exact_post_fold_content(tmp_path):
    generator = _skillrace_for_artifacts(tmp_path)
    attempt_id = "e0000-a00"
    pre_tree = {"schema": "behavior-tree/2", "folded_attempts": {}}
    generator.tree_path.write_text(json.dumps(pre_tree))
    before = generator.snapshot()

    post_tree = {
        "schema": "behavior-tree/2",
        "folded_attempts": {attempt_id: {"actions": [["new", "n0", "x"]]}},
    }
    post_content = json.dumps(post_tree, indent=2) + "\n"
    versioned = capture_adaptive_artifacts(
        generator.tree_path, overrides={"tree.json": post_content}
    )
    stage_fold_artifact_version(generator.tree_path, attempt_id, versioned)
    # Simulate interruption after the durable version but before global publication.
    generator.tree_path.write_text(json.dumps(pre_tree))

    assert generator.restore_for_pending_fold(before, attempt_id) == "forward"
    assert generator.tree_path.read_text() == post_content
    assert (
        generator.out / "fold-artifacts" / f"{attempt_id}.complete.json"
    ).is_file()
