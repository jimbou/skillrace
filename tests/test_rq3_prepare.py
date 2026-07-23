from __future__ import annotations

import json
from pathlib import Path

from skillrace.closeai import nonproduction_chat_fixture
from skillrace.rq3_prepare import benchmark_template_hash, prepare_scenario
from skillrace.scenario_contract import load_scenario


ROOT = Path(__file__).resolve().parents[1]


def _chat(label: str, calls: list[str]):
    def respond(_messages, **settings):
        model = settings["model"]
        calls.append(model)
        return {
            "content": f"# {label}\n\nGeneral instructions for {model}.\n",
            "model": model,
            "id": f"fixture-{label}-{model}",
            "usage": {"prompt_tokens": 8, "completion_tokens": 6},
            "cost_provider_credits": 0.01,
        }

    return nonproduction_chat_fixture(respond)


def test_each_model_prepares_one_private_base_skill_from_identical_benchmark(tmp_path):
    source = ROOT / "scenarios" / "argparse-cli"
    calls: list[str] = []
    glm = prepare_scenario(
        source,
        tmp_path / "glm" / "argparse-cli",
        model="glm-4.5-flash",
        chat_fn=_chat("glm", calls),
    )
    deepseek = prepare_scenario(
        source,
        tmp_path / "deepseek" / "argparse-cli",
        model="deepseek-v4-flash",
        chat_fn=_chat("deepseek", calls),
    )

    assert calls == ["glm-4.5-flash", "deepseek-v4-flash"]
    assert glm["benchmark_template_hash"] == deepseek["benchmark_template_hash"]
    assert glm["benchmark_template_hash"] == benchmark_template_hash(source)
    assert glm["base_skill_hash"] != deepseek["base_skill_hash"]
    assert load_scenario(tmp_path / "glm" / "argparse-cli").base_skill_sha256 == glm[
        "base_skill_hash"
    ]
    assert json.loads(
        (tmp_path / "deepseek" / "argparse-cli" / ".skillrace-preparation.json").read_text()
    ) == deepseek


def test_preparation_resumes_without_a_second_model_call(tmp_path):
    source = ROOT / "scenarios" / "config-parser"
    output = tmp_path / "config-parser"
    first = prepare_scenario(
        source,
        output,
        model="glm-4.5-flash",
        chat_fn=_chat("first", []),
    )
    second = prepare_scenario(
        source,
        output,
        model="glm-4.5-flash",
        chat_fn=nonproduction_chat_fixture(
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("base generation must be exactly once")
            )
        ),
    )
    assert second == first
