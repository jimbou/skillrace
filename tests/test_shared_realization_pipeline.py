from __future__ import annotations

import json

import pytest

import skillrace.generator as generator_module
import skillrace.greybox as greybox_module
import skillrace.guards as guards_module
import skillrace.loop as loop_module


SANITY = {
    "required_paths": ["/workspace"],
    "required_tools": ["bash"],
    "task_probe": {"command": "true", "allowed_exit_codes": [0]},
    "unsolved_check": None,
}


def test_shared_proposer_uses_one_bounded_provider_attempt(monkeypatch):
    calls = []

    def fake_chat(*args, **kwargs):
        calls.append(kwargs)
        return {"content": "[]", "cost_provider_credits": 0.0}

    monkeypatch.setattr(generator_module, "chat", fake_chat)

    generator_module.propose_batch(
        "ctx", [], 1, "deepseek-v3.2", 0.9, skill="demo"
    )

    assert calls[0]["retries"] == 1
    assert calls[0]["timeout_seconds"] == 180


def test_shared_realization_disables_provider_thinking_by_default(monkeypatch):
    calls = []

    def fake_chat(*args, **kwargs):
        calls.append(kwargs)
        return {
            "content": json.dumps(
                {
                    "prompt": "fix it",
                    "tail": "RUN true",
                    "sanity": SANITY,
                }
            ),
            "cost_provider_credits": 0.0,
        }

    monkeypatch.setattr(generator_module, "chat", fake_chat)

    generator_module.realize("ctx", "task", "env", "deepseek-v4-flash")

    assert calls[0]["reasoning"] is False
    assert calls[0]["max_tokens"] == 4000
    assert calls[0]["retries"] == 1
    assert calls[0]["timeout_seconds"] == 180
    assert generator_module.RandomGenerator.for_test().reasoning is False


def test_realize_and_build_stops_when_shared_transaction_deadline_expires(
    monkeypatch,
):
    now = [100.0]
    build_timeouts = []

    monkeypatch.setattr(generator_module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        generator_module,
        "realize",
        lambda *args, **kwargs: ("do it", "RUN false", SANITY, 0.25),
    )

    def slow_failed_build(containerfile, tag, timeout):
        build_timeouts.append(timeout)
        now[0] += 301.0
        return False, "synthetic build failure"

    monkeypatch.setattr(generator_module, "build_image", slow_failed_build)
    monkeypatch.setattr(
        generator_module,
        "repair_tail",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("repair must not start after the transaction deadline")
        ),
    )

    artifact, cost, error = generator_module.realize_and_build(
        "ctx",
        "task",
        "env",
        "glm-4.5-flash",
        "demo:base",
        "candidate-deadline",
        build_retries=4,
        build_timeout=600,
        realization_timeout=300,
    )

    assert artifact is None
    assert cost == 0.25
    assert build_timeouts == [300]
    assert error == "candidate realization timed out after 300 seconds"


def test_invalid_realizer_response_preserves_paid_call_cost(monkeypatch):
    monkeypatch.setattr(
        generator_module,
        "chat",
        lambda *args, **kwargs: {
            "content": '{"prompt":"missing tail"}',
            "cost_provider_credits": 0.375,
        },
    )

    with pytest.raises(generator_module.GenerationFailure) as caught:
        generator_module.realize("ctx", "task", "env", "glm-4.5-flash")

    assert caught.value.reason == "invalid-realization-response"
    assert caught.value.cost_provider_credits == 0.375

    generator = generator_module.RandomGenerator(
        "demo", "/definitely/missing", "demo:base"
    )
    outcome = generator._make_one_detailed(
        {"summary": "case", "task": "task", "env": "env"}
    )
    assert outcome.candidate is None
    assert outcome.cost_provider_credits == 0.375
    assert outcome.error["reason"] == "invalid-realization-response"


def test_shared_pipeline_repairs_build_failure_and_preserves_sanity(monkeypatch):
    monkeypatch.setattr(
        generator_module,
        "realize",
        lambda *a, **k: ("do it", "RUN false", SANITY, 0.1),
    )
    builds = []

    def fake_build(containerfile, tag, timeout):
        builds.append((containerfile, tag, timeout))
        return (len(builds) == 2, "first build failed")

    repairs = []

    def fake_repair(
        ctx, tail, error, model, reasoning=True, *, timeout_seconds=None
    ):
        repairs.append((tail, error, model, reasoning))
        return "RUN true", 0.2

    monkeypatch.setattr(generator_module, "build_image", fake_build)
    monkeypatch.setattr(generator_module, "repair_tail", fake_repair)

    artifact, cost, error = generator_module.realize_and_build(
        "ctx", "task", "env", "glm-4.5-flash", "demo:base", "candidate-1",
        build_retries=1, build_timeout=17,
    )

    assert error is None
    assert cost == 0.3
    assert artifact["sanity"] == SANITY
    assert artifact["containerfile"].startswith("FROM demo:base")
    assert artifact["built_image"] == "skillrace/candidate-1:built"
    assert artifact["build_attempts"] == 2
    assert len(repairs) == 1 and len(builds) == 2


def test_random_greybox_and_skillrace_import_the_same_pipeline_object():
    assert greybox_module.realize_and_build is generator_module.realize_and_build
    assert guards_module.realize_and_build is generator_module.realize_and_build


def test_random_and_greybox_build_from_runnable_tag_bound_to_immutable_base(monkeypatch):
    immutable = "sha256:" + "a" * 64
    seen = []

    def fake_pipeline(*args, **kwargs):
        seen.append(args[4])
        return (
            {
                "prompt": "do it",
                "containerfile": f"FROM {args[4]}\n",
                "built_image": f"skillrace/{args[5]}:built",
                "sanity": SANITY,
                "build_attempts": 1,
            },
            0.0,
            None,
        )

    random = generator_module.RandomGenerator(
        "demo",
        "/definitely/missing",
        "demo:base",
        base_image_identity=immutable,
    )
    monkeypatch.setattr(generator_module, "realize_and_build", fake_pipeline)
    random_candidate, _ = random._make_one(
        {"summary": "case", "task": "task", "env": "env"}
    )

    greybox = greybox_module.GreyboxGenerator(
        "demo",
        "/definitely/missing",
        "demo:base",
        base_image_identity=immutable,
    )
    seed = {
        "cand": {
            "candidate_id": "seed",
            "provenance": {"task_nl": "task", "env_nl": "env"},
        },
        "seq": ["bash:pytest"],
        "energy": 1,
    }
    greybox.corpus.append(seed)
    greybox.queue.append(seed)
    monkeypatch.setattr(greybox, "_mutate", lambda selected: ("task", "env"))
    monkeypatch.setattr(greybox_module, "realize_and_build", fake_pipeline)
    greybox_candidate = greybox.propose()

    assert seen == ["demo:base", "demo:base"]
    assert random_candidate["base_image"] == "demo:base"
    assert greybox_candidate["base_image"] == "demo:base"
    assert random_candidate["base_image_identity"] == immutable
    assert greybox_candidate["base_image_identity"] == immutable
    assert random_candidate["provenance"]["requested_base_image"] == "demo:base"
    assert greybox_candidate["provenance"]["requested_base_image"] == "demo:base"


def test_skillrace_synthesis_receives_runnable_tag_and_immutable_binding(
    tmp_path, monkeypatch
):
    immutable = "sha256:" + "b" * 64
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("demo")
    out = tmp_path / "out"
    out.mkdir()
    (out / "tree.json").write_text(json.dumps({"nodes": {}}))

    class Seed:
        cost_provider_credits = 0.0

        def propose(self):
            raise AssertionError("unexpected fallback")

        def snapshot(self):
            return {"schema": "seed/1"}

        def restore(self, snapshot):
            pass

    target = {
        "item": {
            "guard": {
                "parent_id": None,
                "branch_key": "ROOT->n1+n2",
                "condition": "x",
            }
        },
        "mutation": "x exists",
    }
    seen = []
    monkeypatch.setattr(loop_module.G, "extract_all_guards", lambda *a, **k: ({}, 0.0))
    monkeypatch.setattr(loop_module.G, "build_frontier", lambda state: [target["item"]])
    monkeypatch.setattr(loop_module.G, "select_target", lambda *a, **k: (target, 0.0))
    monkeypatch.setattr(
        loop_module.G,
        "synthesize",
        lambda tree, selected, skill, directory, base, model, cases, **kwargs: (
            seen.append(
                (
                    base,
                    kwargs.get("requested_base_image"),
                    kwargs.get("base_image_identity"),
                )
            )
            or (str(tmp_path / "case"), {"validated": True}, 0.0)
        ),
    )
    monkeypatch.setattr(
        loop_module.G,
        "load_guard_state",
        lambda path, **kwargs: ({}, tmp_path / "g"),
    )
    monkeypatch.setattr(loop_module.G, "mark_tried", lambda *a, **k: None)

    generator = loop_module.SkillRACEGenerator(
        "demo",
        skill_dir,
        "demo:base",
        [{"id": "p1"}],
        "model",
        out,
        Seed(),
        base_image_identity=immutable,
    )
    case, source = generator.propose(tmp_path / "cases")

    assert source == "skillrace"
    assert case == str(tmp_path / "case")
    assert seen == [("demo:base", "demo:base", immutable)]


def test_greybox_propose_delegates_realization_build_and_repair_to_shared_pipeline(
    monkeypatch,
):
    generator = greybox_module.GreyboxGenerator(
        "demo", "/definitely/missing", "demo:base", build_retries=4
    )
    seed = {
        "cand": {
            "candidate_id": "seed",
            "provenance": {"task_nl": "task", "env_nl": "env"},
        },
        "seq": ["bash:pytest"],
        "energy": 1,
    }
    generator.corpus.append(seed)
    generator.queue.append(seed)
    monkeypatch.setattr(generator, "_mutate", lambda selected: ("new task", "new env"))
    seen = []

    def fake_pipeline(*args, **kwargs):
        seen.append((args, kwargs))
        return (
            {
                "prompt": "do it",
                "containerfile": "FROM demo:base\n",
                "built_image": "skillrace/candidate:built",
                "sanity": SANITY,
                "build_attempts": 1,
            },
            0.4,
            None,
        )

    monkeypatch.setattr(greybox_module, "realize_and_build", fake_pipeline)

    candidate = generator.propose()

    assert len(seen) == 1
    assert candidate["sanity"] == SANITY
    assert candidate["provenance"]["parent_candidate"] == "seed"


def test_skillrace_guard_synthesis_uses_shared_pipeline_plus_extra_validator(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        guards_module,
        "chat",
        lambda *a, **k: {
            "content": json.dumps(
                {"task": "new task", "env": "new env", "validate_sh": "test -f x"}
            ),
            "cost_provider_credits": 0.1,
        },
    )
    seen = []

    def fake_pipeline(*args, **kwargs):
        seen.append((args, kwargs))
        assert kwargs["validator"]("candidate:built") == (True, "target present")
        return (
            {
                "prompt": "do it",
                "containerfile": "FROM demo:base\n",
                "built_image": "candidate:built",
                "sanity": SANITY,
                "build_attempts": 1,
            },
            0.2,
            None,
        )

    monkeypatch.setattr(guards_module, "_validate_in_image", lambda *a: (True, "target present"))
    monkeypatch.setattr(guards_module, "realize_and_build", fake_pipeline)
    target = {
        "item": {
            "guard": {
                "parent_id": None,
                "condition": "x exists",
                "value_space": {"observed": ["missing"]},
                "branch_key": "ROOT->n1+n2",
            }
        },
        "mutation": "x exists",
        "targeted_property": "p1",
        "rationale": "edge case",
    }
    tree = {"nodes": {}, "root_children": [], "root_edges": {}, "runs": {}}

    case, info, cost = guards_module.synthesize(
        tree, target, "demo", "/definitely/missing", "demo:base",
        "glm-4.5-flash", tmp_path,
    )

    assert len(seen) == 1
    candidate = json.loads((tmp_path / case.split("/")[-1] / "candidate.json").read_text())
    assert candidate["sanity"] == SANITY
    assert info["validated"] is True and cost == 0.3


def test_all_three_methods_pass_the_exact_same_build_retry_and_timeout_policy(
    tmp_path, monkeypatch
):
    calls = {}

    def pipeline_for(method):
        def fake_pipeline(*args, **kwargs):
            calls[method] = (
                kwargs["build_retries"],
                kwargs["build_timeout"],
            )
            return (
                {
                    "prompt": "do it",
                    "containerfile": "FROM demo:base\n",
                    "built_image": f"candidate:{method}",
                    "sanity": SANITY,
                    "build_attempts": 1,
                },
                0.0,
                None,
            )

        return fake_pipeline

    random = generator_module.RandomGenerator(
        "demo", "/definitely/missing", "demo:base"
    )
    monkeypatch.setattr(
        generator_module, "realize_and_build", pipeline_for("random")
    )
    random._make_one({"summary": "case", "task": "task", "env": "env"})

    greybox = greybox_module.GreyboxGenerator(
        "demo", "/definitely/missing", "demo:base"
    )
    seed = {
        "cand": {
            "candidate_id": "seed",
            "provenance": {"task_nl": "task", "env_nl": "env"},
        },
        "seq": ["bash:pytest"],
        "energy": 1,
    }
    greybox.corpus.append(seed)
    greybox.queue.append(seed)
    monkeypatch.setattr(greybox, "_mutate", lambda selected: ("task", "env"))
    monkeypatch.setattr(
        greybox_module, "realize_and_build", pipeline_for("greybox")
    )
    greybox.propose()

    monkeypatch.setattr(
        guards_module,
        "chat",
        lambda *a, **k: {
            "content": json.dumps(
                {"task": "task", "env": "env", "validate_sh": "true"}
            ),
            "cost_provider_credits": 0.0,
        },
    )
    monkeypatch.setattr(
        guards_module, "realize_and_build", pipeline_for("skillrace")
    )
    target = {
        "item": {
            "guard": {
                "parent_id": None,
                "condition": "condition",
                "value_space": {"observed": ["old"]},
                "branch_key": "ROOT->a+b",
            }
        },
        "mutation": "new",
    }
    guards_module.synthesize(
        {"nodes": {}, "root_children": [], "root_edges": {}, "runs": {}},
        target,
        "demo",
        "/definitely/missing",
        "demo:base",
        "glm-4.5-flash",
        tmp_path,
    )

    assert calls == {
        "random": (4, 600),
        "greybox": (4, 600),
        "skillrace": (4, 600),
    }
