from __future__ import annotations

import json
from pathlib import Path

from skillrace.model_policy import EXPERIMENT_MODELS


ROOT = Path(__file__).resolve().parents[1]
FALLBACK_CONFIG = (
    ROOT
    / "experiments/development-pilots/2026-07-13/deepseek-v3.2-model.json"
)


def test_development_fallback_model_is_explicitly_outside_headline_catalog():
    config = json.loads(FALLBACK_CONFIG.read_text())
    model = config["providers"]["yunwu"]["models"][0]

    assert model["id"] == "deepseek-v3.2"
    assert model["id"] not in EXPERIMENT_MODELS
    assert config["development_only"] is True
    assert config["providers"]["yunwu"]["apiKey"] == "yunwu_key"
    assert model["reasoning"] is True
    assert model["maxTokens"] == 65536
    assert model["thinkingLevelMap"] == {"medium": "minimal"}
    assert model["compat"]["thinkingFormat"] == "deepseek"
    assert (
        model["compat"]["requiresReasoningContentOnAssistantMessages"] is True
    )
