import pathlib

import skillrace.guards as guards
import skillrace.loop as campaign_loop


def test_mutable_campaign_state_uses_atomic_writer():
    forbidden = {
        "skillrace/loop.py": ["camp_path.write_text"],
        "skillrace/check_properties.py": ["verdicts.json\").write_text"],
        "skillrace/compile_checks.py": ["sp.write_text"],
        "skillrace/tree.py": ["tree_path.write_text", "cache_path.write_text"],
        "skillrace/guards.py": ["state_path.write_text", "p.write_text(json.dumps(state"],
    }
    for filename, patterns in forbidden.items():
        source = pathlib.Path(filename).read_text()
        for pattern in patterns:
            assert pattern not in source, f"{filename} still contains {pattern}"
        assert "atomic_write_" in source


def test_unsegmentable_episode_record_is_published_atomically(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    (run_dir / "raw").mkdir(parents=True)
    (run_dir / "raw" / "session.jsonl").write_text("{}\n")
    writes = []
    monkeypatch.setattr(campaign_loop, "render", lambda path: ("trace", 1))
    monkeypatch.setattr(campaign_loop, "segment_text", lambda *args: ([], 0.0))
    monkeypatch.setattr(
        campaign_loop, "validate_spans", lambda episodes, count: (False, "gap")
    )
    monkeypatch.setattr(
        campaign_loop,
        "atomic_write_json",
        lambda path, value: writes.append((path, value)),
    )

    actions, error, cost = campaign_loop.segment_and_fold(
        run_dir, tmp_path / "tree.json", "model", "skill"
    )

    assert actions is None
    assert error == "unsegmentable: gap"
    assert cost == 0.0
    assert writes == [
        (run_dir / "episodes.json", {"unsegmentable": True, "error": "gap"})
    ]


def test_mark_tried_publishes_complete_guard_state_atomically(tmp_path, monkeypatch):
    state = {"guards": {}, "tried": {}, "deferred": []}
    state_path = tmp_path / "tree.guards.json"
    writes = []

    monkeypatch.setattr(
        guards,
        "atomic_write_json",
        lambda path, value: writes.append((path, value.copy())),
    )

    guards.mark_tried(state, state_path, "branch", "mutation")

    assert writes == [
        (
            state_path,
            {
                "guards": {},
                "tried": {"branch": ["mutation"]},
                "deferred": [],
            },
        )
    ]
