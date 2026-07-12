from __future__ import annotations

import pytest

import skillrace.generator as random_module
import skillrace.greybox as greybox_module
from skillrace.generator import GenerationFailure, RandomGenerator
from skillrace.greybox import GreyboxGenerator


def _greybox_with_seed():
    generator = GreyboxGenerator("demo", "/missing", "demo:base")
    seed = {
        "cand": {
            "candidate_id": "seed",
            "provenance": {"task_nl": "task", "env_nl": "env"},
        },
        "seq": ["bash:pytest"],
        "energy": 2,
        "base_energy": 2,
    }
    generator.corpus.append(seed)
    generator.queue.append(seed)
    return generator


def test_greybox_propose_owns_exactly_one_mutation_realize_build_attempt(monkeypatch):
    generator = _greybox_with_seed()
    mutation_calls = []
    build_calls = []
    monkeypatch.setattr(
        generator,
        "_mutate",
        lambda seed: mutation_calls.append(seed) or ("new task", "new env"),
    )

    def failed_build(*args, **kwargs):
        build_calls.append((args, kwargs))
        if len(build_calls) > 1:
            raise AssertionError("Greybox retried inside propose")
        return None, 0.2, "build failed"

    monkeypatch.setattr(greybox_module, "realize_and_build", failed_build)

    with pytest.raises(GenerationFailure, match="build failed"):
        generator.propose()
    assert len(mutation_calls) == 1
    assert len(build_calls) == 1


def test_greybox_no_schedulable_energy_is_typed_generation_failure():
    generator = GreyboxGenerator("demo", "/missing", "demo:base")
    generator.corpus.append(
        {
            "cand": {"candidate_id": "zero", "provenance": {}},
            "seq": [],
            "energy": 0,
            "base_energy": 0,
        }
    )
    with pytest.raises(GenerationFailure, match="energy"):
        generator.propose()


def test_random_all_build_failure_is_retryable_not_permanent_exhaustion(monkeypatch):
    generator = RandomGenerator("demo", "/missing", "demo:base", k=2)
    monkeypatch.setattr(
        random_module,
        "propose_batch",
        lambda *a, **k: (
            [
                {"summary": "a", "task": "a", "env": "a"},
                {"summary": "b", "task": "b", "env": "b"},
            ],
            {"cost_usd": 0.1},
        ),
    )
    monkeypatch.setattr(generator, "_make_one", lambda item: (None, 0.0))
    with pytest.raises(GenerationFailure, match="no buildable"):
        generator.propose()
