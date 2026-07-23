"""Validate the selected Yunwu rate-card evidence used by experiment accounting."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import re
from typing import Any

from .model_policy import (
    BILLING_CURRENCY,
    BILLING_GROUP,
    BILLING_SYMBOL,
    EXPERIMENT_MODELS,
    PROVIDER_CACHE_READ_RATES,
    PROVIDER_CREDIT_RATES,
    provider_credits,
)


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_IDS = {"pricing", "status", "pricing_ui"}


class ProviderEvidenceError(ValueError):
    """The recorded provider evidence is incomplete or disagrees with policy."""


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProviderEvidenceError(f"{field} must be numeric")
    value = float(value)
    if value < 0 or value == float("inf") or value != value:
        raise ProviderEvidenceError(f"{field} must be finite and non-negative")
    return value


def validate_rate_card(path: str | pathlib.Path) -> dict[str, Any]:
    """Fail closed unless the evidence derives the exact frozen credit rates."""

    evidence_path = pathlib.Path(path)
    try:
        data = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProviderEvidenceError(f"cannot read rate-card evidence: {error}") from error
    if data.get("schema") != "yunwu-rate-card-evidence/1":
        raise ProviderEvidenceError("unsupported provider-evidence schema")
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z",
        str(data.get("retrieved_at", "")),
    ):
        raise ProviderEvidenceError("retrieved_at must be an exact UTC timestamp")

    sources = data.get("sources")
    if not isinstance(sources, dict) or set(sources) != _SOURCE_IDS:
        raise ProviderEvidenceError("pricing, status, and pricing_ui sources are required")
    for source_id, source in sources.items():
        if not isinstance(source, dict):
            raise ProviderEvidenceError(f"{source_id} source must be an object")
        if not str(source.get("url", "")).startswith("https://"):
            raise ProviderEvidenceError(f"{source_id} must use an HTTPS source")
        if not _SHA256.fullmatch(str(source.get("sha256", ""))):
            raise ProviderEvidenceError(f"{source_id} has no valid SHA-256")
        if not isinstance(source.get("bytes"), int) or source["bytes"] <= 0:
            raise ProviderEvidenceError(f"{source_id} has no positive byte count")

    currency = data.get("currency", {})
    if (
        currency.get("provider_status_type") != "CUSTOM"
        or currency.get("billing_currency") != BILLING_CURRENCY
        or currency.get("symbol") != BILLING_SYMBOL
        or currency.get("custom_exchange_rate") != 1
        or currency.get("usd_conversion_policy") != "not-derived"
    ):
        raise ProviderEvidenceError("currency evidence disagrees with frozen policy")
    group = data.get("group", {})
    if group != {"id": BILLING_GROUP, "ratio": 1}:
        raise ProviderEvidenceError("only the default group ratio is frozen")

    formula = data.get("formula", {})
    expected_formula = {
        "quota_type": 0,
        "unit": "provider credits per million tokens",
        "input": "2 * model_ratio * group_ratio",
        "output": "2 * model_ratio * completion_ratio * group_ratio",
        "cache_read": "input_rate * cache_ratio when advertised",
    }
    if formula != expected_formula:
        raise ProviderEvidenceError("pricing formula is not the reviewed quota-type-0 formula")

    records = data.get("models")
    if not isinstance(records, list):
        raise ProviderEvidenceError("models must be a list")
    by_id = {record.get("id"): record for record in records if isinstance(record, dict)}
    if tuple(record.get("id") for record in records) != EXPERIMENT_MODELS:
        raise ProviderEvidenceError("model records must exactly follow the frozen track order")
    for model in EXPERIMENT_MODELS:
        record = by_id[model]
        if record.get("quota_type") != 0 or BILLING_GROUP not in record.get(
            "enabled_groups", []
        ):
            raise ProviderEvidenceError(f"{model} is not enabled in the frozen group")
        ratio = _finite_number(record.get("model_ratio"), f"{model}.model_ratio")
        completion = _finite_number(
            record.get("completion_ratio"), f"{model}.completion_ratio"
        )
        derived_input = 2 * ratio
        derived_output = 2 * ratio * completion
        recorded = (
            _finite_number(
                record.get("input_provider_credits_per_million"), f"{model}.input_rate"
            ),
            _finite_number(
                record.get("output_provider_credits_per_million"), f"{model}.output_rate"
            ),
        )
        if recorded != (derived_input, derived_output):
            raise ProviderEvidenceError(f"{model} derived token rates do not match ratios")
        if recorded != PROVIDER_CREDIT_RATES[model]:
            raise ProviderEvidenceError(f"{model} evidence disagrees with model policy")
        cache_ratio = record.get("cache_ratio")
        derived_cache = derived_input if cache_ratio is None else derived_input * float(cache_ratio)
        cache_rate = _finite_number(
            record.get("cache_read_provider_credits_per_million"), f"{model}.cache_rate"
        )
        if cache_rate != derived_cache or cache_rate != PROVIDER_CACHE_READ_RATES[model]:
            raise ProviderEvidenceError(f"{model} cache rate disagrees with policy")

    return {
        "schema": "yunwu-rate-card-validation/1",
        "retrieved_at": data["retrieved_at"],
        "billing_currency": BILLING_CURRENCY,
        "group": BILLING_GROUP,
        "models": list(EXPERIMENT_MODELS),
    }


def _file_sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_runtime_probes(
    path: str | pathlib.Path,
    *,
    repo_root: str | pathlib.Path,
) -> dict[str, Any]:
    """Verify direct journals and successful Pi traces without contacting Yunwu."""

    root = pathlib.Path(repo_root)
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if (
        data.get("schema") != "yunwu-runtime-probes/1"
        or data.get("development_only") is not True
        or data.get("pi_version") != "0.73.1"
    ):
        raise ProviderEvidenceError("runtime probe metadata is malformed")
    ledger_record = data.get("direct_ledger", {})
    ledger_path = root / str(ledger_record.get("path", ""))
    if not ledger_path.is_file() or _file_sha256(ledger_path) != ledger_record.get(
        "sha256"
    ):
        raise ProviderEvidenceError("direct probe ledger hash mismatch")
    ledger = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    tracks = data.get("tracks")
    if not isinstance(tracks, list) or tuple(row.get("model") for row in tracks) != EXPERIMENT_MODELS:
        raise ProviderEvidenceError("runtime probe tracks disagree with model policy")

    for track in tracks:
        model = track["model"]
        config = root / track["model_config"]
        if _file_sha256(config) != track.get("model_config_sha256"):
            raise ProviderEvidenceError(f"{model} Pi model configuration drifted")
        direct = track.get("direct_probe", {})
        terminals = [
            row
            for row in ledger
            if row.get("event") == "terminal"
            and row.get("operation_id") == direct.get("operation_id")
        ]
        if len(terminals) != 1:
            raise ProviderEvidenceError(f"{model} direct terminal is missing or duplicated")
        terminal = terminals[0]
        if (
            terminal.get("event_id") != direct.get("event_id")
            or terminal.get("provider_model") != model
            or terminal.get("http_status") != 200
            or terminal.get("billing_currency") != BILLING_CURRENCY
            or terminal.get("cost_usd") is not None
            or terminal.get("usage", {}).get("reasoning_tokens")
            != direct.get("reasoning_tokens")
            or terminal.get("usage", {}).get("reasoning_tokens", 0) <= 0
            or terminal.get("cost_provider_credits")
            != direct.get("cost_provider_credits")
        ):
            raise ProviderEvidenceError(f"{model} direct probe receipt mismatch")

        pi = track.get("pi_probe", {})
        probe_root = root / pi.get("path", "")
        paths = {
            "session": probe_root / "session.jsonl",
            "cost": probe_root / "cost.json",
            "stdout": probe_root / "stdout.txt",
        }
        for name, file_path in paths.items():
            if not file_path.is_file() or _file_sha256(file_path) != pi.get(
                f"{name}_sha256"
            ):
                raise ProviderEvidenceError(f"{model} Pi {name} evidence drifted")
        assistants = []
        for line in paths["session"].read_text(encoding="utf-8").splitlines():
            message = json.loads(line).get("message", {})
            if message.get("role") == "assistant":
                assistants.append(message)
        thinking = [
            block.get("thinking", "")
            for message in assistants
            for block in message.get("content", [])
            if block.get("type") == "thinking" and block.get("thinking", "").strip()
        ]
        tools = [
            block.get("name")
            for message in assistants
            for block in message.get("content", [])
            if block.get("type") == "toolCall"
        ]
        if (
            len(assistants) != pi.get("assistant_turns")
            or len(thinking) != pi.get("thinking_turns")
            or sum(len(value) for value in thinking) != pi.get("thinking_characters")
            or tools != pi.get("tool_calls")
            or any(message.get("model") != model for message in assistants)
            or any(message.get("errorMessage") for message in assistants)
            or not paths["stdout"].read_text(encoding="utf-8").strip()
        ):
            raise ProviderEvidenceError(f"{model} Pi trace contract failed")
        cost = json.loads(paths["cost"].read_text(encoding="utf-8"))
        expected_cost = provider_credits(
            model,
            cost["in"] + cost.get("cache_read", 0),
            cost["out"],
            cached_input_tokens=cost.get("cache_read", 0),
        )
        if (
            cost.get("billing_currency") != BILLING_CURRENCY
            or cost.get("cost_usd") is not None
            or not math.isclose(
                float(cost.get("cost_provider_credits", -1)),
                expected_cost,
                rel_tol=0,
                abs_tol=1e-15,
            )
            or not math.isclose(
                float(cost.get("cost_provider_credits", -1)),
                float(pi.get("cost_provider_credits", -2)),
                rel_tol=0,
                abs_tol=1e-15,
            )
        ):
            raise ProviderEvidenceError(f"{model} Pi cost receipt mismatch")

    return {
        "schema": "yunwu-runtime-probes-validation/1",
        "models": list(EXPERIMENT_MODELS),
        "direct_probes": len(tracks),
        "pi_probes": len(tracks),
        "pi_version": "0.73.1",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rate_card")
    parser.add_argument("--runtime-probes")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)
    report = {"rate_card": validate_rate_card(args.rate_card)}
    if args.runtime_probes:
        report["runtime_probes"] = validate_runtime_probes(
            args.runtime_probes, repo_root=args.repo_root
        )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
