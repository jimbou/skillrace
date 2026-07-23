from __future__ import annotations

import json

import pytest

from skillrace.run_case import _trace_cost


def test_trace_cost_uses_track_rate_and_cache_read_without_claiming_usd(tmp_path):
    trace = tmp_path / "session.jsonl"
    trace.write_text(
        json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "usage": {
                        "input": 100,
                        "output": 20,
                        "cacheRead": 50,
                    },
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    receipt = _trace_cost(trace, "deepseek-v4-flash")
    assert receipt["cost_provider_credits"] == pytest.approx(
        (100 * 1.0 + 20 * 2.0 + 50 * 0.02) / 1_000_000
    )
    assert receipt["cache_read"] == 50
    assert receipt["billing_currency"] == "YUNWU_CREDIT"
    assert receipt["cost_usd"] is None


def test_trace_cost_uses_the_dated_development_rate_without_claiming_headline_status(tmp_path):
    trace = tmp_path / "session.jsonl"
    trace.write_text(
        json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "usage": {"input": 100, "output": 20, "cacheRead": 50},
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    receipt = _trace_cost(trace, "deepseek-v3.2")

    assert receipt["turns"] == 1
    assert receipt["in"] == 100
    assert receipt["out"] == 20
    assert receipt["cache_read"] == 50
    assert receipt["billing_status"] == "known"
    assert receipt["billing_currency"] == "YUNWU_CREDIT"
    assert receipt["cost_provider_credits"] == pytest.approx(
        (100 * 2.0 + 20 * 3.0 + 50 * 2.0) / 1_000_000
    )
    assert receipt["price_provider_credits"] == pytest.approx(0.00036)


def test_trace_cost_never_represents_an_unpriced_model_as_free(tmp_path):
    trace = tmp_path / "session.jsonl"
    trace.write_text(
        json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "usage": {"input": 100, "output": 20, "cacheRead": 50},
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    receipt = _trace_cost(trace, "unpriced-development-model")

    assert receipt["billing_status"] == "unknown"
    assert receipt["billing_currency"] is None
    assert receipt["cost_provider_credits"] is None
    assert receipt["price_provider_credits"] is None
