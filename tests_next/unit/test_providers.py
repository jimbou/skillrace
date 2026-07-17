import json
from decimal import Decimal
from pathlib import Path

import pytest

from skillrace_next.runtime.providers import (
    estimate_cost,
    qualified_model,
    resolve_model,
    write_pi_models,
)


def test_lab_friendly_models_resolve_to_distinct_upstream_models() -> None:
    deepseek = resolve_model("lab", "deepseek-v4-flash")
    qwen = resolve_model("lab", "qwen3.6-flash")

    assert deepseek.upstream_model == "ds/deepseek-v4-flash"
    assert qwen.upstream_model == "ali/qwen3.6-flash"
    assert deepseek.key_environment == "LAB_KEY_UNLIMITED"
    assert deepseek.base_url == "https://llm.xmcp.ltd/v1"
    assert qualified_model(deepseek) == "lab/deepseek-v4-flash"
    assert qualified_model(qwen) == "lab/qwen3.6-flash"


def test_provider_model_pair_is_validated() -> None:
    assert resolve_model("yunwu", "deepseek-v3.2").upstream_model == "deepseek-v3.2"

    with pytest.raises(ValueError, match="unsupported provider/model"):
        resolve_model("yunwu", "qwen3.6-flash")
    with pytest.raises(ValueError, match="unsupported provider/model"):
        resolve_model("unknown", "deepseek-v4-flash")


def test_pi_catalog_contains_only_selected_lab_upstream_model(tmp_path: Path) -> None:
    selected = resolve_model("lab", "qwen3.6-flash")
    path = write_pi_models(tmp_path / "models.json", selected)

    value = json.loads(path.read_text(encoding="utf-8"))
    assert set(value["providers"]) == {"lab"}
    provider = value["providers"]["lab"]
    assert provider["baseUrl"] == "https://llm.xmcp.ltd/v1"
    assert provider["api"] == "openai-completions"
    assert provider["apiKey"] == "LAB_KEY_UNLIMITED"
    assert [model["id"] for model in provider["models"]] == ["ali/qwen3.6-flash"]


def test_cost_uses_known_rates_and_refuses_unknown_cache_write_price() -> None:
    selected = resolve_model("lab", "deepseek-v4-flash")

    priced = estimate_cost(
        selected,
        {
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "cache_read_tokens": 1_000_000,
            "cache_write_tokens": 0,
        },
    )
    unpriced = estimate_cost(
        selected,
        {
            "input_tokens": 1,
            "output_tokens": 1,
            "cache_read_tokens": 0,
            "cache_write_tokens": 1,
        },
    )

    assert priced == Decimal("0.432")
    assert unpriced is None


def test_cost_is_unpriced_when_provider_rates_are_unknown() -> None:
    selected = resolve_model("yunwu", "deepseek-v3.2")

    assert estimate_cost(selected, {"input_tokens": 1}) is None
