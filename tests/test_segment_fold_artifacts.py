from __future__ import annotations

import json

from skillrace.loop import segment_and_fold


def test_segment_and_fold_completes_the_staged_artifact_version(tmp_path, monkeypatch):
    import skillrace.loop as loop_module

    run_dir = tmp_path / "runs" / "run-1"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "session.jsonl").write_text("{}\n")
    tree_path = tmp_path / "tree.json"

    monkeypatch.setattr(loop_module, "render", lambda path: ("trace", 1))
    monkeypatch.setattr(loop_module, "target_episodes", lambda count: 1)
    monkeypatch.setattr(
        loop_module,
        "segment_text",
        lambda text, target, model: ([{"start": 0, "end": 1}], 0.25),
    )
    monkeypatch.setattr(loop_module, "validate_spans", lambda spans, count: (True, None))
    monkeypatch.setattr(loop_module, "call_reasonings", lambda path: [])
    monkeypatch.setattr(loop_module, "assemble", lambda spans, reasonings: [{"intent": "x"}])
    monkeypatch.setattr(
        loop_module,
        "empty_tree",
        lambda skill: {"schema": "behavior-tree/2", "skill": skill, "folded_attempts": {}},
    )

    def fold(tree, episodes, run_id, model, cache, run_meta):
        tree["nodes"] = [{"id": "n0"}]
        cache["decision"] = "new"
        return [("new", "n0", "intent")]

    monkeypatch.setattr(loop_module, "tree_fold", fold)

    actions, error, cost = segment_and_fold(
        run_dir,
        tree_path,
        "model",
        "demo",
        attempt_id="e0000-a00",
    )

    assert actions == [("new", "n0", "intent")]
    assert error is None
    assert cost == 0.25
    complete = tmp_path / "fold-artifacts" / "e0000-a00.complete.json"
    assert complete.is_file()
    assert json.loads(tree_path.read_text())["folded_attempts"]["e0000-a00"]

