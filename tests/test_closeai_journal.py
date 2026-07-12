from __future__ import annotations

import copy
import io
import hashlib
import json
import multiprocessing
import pathlib
import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor

import pytest

import skillrace.closeai as closeai
from skillrace.io_utils import canonical_json_bytes, canonical_json_hash


class FakeResponse(io.BytesIO):
    def __init__(self, value, *, request_id="request-123", status=200):
        super().__init__(json.dumps(value).encode("utf-8"))
        self.headers = {"x-request-id": request_id}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


def _response(*, content="provider output must stay private", response_id="chatcmpl-1"):
    return {
        "id": response_id,
        "model": "qwen3.6-flash",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
        },
    }


def _records(path: pathlib.Path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def _receipts(path: pathlib.Path):
    directory = path.with_name(f"{path.name}.events")
    return [json.loads(receipt.read_text()) for receipt in directory.glob("*.json")]


@pytest.fixture
def direct_call(tmp_path, monkeypatch):
    ledger = tmp_path / "cost-ledger.jsonl"
    monkeypatch.setenv("CLOSE_API_KEY", "top-secret-api-key")
    monkeypatch.setenv("SKILLRACE_LEDGER", str(ledger))
    monkeypatch.setattr(closeai.time, "sleep", lambda seconds: None)
    return ledger


def test_success_journals_redacted_intent_and_one_priced_terminal(
    direct_call, monkeypatch
):
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(_response()),
    )
    monkeypatch.setattr(
        closeai,
        "log_usage",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("direct calls must not create a second cost record")
        ),
    )

    result = closeai.chat(
        [
            {"role": "system", "content": "private system message"},
            {"role": "user", "content": "private user prompt"},
        ],
        model="qwen3.6-flash",
        temperature=0.25,
        max_tokens=321,
        reasoning=False,
        tag="generate.propose",
        skill="json-csv",
        retries=1,
    )

    assert result["content"] == "provider output must stay private"
    assert result["usage"]["total_tokens"] == 18
    assert result["cost_usd"] == pytest.approx((11 * 0.144 + 7 * 0.88) / 1e6)

    rows = _records(direct_call)
    assert [row["event"] for row in rows] == ["intent", "terminal"]
    intent, terminal = rows
    assert intent["call_id"] == terminal["call_id"]
    assert intent["retry_ordinal"] == terminal["retry_ordinal"] == 1
    assert intent["status"] == "pending"
    assert terminal["status"] == "success"
    assert intent["model"] == "qwen3.6-flash"
    assert intent["temperature"] == 0.25
    assert intent["max_tokens"] == 321
    assert intent["reasoning"] is False
    assert intent["tag"] == "generate.propose"
    assert intent["skill"] == "json-csv"
    assert intent["tag_sha256"] == hashlib.sha256(b"generate.propose").hexdigest()
    assert intent["skill_sha256"] == hashlib.sha256(b"json-csv").hexdigest()
    messages = [
        {"role": "system", "content": "private system message"},
        {"role": "user", "content": "private user prompt"},
    ]
    payload = {
        "model": "qwen3.6-flash",
        "messages": messages,
        "temperature": 0.25,
        "max_tokens": 321,
        "enable_thinking": False,
    }
    assert intent["messages_sha256"] == canonical_json_hash(messages)
    expected_request = canonical_json_bytes(payload)
    assert intent["request_sha256"] == hashlib.sha256(expected_request).hexdigest()
    assert intent["request_bytes"] == len(expected_request)
    assert terminal["provider_response_id_sha256"] == hashlib.sha256(
        b"chatcmpl-1"
    ).hexdigest()
    assert terminal["provider_request_id_sha256"] == hashlib.sha256(
        b"request-123"
    ).hexdigest()
    assert "provider_response_id" not in terminal
    assert "provider_request_id" not in terminal
    assert terminal["http_status"] == 200
    assert terminal["provider_model"] == "qwen3.6-flash"
    assert terminal["pricing_table_version"] == closeai.PRICING_TABLE_VERSION
    assert terminal["usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }
    assert terminal["cost_usd"] == pytest.approx(result["cost_usd"])
    assert terminal["latency_ms"] >= 0
    assert sum(row.get("cost_usd", 0) > 0 for row in rows) == 1

    raw = direct_call.read_text() + "".join(
        receipt.read_text()
        for receipt in direct_call.with_name(
            f"{direct_call.name}.events"
        ).glob("*.json")
    )
    for secret in (
        "top-secret-api-key",
        "private system message",
        "private user prompt",
        "provider output must stay private",
    ):
        assert secret not in raw


def test_success_returns_redacted_durable_terminal_receipt_identity(
    direct_call, monkeypatch
):
    """RQ3 can bind an artifact to the exact receipt persisted before return."""

    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(_response()),
    )

    result = closeai.chat(
        [{"role": "user", "content": "private prompt"}],
        retries=1,
        operation_id="rq3-base-stable-operation",
    )

    receipt = result["journal_terminal_receipt"]
    call_terminal = result["journal_call_terminal_receipt"]
    assert result["operation_id"] == "rq3-base-stable-operation"
    assert result["provider_model"] == "qwen3.6-flash"
    assert result["provider_response_id_sha256"] == hashlib.sha256(
        b"chatcmpl-1"
    ).hexdigest()
    assert result["provider_request_id_sha256"] == hashlib.sha256(
        b"request-123"
    ).hexdigest()
    assert result["billing_status"] == "known"
    assert receipt["event"] == "terminal"
    assert receipt["status"] == "success"
    assert receipt["operation_id"] == result["operation_id"]
    assert result["journal_terminal_event_id"] == receipt["event_id"]
    assert result["journal_terminal_receipt_sha256"] == canonical_json_hash(receipt)
    assert call_terminal["event"] == "call_terminal"
    assert call_terminal["status"] == "success"
    assert call_terminal["operation_id"] == result["operation_id"]
    assert call_terminal["last_retry_ordinal"] == receipt["retry_ordinal"]
    assert result["journal_call_terminal_event_id"] == call_terminal["event_id"]
    assert result["journal_call_terminal_receipt_sha256"] == canonical_json_hash(
        call_terminal
    )

    durable = {
        value["event_id"]: value for value in _receipts(direct_call)
    }
    assert durable[receipt["event_id"]] == receipt
    assert durable[call_terminal["event_id"]] == call_terminal
    serialized = json.dumps(result, sort_keys=True)
    assert "chatcmpl-1" not in serialized
    assert "request-123" not in serialized
    assert "private prompt" not in serialized


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("model", "wrong-model", "model"),
        ("usage", None, "usage"),
        ("cost_usd", None, "cost"),
        ("billing_status", "unknown", "billing status"),
        ("operation_id", "wrong-operation", "operation identity"),
        ("journal_terminal_receipt", None, "terminal receipt"),
        ("journal_call_terminal_receipt", None, "call terminal receipt"),
    ],
)
def test_chat_result_validation_fails_closed_on_incomplete_provenance(
    direct_call, monkeypatch, field, replacement, message
):
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(_response()),
    )
    operation_id = f"rq3-validation-{field.replace('_', '-')}"
    result = closeai.chat(
        [{"role": "user", "content": "private"}],
        retries=1,
        operation_id=operation_id,
    )
    malformed = copy.deepcopy(result)
    malformed[field] = replacement

    with pytest.raises(closeai.ResponseSchemaError, match=message):
        closeai.validate_chat_result(
            malformed,
            expected_model="qwen3.6-flash",
            expected_operation_id=operation_id,
        )


def test_retry_journals_error_before_next_intent(direct_call, monkeypatch):
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "rate limited response may contain secrets",
                {"x-request-id": "failed-request"},
                None,
            )
        return FakeResponse(
            _response(response_id="chatcmpl-retry"), request_id="successful-request"
        )

    monkeypatch.setattr(closeai.urllib.request, "urlopen", fake_urlopen)

    result = closeai.chat(
        [{"role": "user", "content": "never journal this"}], retries=3
    )

    assert result["content"] == "provider output must stay private"
    rows = _records(direct_call)
    assert [(row["event"], row["status"], row["retry_ordinal"]) for row in rows] == [
        ("intent", "pending", 1),
        ("terminal", "error", 1),
        ("intent", "pending", 2),
        ("terminal", "success", 2),
    ]
    assert rows[1]["error_class"] == "HTTPError"
    assert rows[1]["http_status"] == 429
    assert rows[1]["provider_request_id_sha256"] == hashlib.sha256(
        b"failed-request"
    ).hexdigest()
    assert "rate limited" not in direct_call.read_text()


def test_retry_exhaustion_is_bounded_and_every_attempt_is_terminal(
    direct_call, monkeypatch
):
    calls = 0
    sleeps = []

    def fail(request, timeout):
        nonlocal calls
        calls += 1
        raise TimeoutError("provider timeout with private diagnostics")

    monkeypatch.setattr(closeai.urllib.request, "urlopen", fail)
    monkeypatch.setattr(closeai.time, "sleep", sleeps.append)

    with pytest.raises(RuntimeError, match="failed after 3 attempts: TimeoutError"):
        closeai.chat([{"role": "user", "content": "private"}], retries=3)

    assert calls == 3
    assert sleeps == [2, 4]
    rows = _records(direct_call)
    assert len(rows) == 6
    assert [row["retry_ordinal"] for row in rows] == [1, 1, 2, 2, 3, 3]
    assert [row["status"] for row in rows[1::2]] == ["error", "error", "error"]
    assert "private diagnostics" not in direct_call.read_text()


@pytest.mark.parametrize("retries", [0, -1, 11, True])
def test_retry_bound_is_validated_before_provider_call(
    direct_call, monkeypatch, retries
):
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("provider must not be called")
        ),
    )
    with pytest.raises(ValueError, match="retries"):
        closeai.chat([{"role": "user", "content": "private"}], retries=retries)
    assert not direct_call.exists()


@pytest.mark.parametrize(
    "request_settings",
    [
        {"temperature": float("nan")},
        {"temperature": -0.1},
        {"temperature": 2.1},
        {"max_tokens": 0},
        {"max_tokens": True},
        {"reasoning": "yes"},
    ],
)
def test_request_parameters_are_bounded_before_provider_or_journal(
    direct_call, monkeypatch, request_settings
):
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("provider must not be called")
        ),
    )

    with pytest.raises(ValueError, match="request parameters"):
        closeai.chat(
            [{"role": "user", "content": "private"}],
            retries=1,
            **request_settings,
        )

    assert not direct_call.exists()


@pytest.mark.parametrize(
    ("malformed", "billing_known"),
    [
        (
            {
                "id": "malformed-response-id",
                "model": "qwen3.6-flash",
                "choices": [],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
            True,
        ),
        (
            {
                "id": "malformed-response-id",
                "model": "qwen3.6-flash",
                "choices": [{"message": {"content": "hello"}}],
                "usage": {"prompt_tokens": "one", "completion_tokens": 1},
            },
            False,
        ),
    ],
)
def test_malformed_provider_response_is_a_journaled_attempt_error(
    direct_call, monkeypatch, malformed, billing_known
):
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(malformed),
    )

    with pytest.raises(RuntimeError, match="ResponseSchemaError"):
        closeai.chat([{"role": "user", "content": "private"}], retries=1)

    rows = _records(direct_call)
    assert [row["status"] for row in rows] == ["pending", "error"]
    assert rows[-1]["error_class"] == "ResponseSchemaError"
    assert rows[-1]["provider_response_id_sha256"] == hashlib.sha256(
        b"malformed-response-id"
    ).hexdigest()
    if billing_known:
        assert rows[-1]["billing_status"] == "known"
        assert rows[-1]["usage"] == {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        }
        assert rows[-1]["cost_usd"] == pytest.approx((0.144 + 0.88) / 1e6)
    else:
        assert rows[-1]["billing_status"] == "unknown"
        assert rows[-1]["usage"] is None
        assert rows[-1]["cost_usd"] is None


@pytest.mark.parametrize(
    ("response", "http_status"),
    [
        (_response(), 300),
        (_response(response_id="x" * 257), 200),
        ({**_response(), "model": "different-model"}, 200),
        (
            {
                **_response(),
                "choices": [{"message": {"role": "assistant", "content": None}}],
            },
            200,
        ),
        (
            {
                **_response(),
                "usage": {
                    "prompt_tokens": -1,
                    "completion_tokens": 7,
                    "total_tokens": 6,
                },
            },
            200,
        ),
        (
            {
                **_response(),
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 999,
                },
            },
            200,
        ),
    ],
)
def test_strict_response_contract_rejects_unsafe_or_inconsistent_values(
    direct_call, monkeypatch, response, http_status
):
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(response, status=http_status),
    )

    with pytest.raises(RuntimeError, match="ResponseSchemaError"):
        closeai.chat([{"role": "user", "content": "private"}], retries=1)

    terminal = _records(direct_call)[-1]
    assert terminal["status"] == "error"
    assert terminal["error_class"] == "ResponseSchemaError"
    if response.get("model") == "different-model":
        assert terminal["usage"]["total_tokens"] == 18
        assert terminal["billing_status"] == "unknown"
        assert terminal["cost_usd"] is None


def test_explicit_bounded_provider_model_alias_is_accepted(direct_call, monkeypatch):
    alias = "closeai/qwen3.6-flash"
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse({**_response(), "model": alias}),
    )

    result = closeai.chat(
        [{"role": "user", "content": "private"}],
        retries=1,
        accepted_model_aliases=(alias,),
    )

    assert result["content"] == "provider output must stay private"
    assert _records(direct_call)[-1]["provider_model"] == alias


@pytest.mark.parametrize(
    ("response_id", "request_id"),
    [
        ("unsafe response id", "request-123"),
        ("chatcmpl-1", "top-secret-api-key private user prompt"),
    ],
)
def test_unsafe_provider_identifiers_are_rejected_and_never_logged(
    direct_call, monkeypatch, response_id, request_id
):
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(
            _response(response_id=response_id), request_id=request_id
        ),
    )

    with pytest.raises(RuntimeError, match="ResponseSchemaError") as raised:
        closeai.chat(
            [{"role": "user", "content": "private user prompt"}], retries=1
        )

    assert raised.value.__cause__ is None
    raw = direct_call.read_text() + "".join(
        receipt.read_text()
        for receipt in direct_call.with_name(
            f"{direct_call.name}.events"
        ).glob("*.json")
    )
    assert "unsafe response id" not in raw
    assert "top-secret-api-key" not in raw
    assert "private user prompt" not in raw
    terminal = next(row for row in _records(direct_call) if row["event"] == "terminal")
    if response_id == "unsafe response id":
        assert terminal["billing_status"] == "known"
        assert terminal["usage"]["total_tokens"] == 18
        assert terminal["cost_usd"] is not None
    else:
        assert terminal["billing_status"] == "unknown"
        assert terminal["cost_usd"] is None
    assert terminal["http_status"] == 200


def test_provider_exception_is_sanitized_without_a_sensitive_cause(
    direct_call, monkeypatch
):
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: (_ for _ in ()).throw(
            RuntimeError(
                "top-secret-api-key private user prompt private provider response"
            )
        ),
    )

    with pytest.raises(RuntimeError, match="failed after 1 attempts: ProviderError") as raised:
        closeai.chat(
            [{"role": "user", "content": "private user prompt"}], retries=1
        )

    assert raised.value.__cause__ is None
    raw = direct_call.read_text() + "".join(
        receipt.read_text()
        for receipt in direct_call.with_name(
            f"{direct_call.name}.events"
        ).glob("*.json")
    )
    assert "top-secret-api-key" not in raw
    assert "private user prompt" not in raw
    assert "private provider response" not in raw


@pytest.mark.parametrize(
    "metadata",
    [
        {"tag": "private user prompt"},
        {"skill": "private user prompt"},
    ],
)
def test_untrusted_journal_metadata_is_rejected_before_provider_or_disk(
    direct_call, monkeypatch, metadata
):
    provider_called = False

    def fake_urlopen(request, timeout):
        nonlocal provider_called
        provider_called = True
        return FakeResponse(_response())

    monkeypatch.setattr(closeai.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ValueError, match="metadata"):
        closeai.chat(
            [{"role": "user", "content": "private user prompt"}],
            retries=1,
            **metadata,
        )

    assert provider_called is False
    assert not direct_call.exists()


def test_invalid_error_http_status_cannot_become_logged_response_text(
    direct_call, monkeypatch
):
    class SensitiveStatusError(RuntimeError):
        code = "private provider response"

    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: (_ for _ in ()).throw(SensitiveStatusError()),
    )

    with pytest.raises(RuntimeError, match="ProviderError"):
        closeai.chat([{"role": "user", "content": "private"}], retries=1)

    terminal = next(row for row in _records(direct_call) if row["event"] == "terminal")
    assert terminal["http_status"] is None
    assert terminal["error_class"] == "ProviderError"
    assert "private provider response" not in direct_call.read_text()


def test_hostile_header_accessor_still_gets_a_redacted_terminal_record(
    direct_call, monkeypatch
):
    class HostileHeaders:
        def get(self, name):
            error = RuntimeError("private provider response")
            error.headers = self
            raise error

    response = FakeResponse(_response())
    response.headers = HostileHeaders()
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: response,
    )

    with pytest.raises(RuntimeError, match="ResponseSchemaError") as raised:
        closeai.chat([{"role": "user", "content": "private"}], retries=1)

    assert raised.value.__cause__ is None
    rows = _records(direct_call)
    assert [row["event"] for row in rows] == ["intent", "terminal"]
    assert rows[-1]["error_class"] == "ResponseSchemaError"
    assert "private provider response" not in direct_call.read_text()


def test_hostile_exception_attributes_cannot_skip_the_terminal_record(
    direct_call, monkeypatch
):
    class HostileProviderError(RuntimeError):
        @property
        def code(self):
            raise RuntimeError("private provider response from code accessor")

        @property
        def headers(self):
            raise RuntimeError("private provider response from headers accessor")

    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: (_ for _ in ()).throw(HostileProviderError()),
    )

    with pytest.raises(RuntimeError, match="ProviderError") as raised:
        closeai.chat([{"role": "user", "content": "private"}], retries=1)

    assert raised.value.__cause__ is None
    rows = _records(direct_call)
    assert [row["event"] for row in rows] == ["intent", "terminal"]
    assert rows[-1]["error_class"] == "ProviderError"
    raw = direct_call.read_text()
    assert "private provider response" not in raw


def test_corrupt_durable_state_raises_only_a_sanitized_journal_error(
    direct_call, monkeypatch
):
    receipts = direct_call.with_name(f"{direct_call.name}.events")
    receipts.mkdir()
    (receipts / "corrupt.json").write_text(
        "private provider response and top-secret-api-key",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: (_ for _ in ()).throw(
            AssertionError("provider must not be called")
        ),
    )

    with pytest.raises(closeai.JournalError, match="durable journal state") as raised:
        closeai.chat(
            [{"role": "user", "content": "private user prompt"}],
            retries=1,
            operation_id="campaign-corrupt-state",
        )

    assert raised.value.__cause__ is None
    assert "private provider response" not in str(raised.value)
    assert "top-secret-api-key" not in str(raised.value)


def test_unknown_pricing_fails_closed_before_production_provider_call(
    direct_call, monkeypatch
):
    provider_called = False

    def fake_urlopen(request, timeout):
        nonlocal provider_called
        provider_called = True
        return FakeResponse({**_response(), "model": "unpriced-model"})

    monkeypatch.setattr(closeai.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(closeai.UnknownPricingError, match="pricing is unavailable") as raised:
        closeai.chat(
            [{"role": "user", "content": "private"}],
            model="unpriced-model",
            retries=1,
        )

    assert raised.value.__cause__ is None
    assert provider_called is False
    assert not direct_call.exists()


def test_unknown_development_pricing_is_explicitly_marked_not_zero(
    direct_call, monkeypatch
):
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(
            {**_response(), "model": "unpriced-model"}
        ),
    )

    result = closeai.chat(
        [{"role": "user", "content": "private"}],
        model="unpriced-model",
        retries=1,
        journal_mode="development",
    )

    terminal = _records(direct_call)[-1]
    assert result["cost_usd"] is None
    assert terminal["billing_status"] == "unknown"
    assert terminal["cost_usd"] is None
    assert terminal["provider_model"] == "unpriced-model"
    assert terminal["pricing_table_version"] == closeai.PRICING_TABLE_VERSION


def test_stable_operation_id_deduplicates_a_durable_terminal_call(
    direct_call, monkeypatch
):
    provider_calls = 0

    def fake_urlopen(request, timeout):
        nonlocal provider_calls
        provider_calls += 1
        return FakeResponse(_response())

    monkeypatch.setattr(closeai.urllib.request, "urlopen", fake_urlopen)
    messages = [{"role": "user", "content": "private"}]

    first = closeai.chat(messages, retries=1, operation_id="campaign-e0001-a01")
    with pytest.raises(closeai.DuplicateOperationError, match="already terminal"):
        closeai.chat(messages, retries=1, operation_id="campaign-e0001-a01")

    assert first["content"] == "provider output must stay private"
    assert provider_calls == 1
    rows = _records(direct_call)
    assert [row["event"] for row in rows] == ["intent", "terminal"]
    assert any(row["event"] == "call_terminal" for row in _receipts(direct_call))
    assert sum((row.get("cost_usd") or 0) > 0 for row in rows) == 1


def test_concurrent_different_requests_cannot_share_one_operation_claim(
    direct_call, monkeypatch
):
    provider_calls = 0
    provider_lock = threading.Lock()
    entry_barrier = threading.Barrier(2)
    real_operation_records = closeai._operation_records

    def synchronized_operation_records(path, operation_id):
        records = real_operation_records(path, operation_id)
        entry_barrier.wait(timeout=10)
        return records

    def fake_urlopen(request, timeout):
        nonlocal provider_calls
        with provider_lock:
            provider_calls += 1
        return FakeResponse(_response())

    monkeypatch.setattr(closeai, "_operation_records", synchronized_operation_records)
    monkeypatch.setattr(closeai.urllib.request, "urlopen", fake_urlopen)

    def invoke(prompt):
        try:
            closeai.chat(
                [{"role": "user", "content": prompt}],
                retries=1,
                operation_id="one-shared-operation",
            )
            return "success"
        except (closeai.JournalError, closeai.OperationConflictError):
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(invoke, ["private-a", "private-b"]))

    assert sorted(outcomes) == ["conflict", "success"]
    assert provider_calls == 1


def test_stale_unmatched_intent_is_outcome_unknown_and_never_replayed(
    direct_call, monkeypatch
):
    provider_calls = 0

    def crash_after_intent(request, timeout):
        nonlocal provider_calls
        provider_calls += 1
        raise KeyboardInterrupt("simulated process death")

    monkeypatch.setattr(closeai.urllib.request, "urlopen", crash_after_intent)
    messages = [{"role": "user", "content": "private"}]
    with pytest.raises(KeyboardInterrupt, match="simulated process death"):
        closeai.chat(messages, retries=1, operation_id="campaign-e0002-a01")

    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: (_ for _ in ()).throw(
            AssertionError("stale operation must not be replayed")
        ),
    )
    with pytest.raises(closeai.OutcomeUnknownError, match="outcome is unknown"):
        closeai.chat(
            messages,
            retries=1,
            operation_id="campaign-e0002-a01",
            stale_intent_seconds=0,
        )

    assert provider_calls == 1
    rows = _records(direct_call)
    assert [row["status"] for row in rows] == ["pending", "outcome_unknown"]
    assert any(
        row["event"] == "call_terminal" and row["status"] == "outcome_unknown"
        for row in _receipts(direct_call)
    )
    assert all(row.get("cost_usd") in (None, 0) for row in rows)


def test_invalid_stale_intent_timestamp_is_sanitized(direct_call, monkeypatch):
    messages = [{"role": "user", "content": "private"}]
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        closeai.chat(messages, retries=1, operation_id="campaign-bad-timestamp")

    intent_receipt = next(
        receipt
        for receipt in direct_call.with_name(
            f"{direct_call.name}.events"
        ).glob("*.json")
        if json.loads(receipt.read_text())["event"] == "intent"
    )
    intent = json.loads(intent_receipt.read_text())
    intent["ts"] = "2026-07-11T00:00:00"
    intent_receipt.write_text(json.dumps(intent), encoding="utf-8")

    with pytest.raises(closeai.JournalError, match="invalid timestamp") as raised:
        closeai.chat(
            messages,
            retries=1,
            operation_id="campaign-bad-timestamp",
            stale_intent_seconds=0,
        )

    assert raised.value.__cause__ is None


def test_recovery_preserves_remaining_journaled_retry_backoff(
    direct_call, monkeypatch
):
    messages = [{"role": "user", "content": "private"}]
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: (_ for _ in ()).throw(TimeoutError("retry")),
    )
    monkeypatch.setattr(
        closeai.time,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt("crash in backoff")),
    )
    with pytest.raises(KeyboardInterrupt, match="crash in backoff"):
        closeai.chat(
            messages,
            retries=2,
            operation_id="campaign-backoff-recovery",
        )

    resumed_sleeps = []
    monkeypatch.setattr(closeai.time, "sleep", resumed_sleeps.append)
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(_response()),
    )

    closeai.chat(
        messages,
        retries=2,
        operation_id="campaign-backoff-recovery",
    )

    assert len(resumed_sleeps) == 1
    assert 0 < resumed_sleeps[0] <= 2


def test_exact_request_bytes_and_bounded_retry_policy_are_frozen_and_journaled(
    direct_call, monkeypatch
):
    messages = [{"role": "user", "content": "original private prompt"}]
    sent_bodies = []
    observed_timeouts = []

    def retry_once(request, timeout):
        sent_bodies.append(bytes(request.data))
        observed_timeouts.append(timeout)
        if len(sent_bodies) == 1:
            messages[0]["content"] = "mutated after request freeze"
            raise TimeoutError("retry")
        return FakeResponse(_response())

    monkeypatch.setattr(closeai.urllib.request, "urlopen", retry_once)

    closeai.chat(messages, retries=2, timeout_seconds=17)

    assert sent_bodies[0] == sent_bodies[1]
    assert b"original private prompt" in sent_bodies[0]
    assert b"mutated after request freeze" not in sent_bodies[0]
    assert observed_timeouts == [17, 17]
    rows = _records(direct_call)
    intents = [row for row in rows if row["event"] == "intent"]
    assert len(intents) == 2
    assert all(
        row["request_sha256"] == hashlib.sha256(sent_bodies[0]).hexdigest()
        for row in intents
    )
    assert all(row["request_bytes"] == len(sent_bodies[0]) for row in intents)
    assert all(row["timeout_seconds"] == 17 for row in intents)
    assert all(row["retry_limit"] == 2 for row in intents)
    assert all(row["retry_backoff_seconds"] == [2] for row in intents)
    assert all(row["retry_policy_version"] == "bounded-linear-v1" for row in intents)


def test_message_hash_is_derived_from_the_frozen_request_not_live_messages(
    direct_call, monkeypatch
):
    messages = [{"role": "user", "content": "original"}]
    original_messages = json.loads(json.dumps(messages))
    real_canonical_bytes = closeai.canonical_json_bytes
    mutated = False

    def mutate_after_request_freeze(value):
        nonlocal mutated
        encoded = real_canonical_bytes(value)
        if not mutated and isinstance(value, dict) and "messages" in value:
            messages[0]["content"] = "changed between traversals"
            mutated = True
        return encoded

    monkeypatch.setattr(closeai, "canonical_json_bytes", mutate_after_request_freeze)
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(_response()),
    )

    closeai.chat(messages, retries=1)

    intent = next(row for row in _records(direct_call) if row["event"] == "intent")
    assert intent["messages_sha256"] == canonical_json_hash(original_messages)


def test_ledger_path_is_resolved_at_call_time_and_explicit_path_wins(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CLOSE_API_KEY", "secret")
    monkeypatch.setattr(closeai.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(_response()),
    )
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    campaign = tmp_path / "campaign" / "model-calls.jsonl"

    monkeypatch.setenv("SKILLRACE_LEDGER", str(first))
    closeai.chat([{"role": "user", "content": "first"}], retries=1)
    monkeypatch.setenv("SKILLRACE_LEDGER", str(second))
    closeai.chat([{"role": "user", "content": "second"}], retries=1)
    closeai.chat(
        [{"role": "user", "content": "campaign"}],
        retries=1,
        ledger_path=campaign,
    )

    assert len(_records(first)) == 2
    assert len(_records(second)) == 2
    assert len(_records(campaign)) == 2


def test_one_absolute_ledger_path_is_frozen_for_the_whole_call(
    tmp_path, monkeypatch
):
    first_directory = tmp_path / "first-cwd"
    second_directory = tmp_path / "second-cwd"
    first_directory.mkdir()
    second_directory.mkdir()
    monkeypatch.chdir(first_directory)
    monkeypatch.setenv("CLOSE_API_KEY", "secret")
    monkeypatch.setenv("SKILLRACE_LEDGER", "relative-ledger.jsonl")

    def move_environment_during_provider_call(request, timeout):
        monkeypatch.chdir(second_directory)
        monkeypatch.setenv("SKILLRACE_LEDGER", "changed-ledger.jsonl")
        return FakeResponse(_response())

    monkeypatch.setattr(closeai.urllib.request, "urlopen", move_environment_during_provider_call)

    closeai.chat([{"role": "user", "content": "private"}], retries=1)

    assert len(_records(first_directory / "relative-ledger.jsonl")) == 2
    assert not (second_directory / "changed-ledger.jsonl").exists()


def test_production_fails_before_provider_when_intent_cannot_be_persisted(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CLOSE_API_KEY", "secret")
    unusable = tmp_path / "is-a-directory"
    unusable.mkdir()
    provider_called = False

    def fake_urlopen(request, timeout):
        nonlocal provider_called
        provider_called = True
        return FakeResponse(_response())

    monkeypatch.setattr(closeai.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(closeai.JournalError, match="persist model-call journal"):
        closeai.chat(
            [{"role": "user", "content": "private"}],
            retries=1,
            ledger_path=unusable,
        )
    assert provider_called is False


def test_ledger_resolution_is_sanitized_fail_closed_or_explicitly_fail_open(
    monkeypatch
):
    class UnresolvablePath:
        def __fspath__(self):
            raise RuntimeError("top-secret-api-key private user prompt")

    provider_calls = 0

    def fake_urlopen(request, timeout):
        nonlocal provider_calls
        provider_calls += 1
        return FakeResponse(_response())

    monkeypatch.setenv("CLOSE_API_KEY", "secret")
    monkeypatch.setattr(closeai.urllib.request, "urlopen", fake_urlopen)
    path = UnresolvablePath()

    with pytest.raises(closeai.JournalError, match="resolve model-call journal") as raised:
        closeai.chat(
            [{"role": "user", "content": "private"}],
            retries=1,
            ledger_path=path,
        )
    assert raised.value.__cause__ is None
    assert provider_calls == 0

    result = closeai.chat(
        [{"role": "user", "content": "private"}],
        retries=1,
        ledger_path=path,
        journal_mode="development",
    )
    legacy_cost = closeai.log_usage(
        "run.agent",
        "qwen3.6-flash",
        4,
        2,
        "demo",
        ledger_path=path,
    )

    assert result["content"] == "provider output must stay private"
    assert provider_calls == 1
    assert legacy_cost == pytest.approx((4 * 0.144 + 2 * 0.88) / 1e6)


def test_explicit_development_mode_is_fail_open_and_legacy_usage_stays_safe(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CLOSE_API_KEY", "secret")
    unusable = tmp_path / "is-a-directory"
    unusable.mkdir()
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(_response()),
    )

    result = closeai.chat(
        [{"role": "user", "content": "private"}],
        retries=1,
        ledger_path=unusable,
        journal_mode="development",
    )
    legacy_cost = closeai.log_usage(
        "run.agent", "qwen3.6-flash", 4, 2, "demo", ledger_path=unusable
    )

    assert result["content"] == "provider output must stay private"
    assert legacy_cost == pytest.approx((4 * 0.144 + 2 * 0.88) / 1e6)


def test_legacy_development_usage_drops_unsafe_metadata_without_leaking(
    tmp_path
):
    ledger = tmp_path / "legacy.jsonl"

    cost = closeai.log_usage(
        "private user prompt",
        "qwen3.6-flash",
        4,
        2,
        "demo",
        ledger_path=ledger,
    )

    assert cost == pytest.approx((4 * 0.144 + 2 * 0.88) / 1e6)
    assert not ledger.exists()


def test_identical_legacy_usage_events_in_same_clock_tick_are_not_deduplicated(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "legacy-concurrent.jsonl"
    monkeypatch.setattr(closeai, "_now", lambda: "2026-07-11T00:00:00+00:00")

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(
            executor.map(
                lambda _: closeai.log_usage(
                    "run.agent",
                    "qwen3.6-flash",
                    4,
                    2,
                    "demo",
                    ledger_path=ledger,
                ),
                range(2),
            )
        )

    rows = _records(ledger)
    assert len(rows) == 2
    assert len({row["operation_id"] for row in rows}) == 2


def _write_legacy_rows(path: str, process_number: int, count: int):
    for index in range(count):
        closeai.log_usage(
            f"process-{process_number}-{index}",
            "qwen3.6-flash",
            index,
            index + 1,
            ledger_path=path,
        )


def test_journal_appends_are_process_safe(tmp_path):
    ledger = tmp_path / "concurrent.jsonl"
    context = multiprocessing.get_context("fork")
    processes = [
        context.Process(target=_write_legacy_rows, args=(str(ledger), number, 8))
        for number in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)

    assert all(process.exitcode == 0 for process in processes)
    rows = _records(ledger)
    assert len(rows) == 32
    assert len({row["tag"] for row in rows}) == 32
    assert all(row["event"] == "external_usage" for row in rows)


def test_event_receipt_recovers_after_atomic_materialization_failure(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "journal.jsonl"
    first = {
        "schema": "skillrace-model-call-journal/2",
        "event_id": "event-1",
        "event": "external_usage",
        "status": "success",
    }
    second = {
        "schema": "skillrace-model-call-journal/2",
        "event_id": "event-2",
        "event": "external_usage",
        "status": "success",
    }
    closeai._append_record(ledger, first)
    real_replace = closeai.os.replace

    def fail_ledger_replace(source, destination):
        if pathlib.Path(destination) == ledger:
            raise OSError("simulated crash before ledger replacement")
        return real_replace(source, destination)

    monkeypatch.setattr(closeai.os, "replace", fail_ledger_replace)
    with pytest.raises(OSError, match="simulated crash"):
        closeai._append_record(ledger, second)

    assert _records(ledger) == [first]
    monkeypatch.setattr(closeai.os, "replace", real_replace)
    closeai._append_record(ledger, second)

    assert _records(ledger) == [first, second]


def test_file_and_new_directory_fsyncs_happen_while_journal_lock_is_held(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "new-parent" / "journal.jsonl"
    lock_held = False
    fsync_lock_states = []
    fsynced_directories = []
    real_flock = closeai.fcntl.flock
    real_fsync = closeai.os.fsync
    real_fsync_directory = closeai._fsync_directory

    def recording_flock(descriptor, operation):
        nonlocal lock_held
        result = real_flock(descriptor, operation)
        if operation == closeai.fcntl.LOCK_EX:
            lock_held = True
        elif operation == closeai.fcntl.LOCK_UN:
            lock_held = False
        return result

    def recording_fsync(descriptor):
        fsync_lock_states.append(lock_held)
        return real_fsync(descriptor)

    def recording_fsync_directory(path):
        fsynced_directories.append(pathlib.Path(path))
        return real_fsync_directory(path)

    monkeypatch.setattr(closeai.fcntl, "flock", recording_flock)
    monkeypatch.setattr(closeai.os, "fsync", recording_fsync)
    monkeypatch.setattr(closeai, "_fsync_directory", recording_fsync_directory)

    closeai._append_record(
        ledger,
        {
            "schema": "skillrace-model-call-journal/2",
            "event_id": "event-1",
            "event": "intent",
            "status": "pending",
        },
    )

    assert fsync_lock_states
    assert all(fsync_lock_states)
    assert tmp_path in fsynced_directories
    assert tmp_path / "new-parent" in fsynced_directories


def test_direct_call_journal_is_thread_safe(direct_call, monkeypatch):
    counter = 0
    lock = threading.Lock()

    def fake_urlopen(request, timeout):
        nonlocal counter
        with lock:
            counter += 1
            current = counter
        return FakeResponse(
            _response(response_id=f"response-{current}"),
            request_id=f"request-{current}",
        )

    monkeypatch.setattr(closeai.urllib.request, "urlopen", fake_urlopen)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda index: closeai.chat(
                    [{"role": "user", "content": f"secret-{index}"}], retries=1
                ),
                range(24),
            )
        )

    assert len(results) == 24
    rows = _records(direct_call)
    assert len(rows) == 48
    call_ids = {row["call_id"] for row in rows}
    assert len(call_ids) == 24
    assert all(
        sum(row["call_id"] == call_id for row in rows) == 2 for call_id in call_ids
    )
