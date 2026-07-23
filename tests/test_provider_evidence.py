from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from skillrace.provider_evidence import (
    ProviderEvidenceError,
    validate_rate_card,
    validate_runtime_probes,
)


ROOT = Path(__file__).resolve().parents[1]
RATE_CARD = (
    ROOT / "experiments/provider-evidence/yunwu-2026-07-12/rate-card.json"
)
RUNTIME_PROBES = RATE_CARD.with_name("runtime-probes.json")


def test_dated_rate_card_derives_both_frozen_track_rates():
    assert validate_rate_card(RATE_CARD) == {
        "schema": "yunwu-rate-card-validation/1",
        "retrieved_at": "2026-07-12T14:16:46.015262Z",
        "billing_currency": "YUNWU_CREDIT",
        "group": "default",
        "models": ["glm-4.5-flash", "deepseek-v4-flash"],
    }


def test_rate_card_rejects_a_silent_price_change(tmp_path):
    data = json.loads(RATE_CARD.read_text(encoding="utf-8"))
    data = copy.deepcopy(data)
    data["models"][1]["output_provider_credits_per_million"] = 0.137
    path = tmp_path / "rate-card.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ProviderEvidenceError, match="derived token rates"):
        validate_rate_card(path)


def test_rate_card_forbids_claiming_a_usd_conversion(tmp_path):
    data = json.loads(RATE_CARD.read_text(encoding="utf-8"))
    data["currency"]["usd_conversion_policy"] = "provider-rate-is-usd"
    path = tmp_path / "rate-card.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ProviderEvidenceError, match="currency evidence"):
        validate_rate_card(path)


def test_direct_and_pi_probe_evidence_is_complete_for_both_tracks():
    assert validate_runtime_probes(RUNTIME_PROBES, repo_root=ROOT) == {
        "schema": "yunwu-runtime-probes-validation/1",
        "models": ["glm-4.5-flash", "deepseek-v4-flash"],
        "direct_probes": 2,
        "pi_probes": 2,
        "pi_version": "0.73.1",
    }
