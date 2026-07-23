from __future__ import annotations

import io
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODELS = ("glm-4.5-flash", "deepseek-v4-flash")
SUPPORTED_MODELS = (
    "glm-4.5-flash",
    "glm-4.5",
    "glm-4.5-air",
    "glm-4.7",
    "grok-4.3",
    "grok-4-1-fast-reasoning",
    "qwen3.5-plus",
    "qwen3-coder-flash",
    "qwen3-coder-480b-a35b-instruct",
    "deepseek-v4-flash",
    "deepseek-v3.2",
)


def test_dual_manifest_names_two_separate_complete_tracks():
    manifest = json.loads(
        (ROOT / "experiments/protocols/issta-main.dual-model.draft.json").read_text()
    )
    assert manifest["schema"] == "dual-model-experiment/1"
    assert manifest["status"] == "draft"
    assert [track["model"] for track in manifest["tracks"]] == list(MODELS)
    assert len({track["protocol"] for track in manifest["tracks"]}) == 2
    assert len({track["output_root"] for track in manifest["tracks"]}) == 2
    for track in manifest["tracks"]:
        protocol = json.loads((ROOT / track["protocol"]).read_text())
        assert protocol["model"] == track["model"]
        assert protocol["budget"] == 30
        assert protocol["bootstrap_count"] == 10
        assert not any(key.endswith("_model") for key in protocol)


def test_pi_has_one_costless_catalog_per_track():
    for model in MODELS:
        path = ROOT / f"images/pi-base/models.yunwu.{model}.json"
        catalog = json.loads(path.read_text())
        provider = catalog["providers"]["yunwu"]
        assert provider["baseUrl"] == "https://yunwu.ai/v1"
        assert provider["apiKey"] == "yunwu_key"
        models = provider["models"]
        assert [record["id"] for record in models] == [model]
        assert "cost" not in models[0]
        if model == "glm-4.5-flash":
            assert models[0]["compat"]["requiresAssistantAfterToolResult"] is True
            assert models[0]["compat"]["requiresThinkingAsText"] is True


def test_every_supported_model_has_a_one_model_pi_catalog():
    from skillrace.model_policy import SUPPORTED_MODELS as policy_models

    assert policy_models == SUPPORTED_MODELS
    for model in policy_models:
        path = ROOT / f"images/pi-base/models.yunwu.{model}.json"
        catalog = json.loads(path.read_text())
        provider = catalog["providers"]["yunwu"]
        assert provider["baseUrl"] == "https://yunwu.ai/v1"
        assert provider["apiKey"] == "yunwu_key"
        assert [record["id"] for record in provider["models"]] == [model]
        assert provider["models"][0]["reasoning"] is (
            model
            not in {
                "qwen3-coder-flash",
                "qwen3-coder-480b-a35b-instruct",
            }
        )


def test_generic_model_helpers_accept_supported_inventory_without_widening_headline():
    from skillrace.model_policy import AGENT_MODELS, EXPERIMENT_MODELS, SUPPORTED_MODELS

    assert EXPERIMENT_MODELS == MODELS
    assert SUPPORTED_MODELS == globals()["SUPPORTED_MODELS"]
    assert AGENT_MODELS == (
        "glm-4.5-flash",
        "glm-4.5",
        "glm-4.7",
        "grok-4.3",
        "grok-4-1-fast-reasoning",
        "qwen3.5-plus",
        "qwen3-coder-flash",
        "qwen3-coder-480b-a35b-instruct",
        "deepseek-v4-flash",
        "deepseek-v3.2",
    )
    assert "glm-4.5-air" not in AGENT_MODELS
    from skillrace.model_policy import REASONING_TRACE_MODELS

    assert REASONING_TRACE_MODELS == (
        "glm-4.5-flash",
        "glm-4.5",
        "glm-4.7",
        "grok-4.3",
        "grok-4-1-fast-reasoning",
        "qwen3.5-plus",
        "deepseek-v4-flash",
        "deepseek-v3.2",
    )
    assert "qwen3-coder-flash" not in REASONING_TRACE_MODELS
    assert "qwen3-coder-480b-a35b-instruct" not in REASONING_TRACE_MODELS
    for path in (
        ROOT / "images/pi-base/build.sh",
        ROOT / "images/pi-base/run_once.sh",
    ):
        text = path.read_text()
        for model in SUPPORTED_MODELS:
            assert model in text
    for path in (ROOT / "scripts/yunwu_hello.py", ROOT / "scripts/yunwu_hello_cost.py"):
        assert "choices=SUPPORTED_MODELS" in path.read_text()


def test_pi_image_build_is_one_model_per_track_and_manual_runner_uses_credits():
    dockerfile = (ROOT / "images/pi-base/Dockerfile.pi-base").read_text()
    build = (ROOT / "images/pi-base/build.sh").read_text()
    runner = (ROOT / "images/pi-base/run_once.sh").read_text()
    assert "ARG MODEL_CONFIG=" in dockerfile
    assert "ARG TRACK_MODEL=" in dockerfile
    assert 'models.length!==1' in dockerfile
    assert 'skillrace/pi-base:${VERSION}-${MODEL}' in build
    assert 'skillrace/pi-base:0.73.1-${MODEL}' in runner
    assert 'trap cleanup EXIT INT TERM' in runner
    assert 'docker rm -f "$CONTAINER_NAME"' in runner
    assert "cost_provider_credits" in runner
    assert '"cost_usd": None' in runner
    assert 'cost: $' not in runner


def test_model_policy_records_user_supplied_custom_credit_rates_not_usd():
    from skillrace.model_policy import (
        BILLING_CURRENCY,
        DEFAULT_DEVELOPMENT_MODEL,
        EXPERIMENT_MODELS,
        PROVIDER_CREDIT_RATES,
        SKILLGEN_TRACK_IMAGES,
    )

    assert EXPERIMENT_MODELS == MODELS
    assert DEFAULT_DEVELOPMENT_MODEL == "glm-4.5-flash"
    assert BILLING_CURRENCY == "YUNWU_CREDIT"
    assert PROVIDER_CREDIT_RATES == {
        "glm-4.5-flash": (0.02, 0.08),
        "glm-4.5": (1.6, 6.4),
        "deepseek-v4-flash": (1.0, 2.0),
        "deepseek-v3.2": (2.0, 3.0),
        "gpt-5.4-mini": (0.75, 4.5),
        "qwen3.5-plus": (0.8, 4.8),
    }
    assert SKILLGEN_TRACK_IMAGES == {
        model: f"skillrace/skillgen-base:0.73.1-{model}" for model in MODELS
    }


def test_development_inventory_and_v32_credit_rate_are_recorded():
    from skillrace.model_policy import (
        DEVELOPMENT_CANDIDATE_CREDIT_RATES,
        DEVELOPMENT_CANDIDATE_MODELS,
        EXPERIMENT_MODELS,
        PROVISIONAL_DEVELOPMENT_RATE_CARD_VERSION,
        provider_credits_for_known_model,
        rate_card_version_for_model,
    )

    assert DEVELOPMENT_CANDIDATE_MODELS == (
        "glm-4.5",
        "glm-4.5-air",
        "glm-4.7",
        "grok-4.3",
        "grok-4-1-fast-reasoning",
        "qwen3.5-plus",
        "qwen3-coder-flash",
        "qwen3-coder-480b-a35b-instruct",
        "deepseek-v3.2",
        "gpt-5.4-mini",
    )
    assert "deepseek-v3.2" not in EXPERIMENT_MODELS
    assert DEVELOPMENT_CANDIDATE_CREDIT_RATES == {}
    assert PROVISIONAL_DEVELOPMENT_RATE_CARD_VERSION.endswith("v3.2-v1")
    assert rate_card_version_for_model("deepseek-v3.2") != (
        PROVISIONAL_DEVELOPMENT_RATE_CARD_VERSION
    )
    assert provider_credits_for_known_model(
        "deepseek-v3.2", 150, 20, cached_input_tokens=50
    ) == pytest.approx((100 * 2.0 + 50 * 2.0 + 20 * 3.0) / 1_000_000)


def test_active_runtime_defaults_do_not_name_superseded_qwen_models():
    active = [
        *sorted((ROOT / "skillrace").glob("*.py")),
        ROOT / "paper/skillrace.tex",
        ROOT / "website/index.html",
    ]
    offenders = []
    for path in active:
        text = path.read_text()
        if "qwen3.5-flash" in text or "qwen3.6-flash" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_short_structured_helper_calls_disable_hidden_provider_thinking():
    helpers = (
        "generator.py",
        "greybox.py",
        "tree.py",
        "guards.py",
        "segment.py",
        "compile_checks.py",
        "check_properties.py",
        "gen_agent.py",
    )
    offenders = [
        name
        for name in helpers
        if "reasoning=True" in (ROOT / "skillrace" / name).read_text()
    ]
    assert offenders == []


@pytest.mark.parametrize(
    ("model", "input_rate", "output_rate", "journal_mode"),
    (
        ("glm-4.5-flash", 0.02, 0.08, "production"),
        ("deepseek-v4-flash", 1.0, 2.0, "production"),
        ("deepseek-v3.2", 2.0, 3.0, "development"),
    ),
)
def test_direct_receipt_records_yunwu_credits_without_claiming_usd(
    model, input_rate, output_rate, journal_mode, tmp_path, monkeypatch
):
    import skillrace.closeai as client

    class Response(io.BytesIO):
        status = 200
        headers = {"x-request-id": "credit-test-request"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    payload = {
        "id": "credit-test-response",
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }
    monkeypatch.setenv("yunwu_key", "not-written-to-ledgers")
    monkeypatch.setattr(
        client.urllib.request,
        "urlopen",
        lambda request, timeout: Response(json.dumps(payload).encode()),
    )
    result = client.chat(
        [{"role": "user", "content": "billing contract"}],
        model=model,
        operation_id=f"test.yunwu-credit-contract.{model}",
        ledger_path=tmp_path / f"{model}.jsonl",
        retries=1,
        reasoning=False,
        journal_mode=journal_mode,
    )

    expected = (11 * input_rate + 7 * output_rate) / 1_000_000
    assert result["cost_provider_credits"] == pytest.approx(expected)
    assert result["cost_usd"] is None
    receipt = result["journal_terminal_receipt"]
    assert receipt["billing_currency"] == "YUNWU_CREDIT"
    assert receipt["cost_provider_credits"] == pytest.approx(expected)
    assert receipt["cost_usd"] is None
    assert receipt["price_usd"] is None
    assert client.validate_chat_result(
        result,
        expected_model=model,
        expected_operation_id=f"test.yunwu-credit-contract.{model}",
    )["cost_provider_credits"] == pytest.approx(expected)


def test_v32_cannot_create_a_production_receipt_before_final_model_freeze():
    import skillrace.closeai as client

    with pytest.raises(client.UnknownPricingError, match="not frozen"):
        client.chat(
            [{"role": "user", "content": "must not reach provider"}],
            model="deepseek-v3.2",
            journal_mode="production",
        )


def test_v32_default_calls_are_journaled_as_development(
    tmp_path, monkeypatch
):
    """Shared campaign helpers may omit mode without forging headline evidence."""

    import skillrace.closeai as client

    class Response(io.BytesIO):
        status = 200
        headers = {"x-request-id": "v32-development-default"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    payload = {
        "id": "v32-development-response",
        "model": "deepseek-v3.2",
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
    }
    ledger = tmp_path / "v32.jsonl"
    monkeypatch.setenv("yunwu_key", "not-written-to-ledgers")
    monkeypatch.setattr(
        client.urllib.request,
        "urlopen",
        lambda request, timeout: Response(json.dumps(payload).encode()),
    )

    result = client.chat(
        [{"role": "user", "content": "development campaign helper"}],
        model="deepseek-v3.2",
        operation_id="test.v32-development-default",
        ledger_path=ledger,
        retries=1,
        reasoning=False,
    )

    assert result["journal_terminal_receipt"]["model"] == "deepseek-v3.2"
    assert [json.loads(line)["event"] for line in ledger.read_text().splitlines()] == [
        "intent",
        "terminal",
    ]
