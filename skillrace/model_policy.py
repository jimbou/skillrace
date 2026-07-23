"""Dual-track model and provider-credit policy fixed before experiment execution."""

from __future__ import annotations

import math


FROZEN_PROVIDER = "yunwu"
GLM_MODEL = "glm-4.5-flash"
GLM_45_MODEL = "glm-4.5"
GLM_45_AIR_MODEL = "glm-4.5-air"
GLM_47_MODEL = "glm-4.7"
GROK_43_MODEL = "grok-4.3"
GROK_41_FAST_REASONING_MODEL = "grok-4-1-fast-reasoning"
QWEN35_PLUS_MODEL = "qwen3.5-plus"
QWEN3_CODER_FLASH_MODEL = "qwen3-coder-flash"
QWEN3_CODER_480B_MODEL = "qwen3-coder-480b-a35b-instruct"
DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_V3_MODEL = "deepseek-v3.2"
GPT_54_MINI_MODEL = "gpt-5.4-mini"
GLM_MODELS = (GLM_MODEL, GLM_45_MODEL, GLM_45_AIR_MODEL, GLM_47_MODEL)
QWEN_HYBRID_THINKING_MODELS = (QWEN35_PLUS_MODEL,)
STREAM_ONLY_MODELS = (GLM_45_MODEL,)
EXPERIMENT_MODELS = (GLM_MODEL, DEEPSEEK_MODEL)
RESPONSES_MODELS = (GPT_54_MINI_MODEL,)
# These models are usable only for explicitly development-labelled work until the final
# headline inventory is hardcoded. They never widen EXPERIMENT_MODELS implicitly.
DEVELOPMENT_CANDIDATE_MODELS = (
    GLM_45_MODEL,
    GLM_45_AIR_MODEL,
    GLM_47_MODEL,
    GROK_43_MODEL,
    GROK_41_FAST_REASONING_MODEL,
    QWEN35_PLUS_MODEL,
    QWEN3_CODER_FLASH_MODEL,
    QWEN3_CODER_480B_MODEL,
    DEEPSEEK_V3_MODEL,
    GPT_54_MINI_MODEL,
)
SUPPORTED_MODELS = (
    GLM_MODEL,
    GLM_45_MODEL,
    GLM_45_AIR_MODEL,
    GLM_47_MODEL,
    GROK_43_MODEL,
    GROK_41_FAST_REASONING_MODEL,
    QWEN35_PLUS_MODEL,
    QWEN3_CODER_FLASH_MODEL,
    QWEN3_CODER_480B_MODEL,
    DEEPSEEK_MODEL,
    DEEPSEEK_V3_MODEL,
)
# Models observed to emit structured OpenAI tool calls through Yunwu and therefore
# usable by the stock Pi coding agent. GLM 4.5 Air is directly callable but Yunwu
# currently renders requested function calls as plain text, so it remains outside
# campaign execution until a future capability probe succeeds.
AGENT_MODELS = (
    GLM_MODEL,
    GLM_45_MODEL,
    GLM_47_MODEL,
    GROK_43_MODEL,
    GROK_41_FAST_REASONING_MODEL,
    QWEN35_PLUS_MODEL,
    QWEN3_CODER_FLASH_MODEL,
    QWEN3_CODER_480B_MODEL,
    DEEPSEEK_MODEL,
    DEEPSEEK_V3_MODEL,
)
# Models whose Yunwu/Pi streaming contract yields a distinct reasoning block. The
# two Qwen3 Coder variants emit structured tools but no distinct trace. The 4.1 Grok
# slug does yield Pi thinking blocks but remains a retired xAI alias.
REASONING_TRACE_MODELS = (
    GLM_MODEL,
    GLM_45_MODEL,
    GLM_47_MODEL,
    GROK_43_MODEL,
    GROK_41_FAST_REASONING_MODEL,
    QWEN35_PLUS_MODEL,
    DEEPSEEK_MODEL,
    DEEPSEEK_V3_MODEL,
)
DEFAULT_DEVELOPMENT_MODEL = GLM_MODEL
FROZEN_CONTEXT_WINDOW = 128_000
HIDDEN_TEMPLATE_BASE_IMAGE = "skillrace/skillgen-base:0.73.1-construction"
SKILLGEN_TRACK_IMAGES = {
    model: f"skillrace/skillgen-base:0.73.1-{model}"
    for model in EXPERIMENT_MODELS
}

# Yunwu's public status declares a CUSTOM currency with the symbol ⚡. Its public
# pricing application computes quota-type-0 rates as 2 * model_ratio for input and
# 2 * model_ratio * completion_ratio for output. The 2026-07-12 catalog records:
#   glm-4.5-flash:      model_ratio=.01, completion_ratio=4  -> .02 / .08
#   deepseek-v4-flash:  model_ratio=.5,  completion_ratio=2  -> 1.0 / 2.0
# in the default group. These are custom provider credits per million tokens, not
# USD. Purchase-currency conversion is account-specific and is reported separately.
BILLING_CURRENCY = "YUNWU_CREDIT"
BILLING_SYMBOL = "⚡"
BILLING_GROUP = "default"
RATE_CARD_VERSION = "yunwu-public-rate-card/2026-07-12-dual-flash-v1"
PROVIDER_CREDIT_RATES = {
    GLM_MODEL: (0.02, 0.08),
    GLM_45_MODEL: (1.6, 6.4),
    DEEPSEEK_MODEL: (1.0, 2.0),
    DEEPSEEK_V3_MODEL: (2.0, 3.0),
    GPT_54_MINI_MODEL: (0.75, 4.5),
    QWEN35_PLUS_MODEL: (0.8, 4.8),
}
# DeepSeek advertises cache_ratio=.02, yielding .02 custom credits/M cached
# input tokens. GLM has no distinct cache rate in the catalog; cached input is
# conservatively accounted at its ordinary input rate.
PROVIDER_CACHE_READ_RATES = {
    GLM_MODEL: 0.02,
    GLM_45_MODEL: 1.6,
    DEEPSEEK_MODEL: 0.02,
    DEEPSEEK_V3_MODEL: 2.0,
    GPT_54_MINI_MODEL: 0.075,
    QWEN35_PLUS_MODEL: 0.8,
}

# The user transcribed these values from Yunwu's model-pricing UI on 2026-07-13.
# This is adequate for transparent development accounting, but is deliberately not a
# frozen headline rate card. Yunwu did not provide a separate V3.2 cache-read price in
# that record, so cached input is charged conservatively at the ordinary input rate.
PROVISIONAL_DEVELOPMENT_RATE_CARD_VERSION = (
    "yunwu-user-reported-rate-card/2026-07-13-v3.2-v1"
)
DEVELOPMENT_CANDIDATE_CREDIT_RATES = {}
DEVELOPMENT_CANDIDATE_CACHE_READ_RATES = {
    DEEPSEEK_V3_MODEL: 2.0,
}


def has_known_provider_credit_rate(model: str) -> bool:
    """Whether a model has a dated accounting rate, frozen or development-only."""

    return (
        model in PROVIDER_CREDIT_RATES
        and model in PROVIDER_CACHE_READ_RATES
    ) or (
        model in DEVELOPMENT_CANDIDATE_CREDIT_RATES
        and model in DEVELOPMENT_CANDIDATE_CACHE_READ_RATES
    )


def rate_card_version_for_model(model: str) -> str:
    """Return the accounting provenance for a selected or development model."""

    if model in PROVIDER_CREDIT_RATES:
        return RATE_CARD_VERSION
    if model in DEVELOPMENT_CANDIDATE_CREDIT_RATES:
        return PROVISIONAL_DEVELOPMENT_RATE_CARD_VERSION
    raise ValueError(f"model has no recorded provider-credit rate: {model}")


def provider_credits_for_known_model(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cached_input_tokens: int = 0,
) -> float:
    """Calculate credits for a frozen track or explicitly dated development candidate."""

    if model in PROVIDER_CREDIT_RATES:
        input_rate, output_rate = PROVIDER_CREDIT_RATES[model]
        cache_rate = PROVIDER_CACHE_READ_RATES[model]
    elif model in DEVELOPMENT_CANDIDATE_CREDIT_RATES:
        input_rate, output_rate = DEVELOPMENT_CANDIDATE_CREDIT_RATES[model]
        cache_rate = DEVELOPMENT_CANDIDATE_CACHE_READ_RATES[model]
    else:
        raise ValueError(f"model has no recorded provider-credit rate: {model}")
    for label, value in (
        ("input_tokens", input_tokens),
        ("output_tokens", output_tokens),
        ("cached_input_tokens", cached_input_tokens),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{label} must be a non-negative integer")
    if cached_input_tokens > input_tokens:
        raise ValueError("cached_input_tokens cannot exceed input_tokens")
    uncached = input_tokens - cached_input_tokens
    value = (
        uncached * input_rate
        + cached_input_tokens * cache_rate
        + output_tokens * output_rate
    ) / 1_000_000
    if not math.isfinite(value):
        raise ValueError("computed provider credits are not finite")
    return value


def require_experiment_model(model: str) -> str:
    """Return a selected track model or reject cross-track/unknown identifiers."""

    if model not in EXPERIMENT_MODELS:
        raise ValueError(f"model is not in the frozen dual-track set: {model}")
    return model


def require_supported_model(model: str) -> str:
    """Return a model with a checked-in runtime catalog or reject the identifier."""

    if model not in SUPPORTED_MODELS:
        raise ValueError(f"model is not in the supported Yunwu set: {model}")
    return model


def skillgen_track_image(model: str) -> str:
    """Return the model-catalog-frozen runtime used by RQ3 hidden executions."""

    return SKILLGEN_TRACK_IMAGES[require_experiment_model(model)]


def provider_credits(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cached_input_tokens: int = 0,
) -> float:
    """Return frozen Yunwu custom credits for one model-homogeneous operation."""

    require_experiment_model(model)
    return provider_credits_for_known_model(
        model,
        input_tokens,
        output_tokens,
        cached_input_tokens=cached_input_tokens,
    )
