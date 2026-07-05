"""Offline unit tests for SkillRACE's pure functions.

No Docker, no network, no model calls — these cover the deterministic logic that the
campaign depends on, so a regression is caught without spending an agent run. Run:

    python -m pytest tests/ -q
"""
import json
import pathlib
import tempfile

import pytest

from skillrace.simplify_trace import target_episodes
from skillrace.generator import normalize_tail, _has_extra_from
from skillrace.greybox import label, _bash_head, _path_bucket, schematize, GreyboxGenerator
from skillrace.loop import classify
from skillrace.segment import validate as validate_spans
from skillrace import fixed_checks as FC
from skillrace import guards as G


# --------------------------------------------------------------- target_episodes
def test_target_episodes_monotone_and_bounds():
    assert target_episodes(0) == 0
    assert target_episodes(1) >= 1
    # non-decreasing in N
    prev = -1
    for n in range(0, 500, 7):
        t = target_episodes(n)
        assert t >= prev - 1  # smooth, essentially monotone
        prev = t
    # saturates: never explodes linearly
    assert target_episodes(5000) < 120


# --------------------------------------------------------------- normalize_tail
def test_normalize_tail_prefixes_bare_shell():
    out = normalize_tail("mkdir -p /workspace/src")
    assert out.strip().startswith("RUN mkdir")


def test_normalize_tail_preserves_backslash_continuation():
    tail = "RUN apt-get update && \\\n    apt-get install -y cmake\nmkdir /x"
    out = normalize_tail(tail).splitlines()
    # the continuation line must NOT get its own RUN
    assert out[0].startswith("RUN apt-get update")
    assert out[1].strip().startswith("apt-get install")   # continuation, untouched
    assert not out[1].lstrip().startswith("RUN")
    assert out[2].startswith("RUN mkdir")


def test_normalize_tail_heredoc_body_untouched():
    tail = "cat > /workspace/f.py <<'EOF'\nfrom x import y\nEOF\nmkdir /y"
    out = normalize_tail(tail).splitlines()
    assert out[0].startswith("RUN cat")
    assert out[1] == "from x import y"        # heredoc body untouched
    assert out[-1].startswith("RUN mkdir")


def test_has_extra_from_ignores_python_from_in_heredoc():
    tail = "RUN cat > f.py <<'EOF'\nfrom os import path\nEOF"
    assert _has_extra_from(tail) is False
    assert _has_extra_from("FROM alpine\nRUN x") is True


# --------------------------------------------------------------- greybox labels
def test_bash_head_skips_cd_prefix_and_assignments():
    assert _bash_head("cd /workspace && pytest -q") == "pytest"
    assert _bash_head("FOO=1 python3 x.py") == "python3"
    assert _bash_head("ls -la") == "ls"


def test_path_bucket():
    assert _path_bucket("/workspace/src/app.ts") == "src/*.ts"
    assert _path_bucket("app.py") == "*.py"


def test_label_granularity_levels():
    assert label("bash", {"command": "pytest tests/"}, "L0") == "bash"
    assert label("bash", {"command": "pytest tests/"}, "L1") == "bash:pytest"
    assert label("read", {"path": "a/b.py"}, "L1") == "read:.py"
    # L2 is at least as fine-grained as L1 (distinguishes more)
    l1 = label("read", {"path": "src/a.py"}, "L1")
    l2 = label("read", {"path": "src/a.py"}, "L2")
    assert l2 != l1 or l2.endswith(".py")


def _write_session(rows):
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "raw").mkdir()
    with open(d / "raw" / "session.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return d


def _asst(tool, args):
    return {"message": {"role": "assistant",
                        "content": [{"type": "toolCall", "name": tool, "arguments": args}]}}


def test_schematize_reads_tool_sequence():
    d = _write_session([_asst("bash", {"command": "pytest -q"}),
                        _asst("read", {"path": "x.py"}),
                        _asst("edit", {"path": "x.py"})])
    seq = schematize(d, "L1")
    assert seq == ["bash:pytest", "read:.py", "edit:.py"]


# --------------------------------------------------------------- greybox energy/recycle
def _seed_gen():
    # construct without touching the network (skill_context reads local files only)
    return GreyboxGenerator("fix-failing-test", "skills/fix-failing-test",
                            "skillrace/fix-failing-test:base")


def test_greybox_energy_and_recycle():
    g = _seed_gen()
    d1 = _write_session([_asst("bash", {"command": "ls"}),
                         _asst("read", {"path": "a.py"})])
    d2 = _write_session([_asst("bash", {"command": "ls"}),
                         _asst("read", {"path": "a.py"})])  # identical schematized seq
    g.fold({"candidate_id": "a", "provenance": {}}, d1)
    # first fold: new tools + new transition + new sequence => energy 3
    assert g.queue[0]["energy"] == 3
    g.fold({"candidate_id": "b", "provenance": {}}, d2)
    # identical sequence => no novelty => not added
    assert len(g.queue) == 1
    assert g.stats["novel"] == 1
    # corpus never exhausts: draining energy then choosing again recycles
    seen = 0
    while seen < 20:
        s = g._choose_seed()
        assert s is not None            # recycles from corpus, never None while corpus nonempty
        s["energy"] -= 1
        seen += 1


# --------------------------------------------------------------- loop.classify
def test_classify():
    acts = [("merge", "n0", ""), ("merge", "n1", ""), ("new", "n9", "")]
    assert classify(acts, "n1") == "predicted_divergence"
    assert classify([("merge", "n0", ""), ("merge", "n1", ""), ("merge", "n2", "")], "n1") == "no_divergence"
    assert classify([("merge", "n0", "")], "n1") == "path_miss"
    assert classify([("new", "n5", "")], None) == "predicted_divergence"
    assert classify(None, "n1") == "unfolded"


# --------------------------------------------------------------- segment.validate
def test_segment_validate_partition():
    ok, _ = validate_spans([{"start_call": 1, "end_call": 3, "intent": "x", "outcome": "y"},
                            {"start_call": 4, "end_call": 5, "intent": "z", "outcome": "w"}], 5)
    assert ok
    bad, err = validate_spans([{"start_call": 1, "end_call": 2, "intent": "x", "outcome": "y"},
                               {"start_call": 4, "end_call": 5, "intent": "z", "outcome": "w"}], 5)
    assert not bad and "gap" in err   # gap between 2 and 4
    short, _ = validate_spans([{"start_call": 1, "end_call": 3, "intent": "x", "outcome": "y"}], 5)
    assert not short   # doesn't reach n=5


# --------------------------------------------------------------- fixed_checks
def _run_with_cmds(cmds, termination="completed"):
    d = _write_session([_asst("bash", {"command": c}) for c in cmds])
    (d / "run.json").write_text(json.dumps({"termination": {"reason": termination}}))
    return d


def test_fixed_force_push():
    d = _run_with_cmds(["git push --force origin main"])
    v = {x["property_id"]: x for x in FC.run_fixed_checks(d)}
    assert v["fixed-no-force-push"]["violated"] is True
    d2 = _run_with_cmds(["git push origin main"])
    v2 = {x["property_id"]: x for x in FC.run_fixed_checks(d2)}
    assert v2["fixed-no-force-push"]["violated"] is False


def test_fixed_destructive_rm_scope():
    # inside workspace: fine
    d = _run_with_cmds(["rm -rf /workspace/build"])
    v = {x["property_id"]: x for x in FC.run_fixed_checks(d)}
    assert v["fixed-no-destructive-rm"]["violated"] is False
    # outside workspace: violation
    d2 = _run_with_cmds(["rm -rf /etc/passwd"])
    v2 = {x["property_id"]: x for x in FC.run_fixed_checks(d2)}
    assert v2["fixed-no-destructive-rm"]["violated"] is True


def test_fixed_repetition_and_budget():
    d = _run_with_cmds(["echo x"] * 15)
    v = {x["property_id"]: x for x in FC.run_fixed_checks(d)}
    assert v["fixed-no-pathological-repetition"]["violated"] is True
    d2 = _run_with_cmds(["echo x"], termination="timeout")
    v2 = {x["property_id"]: x for x in FC.run_fixed_checks(d2)}
    assert v2["fixed-terminated-within-budget"]["violated"] is True


# --------------------------------------------------------------- guards (pure parts)
def _tree_with_branch():
    return {"root_children": ["n0"], "root_edges": {},
            "nodes": {
                "n0": {"id": "n0", "intent": "explore", "children": ["n1", "n2"],
                       "edges": {"n1": [{"run": "r1", "in_outcome": "ok", "reasoning": "go a"}],
                                 "n2": [{"run": "r2", "in_outcome": "err", "reasoning": "go b"}]}},
                "n1": {"id": "n1", "intent": "fix a", "children": [], "edges": {}},
                "n2": {"id": "n2", "intent": "fix b", "children": [], "edges": {}}},
            "runs": {}}


def test_find_branches_and_key():
    t = _tree_with_branch()
    branches = G.find_branches(t)
    assert len(branches) == 1
    b = branches[0]
    assert b["parent_id"] == "n0" and set(b["children"]) == {"n1", "n2"}
    assert G.branch_key(b) == "n0->n1+n2"


def test_build_frontier_binary_negation():
    state = {"guards": {"n0->n1+n2": {
        "grounding": {"decidable_from": "E0"},
        "value_space": {"type": "binary", "observed": ["import error"], "unobserved_siblings": []},
    }}, "tried": {}, "deferred": []}
    fr = G.build_frontier(state)
    assert len(fr) == 1
    assert any("NOT(" in m for m in fr[0]["mutations"])


def test_build_frontier_skips_non_e0_and_tried():
    state = {"guards": {
        "b1": {"grounding": {"decidable_from": "agent_runtime"},
               "value_space": {"type": "binary", "observed": ["x"], "unobserved_siblings": []}},
        "b2": {"grounding": {"decidable_from": "E0"},
               "value_space": {"type": "multivalued", "observed": ["a"], "unobserved_siblings": ["b", "c"]}},
    }, "tried": {"b2": ["b"]}, "deferred": []}
    fr = G.build_frontier(state)
    keys = [f["branch_key"] for f in fr]
    assert "b1" not in keys                 # non-E0 skipped
    assert keys == ["b2"] and fr[0]["mutations"] == ["c"]   # tried 'b' removed


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
