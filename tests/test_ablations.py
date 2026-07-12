from __future__ import annotations

import json

import pytest

import skillrace.guards as guards_module
from skillrace.ablations import ABLATIONS, get_strategy, guard_view
from skillrace.loop import SkillRACEGenerator


def test_only_full_and_outcomes_only_strategies_exist():
    assert set(ABLATIONS) == {"full", "outcomes-only"}
    assert get_strategy("full").signal_mode == "reasoning-and-outcomes"
    assert get_strategy("outcomes-only").signal_mode == "outcomes-only"

    for unavailable in ("uniform-frontier", "direct-property", "seeded-black-box"):
        with pytest.raises(ValueError, match="unsupported"):
            get_strategy(unavailable)


def test_headline_accepts_only_full_skillrace():
    assert get_strategy("full").validate(headline=True).name == "full"
    with pytest.raises(ValueError, match="headline"):
        get_strategy("outcomes-only").validate(headline=True)


def test_outcomes_only_view_recursively_removes_reasoning_without_mutating_input():
    branch = {
        "condition": "tests failed",
        "sides": [
            {
                "outcome": "pytest exit 1",
                "opening_reasoning": "SECRET_OPENING",
                "nested": {"reasoning": "SECRET_NESTED", "value": 1},
            }
        ],
    }
    view = guard_view(branch, signal_mode="outcomes-only")

    assert view == {
        "condition": "tests failed",
        "sides": [{"outcome": "pytest exit 1", "nested": {"value": 1}}],
    }
    assert "SECRET" not in str(view)
    assert branch["sides"][0]["opening_reasoning"] == "SECRET_OPENING"


def test_outcomes_only_guard_prompt_never_receives_opening_reasoning(monkeypatch):
    captured = []
    tree = {
        "root_children": ["n1", "n2"],
        "root_edges": {
            "n1": [{"run": "r1", "in_outcome": "failed", "reasoning": "SECRET_ONE"}],
            "n2": [{"run": "r2", "in_outcome": "passed", "reasoning": "SECRET_TWO"}],
        },
        "nodes": {
            "n1": {"intent": "one", "children": [], "edges": {}},
            "n2": {"intent": "two", "children": [], "edges": {}},
        },
        "runs": {},
    }

    def fake_chat(messages, **kwargs):
        captured.extend(messages)
        return {
            "content": json.dumps(
                {
                    "condition": "x",
                    "grounding": {
                        "kind": "executable",
                        "check": "true",
                        "decidable_from": "E0",
                    },
                    "value_space": {
                        "type": "binary",
                        "observed": ["a", "b"],
                        "unobserved_siblings": [],
                    },
                    "disagreements": [],
                }
            ),
            "cost_usd": 0.0,
        }

    monkeypatch.setattr(guards_module, "chat", fake_chat)
    guards_module.extract_guard(
        tree,
        {"parent_id": None, "children": ["n1", "n2"]},
        "model",
        signal_mode="outcomes-only",
    )

    prompt = json.dumps(captured)
    assert "SECRET_ONE" not in prompt
    assert "SECRET_TWO" not in prompt
    assert "opening reasoning" not in prompt.lower()
    assert "failed" in prompt and "passed" in prompt


def test_skillrace_snapshot_records_strategy_and_passes_signal_mode(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("demo")
    out = tmp_path / "out"
    out.mkdir()
    (out / "tree.json").write_text(json.dumps({"nodes": {}}))
    observed = []

    class Seed:
        cost_usd = 0.0

        def propose(self):
            return {
                "candidate_id": "fallback",
                "containerfile": "FROM demo:base\n",
                "base_image": "demo:base",
                "prompt": "do it",
                "built_image": "skillrace/fallback:built",
                "sanity": {},
                "provenance": {"source": "bootstrap"},
            }

        def snapshot(self):
            return {"schema": "seed/1"}

        def restore(self, snapshot):
            pass

    monkeypatch.setattr(
        guards_module,
        "extract_all_guards",
        lambda *args, **kwargs: observed.append(kwargs["signal_mode"]) or ({}, 0.0),
    )
    monkeypatch.setattr(guards_module, "build_frontier", lambda state: [])

    generator = SkillRACEGenerator(
        "demo",
        skill_dir,
        "demo:base",
        [{"id": "p1"}],
        "model",
        out,
        Seed(),
        strategy="outcomes-only",
        headline=False,
    )
    generator.propose(tmp_path / "cases")
    snapshot = generator.snapshot()

    assert observed == ["outcomes-only"]
    assert snapshot["strategy"]["name"] == "outcomes-only"
    assert len(snapshot["strategy_hash"]) == 64
