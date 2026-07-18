from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class ProviderModel:
    provider: str
    friendly_model: str
    upstream_model: str
    base_url: str
    key_environment: str
    input_usd_per_million: Decimal | None
    output_usd_per_million: Decimal | None
    cache_read_usd_per_million: Decimal | None
    cache_write_usd_per_million: Decimal | None
    thinking_format: str | None


_MODELS = (
    ProviderModel(
        provider="yunwu",
        friendly_model="deepseek-v3.2",
        upstream_model="deepseek-v3.2",
        base_url="https://yunwu.ai/v1",
        key_environment="yunwu_key",
        input_usd_per_million=None,
        output_usd_per_million=None,
        cache_read_usd_per_million=None,
        cache_write_usd_per_million=None,
        thinking_format="deepseek",
    ),
    ProviderModel(
        provider="lab",
        friendly_model="deepseek-v4-flash",
        upstream_model="ds/deepseek-v4-flash",
        base_url="https://llm.xmcp.ltd/v1",
        key_environment="LAB_KEY_UNLIMITED",
        input_usd_per_million=Decimal("0.143"),
        output_usd_per_million=Decimal("0.286"),
        cache_read_usd_per_million=Decimal("0.003"),
        cache_write_usd_per_million=None,
        thinking_format="deepseek",
    ),
    ProviderModel(
        provider="lab",
        friendly_model="qwen3.6-flash",
        upstream_model="ali/qwen3.6-flash",
        base_url="https://llm.xmcp.ltd/v1",
        key_environment="LAB_KEY_UNLIMITED",
        input_usd_per_million=Decimal("0.171"),
        output_usd_per_million=Decimal("1.029"),
        cache_read_usd_per_million=Decimal("0.017"),
        cache_write_usd_per_million=None,
        thinking_format="qwen",
    ),
)


def resolve_model(provider: str, model: str) -> ProviderModel:
    for item in _MODELS:
        if item.provider == provider and item.friendly_model == model:
            return item
    raise ValueError(f"unsupported provider/model pair: {provider}/{model}")


def qualified_model(model: ProviderModel) -> str:
    return f"{model.provider}/{model.friendly_model}"


def estimate_cost(
    model: ProviderModel, usage: Mapping[str, Any]
) -> Decimal | None:
    input_tokens = int(
        usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
    )
    output_tokens = int(
        usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
    )
    cache_read_tokens = int(usage.get("cache_read_tokens", 0) or 0)
    cache_write_tokens = int(usage.get("cache_write_tokens", 0) or 0)
    if input_tokens and model.input_usd_per_million is None:
        return None
    if output_tokens and model.output_usd_per_million is None:
        return None
    if (
        cache_read_tokens
        and model.cache_read_usd_per_million is None
        and model.input_usd_per_million is None
    ):
        return None
    if cache_write_tokens and model.cache_write_usd_per_million is None:
        return None
    input_rate = model.input_usd_per_million or Decimal("0")
    output_rate = model.output_usd_per_million or Decimal("0")
    cache_read_rate = (
        model.cache_read_usd_per_million
        if model.cache_read_usd_per_million is not None
        else input_rate
    )
    cache_write_rate = model.cache_write_usd_per_million or Decimal("0")
    return (
        Decimal(input_tokens) * input_rate
        + Decimal(output_tokens) * output_rate
        + Decimal(cache_read_tokens) * cache_read_rate
        + Decimal(cache_write_tokens) * cache_write_rate
    ) / Decimal(1_000_000)


def write_pi_models(path: str | Path, model: ProviderModel) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    compat: dict[str, Any] = {
        "supportsDeveloperRole": False,
        "supportsReasoningEffort": False,
        "maxTokensField": "max_tokens",
    }
    if model.thinking_format:
        compat["thinkingFormat"] = model.thinking_format
    value = {
        "providers": {
            model.provider: {
                "baseUrl": model.base_url,
                "api": "openai-completions",
                "apiKey": model.key_environment,
                "authHeader": True,
                "models": [
                    {
                        "id": model.upstream_model,
                        "name": model.friendly_model,
                        "api": "openai-completions",
                        "reasoning": model.thinking_format is not None,
                        "input": ["text", "image"],
                        "cost": {
                            "input": float(model.input_usd_per_million or Decimal("0")),
                            "output": float(model.output_usd_per_million or Decimal("0")),
                            "cacheRead": float(
                                model.cache_read_usd_per_million or Decimal("0")
                            ),
                            "cacheWrite": float(
                                model.cache_write_usd_per_million or Decimal("0")
                            ),
                        },
                        "contextWindow": 131072,
                        "maxTokens": 32768,
                        "compat": compat,
                    }
                ],
            }
        }
    }
    destination.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return destination
