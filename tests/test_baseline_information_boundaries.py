from __future__ import annotations

import json

import skillrace.generator as random_module
import skillrace.greybox as greybox_module
from skillrace.generator import RandomGenerator
from skillrace.greybox import GreyboxGenerator

from tests.helpers import assistant_tool, write_session


class PoisonPath:
    def __fspath__(self):
        raise AssertionError("random must not open or convert an execution path")


def test_random_fold_is_a_true_noop_over_poisoned_execution_data():
    generator = RandomGenerator(
        "demo", "/definitely/missing", "demo:base", source="random"
    )
    candidate = {
        "reasoning": object(),
        "properties": object(),
        "tree": object(),
        "provenance": {"source": "random"},
    }

    before = generator.state()
    assert generator.fold(candidate, PoisonPath()) is None
    assert generator.state() == before


def test_greybox_mutation_prompt_contains_only_skill_seed_nl_and_tool_sequence(
    tmp_path, monkeypatch
):
    generator = GreyboxGenerator(
        "demo", "/definitely/missing", "demo:base", level="L1"
    )
    run = write_session(
        [
            assistant_tool(
                "bash",
                {"command": "pytest -q"},
                extra_content=[
                    {"type": "thinking", "thinking": "SECRET_REASONING"},
                    {"type": "text", "text": "SECRET_OUTCOME"},
                ],
            )
        ],
        tmp_path / "run",
    )
    generator.fold_initial(
        {
            "candidate_id": "seed",
            "prompt": "SECRET_PROMPT_FALLBACK",
            "provenance": {
                "source": "bootstrap",
                "task_nl": "repair the parser",
                "env_nl": "one malformed token",
                "reasoning": "SECRET_PROVENANCE_REASONING",
                "targeted_property": "SECRET_PROPERTY",
                "tree": "SECRET_TREE",
            },
        },
        run,
    )
    seen = []

    def fake_chat(messages, **kwargs):
        seen.append(messages[-1]["content"])
        return {
            "content": json.dumps({"task": "new task", "env": "new env"}),
            "cost_usd": 0.0,
        }

    monkeypatch.setattr(greybox_module, "chat", fake_chat)

    generator._mutate(generator.corpus[0])

    prompt = seen[0]
    assert "repair the parser" in prompt
    assert "one malformed token" in prompt
    assert "bash:pytest" in prompt
    for secret in [
        "SECRET_REASONING",
        "SECRET_OUTCOME",
        "SECRET_PROMPT_FALLBACK",
        "SECRET_PROVENANCE_REASONING",
        "SECRET_PROPERTY",
        "SECRET_TREE",
    ]:
        assert secret not in prompt
        assert secret not in json.dumps(generator.corpus)


def test_random_candidate_has_no_parent_or_skillrace_feedback_provenance(monkeypatch):
    generator = RandomGenerator(
        "demo", "/definitely/missing", "demo:base", source="random", build_retries=0
    )
    monkeypatch.setattr(
        random_module,
        "realize",
        lambda *args, **kwargs: (
            "do it",
            "RUN true",
            {
                "required_paths": ["/workspace"],
                "required_tools": ["bash"],
                "task_probe": {"command": "true", "allowed_exit_codes": [0]},
                "unsolved_check": None,
            },
            0.0,
        ),
    )
    monkeypatch.setattr(random_module, "build_image", lambda *args, **kwargs: (True, ""))

    candidate, _ = generator._make_one(
        {"summary": "fresh case", "task": "do it", "env": "empty project"}
    )

    assert candidate["sanity"]["task_probe"]["command"] == "true"
    assert candidate["provenance"]["independent_test"] is True
    for forbidden in [
        "parent_run_id",
        "parent_candidate",
        "branch_id",
        "guard",
        "mutation",
        "targeted_property",
        "episode",
        "tree_version",
    ]:
        assert forbidden not in candidate["provenance"]
