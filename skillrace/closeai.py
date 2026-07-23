"""Minimal Yunwu (OpenAI-compatible) chat client — stdlib only.

Used by SkillRACE's *model-driven* steps (generation, and later the judgment steps).
These go DIRECT to the provider (not through pi), so temperature is controllable
(D-PI-1). The agent-under-test is the only thing that runs via pi.
"""
from __future__ import annotations
import datetime
import fcntl
import hashlib
import json
import math
import multiprocessing
import os
import pathlib
import tempfile
import time
import urllib.error
import urllib.request
import uuid

from .io_utils import canonical_json_bytes, canonical_json_hash
from .model_policy import (
    BILLING_CURRENCY,
    DEFAULT_DEVELOPMENT_MODEL,
    DEVELOPMENT_CANDIDATE_MODELS,
    EXPERIMENT_MODELS,
    GLM_MODELS,
    PROVIDER_CREDIT_RATES,
    QWEN_HYBRID_THINKING_MODELS,
    RATE_CARD_VERSION,
    RESPONSES_MODELS,
    STREAM_ONLY_MODELS,
    has_known_provider_credit_rate,
    provider_credits_for_known_model,
    rate_card_version_for_model,
)

YUNWU_URL = "https://yunwu.ai/v1/chat/completions"
YUNWU_RESPONSES_URL = "https://yunwu.ai/v1/responses"
YUNWU_API_KEY_ENV = "yunwu_key"
YUNWU_API_KEY_COMPAT_ENV = "yumwu_key"

DEFAULT_LEDGER_PATH = "~/.skillrace/cost_ledger.jsonl"
MAX_RETRIES = 10
PRICING_TABLE_VERSION = RATE_CARD_VERSION
_DISABLED_LEDGER = object()
_ORIGINAL_URLOPEN = urllib.request.urlopen
# ``spawn`` avoids forking a multithreaded generator while it holds provider/journal
# locks. Tests may select ``fork`` for a deterministic local transport double.
_HTTP_PROCESS_CONTEXT = "spawn"


class _NonProductionChatFixture:
    """Explicit marker for a fake chat transport used only by tests/fixtures."""

    __slots__ = ("_function",)

    def __init__(self, function):
        self._function = function

    def __call__(self, *args, **kwargs):
        value = self._function(*args, **kwargs)
        return _normalize_nonproduction_fixture_response(value, args, kwargs)


def nonproduction_chat_fixture(function):
    """Mark an injected chat callable as an intentional non-production fixture.

    RQ3 entry points reject arbitrary replacement callables.  Tests that need to
    avoid a paid provider call must cross this conspicuous boundary explicitly.
    """

    if not callable(function):
        raise TypeError("non-production chat fixture must be callable")
    return _NonProductionChatFixture(function)


def is_nonproduction_chat_fixture(value):
    return isinstance(value, _NonProductionChatFixture)


class JournalError(RuntimeError):
    """A model call could not be durably journaled."""


class ResponseSchemaError(ValueError):
    """The provider returned JSON that is not a usable chat completion."""


class UnknownPricingError(RuntimeError):
    """Production accounting cannot price the requested model."""


class DuplicateOperationError(RuntimeError):
    """A durable operation outcome already exists."""


class OperationInProgressError(RuntimeError):
    """Another caller owns a fresh durable operation intent."""


class OutcomeUnknownError(RuntimeError):
    """A stale intent may already have reached the provider."""


class ProviderWallClockTimeout(RuntimeError):
    """The isolated transport exceeded the whole-call deadline."""


class OperationConflictError(RuntimeError):
    """An operation identifier was reused for a different request."""


def yunwu_api_key() -> str | None:
    """Return the canonical Yunwu key, accepting the early documented typo too."""

    return os.environ.get(YUNWU_API_KEY_ENV) or os.environ.get(
        YUNWU_API_KEY_COMPAT_ENV
    )


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="microseconds"
    )


def _resolve_ledger_path(ledger_path=None):
    """Resolve the destination now, so one process can run multiple campaigns."""
    configured = ledger_path
    if configured is None:
        configured = os.environ.get("SKILLRACE_LEDGER", DEFAULT_LEDGER_PATH)
    return pathlib.Path(os.path.expanduser(os.fspath(configured))).absolute()


def _fsync_directory(path):
    descriptor = os.open(
        pathlib.Path(path),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_jsonl(path):
    if not pathlib.Path(path).exists():
        return []
    records = []
    with pathlib.Path(path).open("rb") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except Exception as error:
                raise ValueError(
                    f"journal contains an invalid record at line {line_number}"
                ) from error
            if not isinstance(value, dict):
                raise ValueError(
                    f"journal record at line {line_number} is not an object"
                )
            records.append(value)
    return records


def _write_receipt(receipts, record):
    event_id = record["event_id"]
    destination = receipts / f"{event_id}.json"
    encoded = canonical_json_bytes(record) + b"\n"
    if destination.exists():
        existing = json.loads(destination.read_bytes())
        volatile = {"ts", "latency_ms"}
        if {
            key: value for key, value in existing.items() if key not in volatile
        } != {
            key: value for key, value in record.items() if key not in volatile
        }:
            raise ValueError("journal event identifier collision")
        return False
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".event-",
        suffix=".tmp",
        dir=receipts,
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            existing = json.loads(destination.read_bytes())
            volatile = {"ts", "latency_ms"}
            if {
                key: value for key, value in existing.items() if key not in volatile
            } != {
                key: value for key, value in record.items() if key not in volatile
            }:
                raise ValueError("journal event identifier collision")
            return False
        _fsync_directory(receipts)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _materialize_ledger(destination, receipts):
    all_receipt_records = []
    for path in sorted(receipts.glob("*.json")):
        value = json.loads(path.read_bytes())
        if not isinstance(value, dict) or value.get("event_id") != path.stem:
            raise ValueError("malformed immutable journal receipt")
        all_receipt_records.append(value)
    receipt_ids = {record["event_id"] for record in all_receipt_records}
    legacy_records = [
        record
        for record in _read_jsonl(destination)
        if record.get("event_id") not in receipt_ids
    ]
    event_order = {"intent": 0, "terminal": 1, "call_terminal": 2}
    receipt_records = [
        record
        for record in all_receipt_records
        if record.get("event") != "call_terminal"
    ]
    receipt_records.sort(
        key=lambda record: (
            str(record.get("operation_id", record.get("call_id", ""))),
            1 if record.get("event") == "call_terminal" else 0,
            int(record.get("retry_ordinal", 0)),
            event_order.get(record.get("event"), 3),
            record["event_id"],
        )
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            for record in [*legacy_records, *receipt_records]:
                stream.write(canonical_json_bytes(record) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _append_record(path, record):
    """Persist an immutable event, then atomically rematerialize its JSONL view."""
    destination = pathlib.Path(path).absolute()
    missing_directories = []
    cursor = destination.parent
    while not cursor.exists() and cursor != cursor.parent:
        missing_directories.append(cursor)
        cursor = cursor.parent
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination.with_name(f"{destination.name}.lock")
    receipts = destination.with_name(f"{destination.name}.events")
    value = dict(record)
    if "event_id" not in value:
        value["event_id"] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    event_id = value["event_id"]
    if (
        not isinstance(event_id, str)
        or not event_id
        or len(event_id) > 128
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in event_id)
    ):
        raise ValueError("invalid journal event identifier")
    lock = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for directory in missing_directories:
            _fsync_directory(directory)
            _fsync_directory(directory.parent)
        _fsync_directory(destination.parent)
        receipts.mkdir(exist_ok=True)
        _fsync_directory(destination.parent)
        created = _write_receipt(receipts, value)
        _materialize_ledger(destination, receipts)
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            os.close(lock)
    return created


def _persist(record, *, ledger_path=None, journal_mode="production"):
    if journal_mode not in {"production", "development"}:
        raise ValueError("journal_mode must be 'production' or 'development'")
    if ledger_path is _DISABLED_LEDGER:
        return None
    try:
        return _append_record(_resolve_ledger_path(ledger_path), record)
    except Exception as error:
        if journal_mode == "production":
            raise JournalError(
                "could not persist model-call journal "
                f"({type(error).__name__})"
            ) from None
        return None


def log_usage(
    tag,
    model,
    in_tokens,
    out_tokens,
    skill=None,
    *,
    ledger_path=None,
    journal_mode="development",
    operation_id=None,
):
    """Log usage measured by Pi while preserving its historical fail-open API.

    Direct :func:`chat` calls journal their own success record and intentionally do
    not call this function.  Pi callers cannot journal a pre-provider intent because
    Pi owns the HTTP exchange, so their old accounting hook remains a distinct event.
    """
    pricing_known = has_known_provider_credit_rate(model)
    if not pricing_known and journal_mode == "production":
        raise UnknownPricingError("model pricing is unavailable") from None
    if journal_mode == "production" and model not in EXPERIMENT_MODELS:
        raise UnknownPricingError("model pricing is not frozen for production") from None
    credit_cost = None
    if pricing_known:
        credit_cost = provider_credits_for_known_model(model, in_tokens, out_tokens)
    try:
        _validate_journal_metadata(model, tag, skill)
    except ValueError:
        if journal_mode == "production":
            raise
        return credit_cost if credit_cost is not None else 0.0
    if operation_id is None:
        operation_id = uuid.uuid4().hex
    try:
        _bounded_identifier(operation_id, "external usage operation id")
    except ResponseSchemaError:
        if journal_mode == "production":
            raise ValueError("operation_id must be bounded safe text") from None
        return credit_cost if credit_cost is not None else 0.0
    record = {
        "schema": "skillrace-model-call-journal/2",
        "event": "external_usage",
        "status": "success",
        "ts": _now(),
        "call_id": operation_id,
        "operation_id": operation_id,
        "tag": tag,
        "skill": skill,
        "tag_sha256": hashlib.sha256(tag.encode("utf-8")).hexdigest(),
        "skill_sha256": (
            hashlib.sha256(skill.encode("utf-8")).hexdigest()
            if skill is not None else None
        ),
        "model": model,
        "pricing_table_version": (
            rate_card_version_for_model(model)
            if pricing_known
            else PRICING_TABLE_VERSION
        ),
        "billing_status": "known" if credit_cost is not None else "unknown",
        "billing_currency": BILLING_CURRENCY if credit_cost is not None else None,
        "in": in_tokens,
        "out": out_tokens,
        "cost_provider_credits": credit_cost,
        "price_provider_credits": (
            round(credit_cost, 6) if credit_cost is not None else None
        ),
        "cost_usd": None,
        "price_usd": None,
    }
    record["event_id"] = hashlib.sha256(
        canonical_json_bytes(
            {"operation_id": operation_id, "event": "external_usage"}
        )
    ).hexdigest()
    try:
        resolved_ledger_path = _resolve_ledger_path(ledger_path)
    except Exception:
        if journal_mode == "production":
            raise JournalError("could not resolve model-call journal") from None
        return credit_cost if credit_cost is not None else 0.0
    _persist(
        record,
        ledger_path=resolved_ledger_path,
        journal_mode=journal_mode,
    )
    return credit_cost if credit_cost is not None else 0.0

# Compatibility alias for older internal callers.  Values are Yunwu custom credits
# per 1M input/output tokens, never USD.  New code should import the explicitly named
# table from model_policy.
PRICES = PROVIDER_CREDIT_RATES


def _request_id(headers):
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if getter is not None:
        for name in ("x-request-id", "request-id", "x-openai-request-id"):
            value = getter(name)
            if value:
                return str(value)
    items = getattr(headers, "items", None)
    if items is not None:
        for name, value in items():
            if str(name).lower() in {
                "x-request-id",
                "request-id",
                "x-openai-request-id",
            }:
                return str(value)
    return None


_SAFE_PROVIDER_ID_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._:/"
)


def _bounded_identifier(value, field):
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ResponseSchemaError(f"{field} must be bounded text")
    if any(character not in _SAFE_PROVIDER_ID_CHARACTERS for character in value):
        raise ResponseSchemaError(f"{field} contains unsafe characters")
    return value


def _bounded_model(value, field="response model"):
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ResponseSchemaError(f"{field} must be bounded text")
    if any(character not in _SAFE_PROVIDER_ID_CHARACTERS for character in value):
        raise ResponseSchemaError(f"{field} contains unsafe characters")
    return value


def _validate_journal_metadata(model, tag, skill):
    try:
        _bounded_model(model, "requested model")
        _bounded_identifier(tag, "journal tag")
        if skill is not None:
            _bounded_identifier(skill, "journal skill")
    except ResponseSchemaError:
        raise ValueError("journal metadata must be bounded safe text") from None


def _chat_request_body_and_identity(
    messages, *, model, temperature, max_tokens, reasoning
):
    if model in RESPONSES_MODELS:
        prompt = "\n\n".join(
            str(message.get("content", ""))
            for message in messages
            if isinstance(message, dict)
        )
        payload = {
            "model": model,
            "input": prompt,
            "reasoning": {"effort": "medium", "summary": "detailed"},
        }
        if max_tokens is not None:
            payload["max_output_tokens"] = max_tokens
    else:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
    if model in STREAM_ONLY_MODELS:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
    if model in QWEN_HYBRID_THINKING_MODELS:
        payload["enable_thinking"] = bool(reasoning)
    elif not reasoning:
        payload["enable_thinking"] = False
        # GLM's native OpenAI-compatible contract uses ``thinking.type``;
        # Yunwu does not consistently translate the generic Qwen-style flag.
        if model in GLM_MODELS:
            payload["thinking"] = {"type": "disabled"}
    body = bytes(canonical_json_bytes(payload))
    frozen_payload = json.loads(body)
    return body, {
        "messages_sha256": canonical_json_hash(
            frozen_payload.get("messages", frozen_payload.get("input", ""))
        ),
        "request_sha256": hashlib.sha256(body).hexdigest(),
        "request_bytes": len(body),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "reasoning": bool(reasoning),
    }


def _normalize_responses_body(value):
    """Adapt Yunwu/OpenAI Responses output to SkillRACE's journal schema."""
    if not isinstance(value, dict):
        raise ResponseSchemaError("responses body must be an object")
    output = value.get("output")
    if not isinstance(output, list):
        raise ResponseSchemaError("responses output must be a list")
    summary = []
    content = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "reasoning":
            for part in item.get("summary") or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    summary.append(part["text"])
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    content.append(part["text"])
    usage = value.get("usage") or {}
    prompt = usage.get("input_tokens", 0)
    completion = usage.get("output_tokens", 0)
    if not all(isinstance(x, int) and x >= 0 for x in (prompt, completion)):
        raise ResponseSchemaError("responses usage is invalid")
    return {
        "id": value.get("id"),
        "model": value.get("model"),
        "choices": [{"message": {
            "role": "assistant",
            "content": "".join(content),
            "reasoning_content": "\n\n".join(summary),
        }}],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }


def chat_request_identity(messages, *, model, temperature, max_tokens, reasoning):
    """Return the exact non-secret identity of the provider request bytes."""

    _, identity = _chat_request_body_and_identity(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        reasoning=reasoning,
    )
    return identity


def _aggregate_stream_chunks(chunks):
    """Convert one complete OpenAI SSE stream to the synchronous response shape."""

    response_id = None
    provider_model = None
    role = None
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason = None
    usage = None
    saw_choice = False
    for chunk in chunks:
        if not isinstance(chunk, dict) or chunk.get("error") is not None:
            raise ResponseSchemaError("stream chunk must be a successful object")
        chunk_id = chunk.get("id")
        chunk_model = chunk.get("model")
        if chunk_id is not None:
            _bounded_identifier(chunk_id, "stream response id")
            if response_id is not None and chunk_id != response_id:
                raise ResponseSchemaError("stream response id changed")
            response_id = chunk_id
        if chunk_model is not None:
            _bounded_model(chunk_model, "stream response model")
            if provider_model is not None and chunk_model != provider_model:
                raise ResponseSchemaError("stream response model changed")
            provider_model = chunk_model

        choices = chunk.get("choices")
        if not isinstance(choices, list):
            raise ResponseSchemaError("stream choices must be a list")
        if choices:
            if len(choices) != 1 or not isinstance(choices[0], dict):
                raise ResponseSchemaError("stream must contain exactly one choice")
            choice = choices[0]
            if choice.get("index") != 0:
                raise ResponseSchemaError("stream choice index must be zero")
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                raise ResponseSchemaError("stream choice delta must be an object")
            if delta.get("tool_calls") is not None:
                raise ResponseSchemaError("direct helper stream returned an unexpected tool call")
            delta_role = delta.get("role")
            if delta_role is not None:
                if delta_role != "assistant" or role not in {None, "assistant"}:
                    raise ResponseSchemaError("stream assistant role is invalid")
                role = delta_role
            for field, destination in (
                ("content", content_parts),
                ("reasoning_content", reasoning_parts),
            ):
                value = delta.get(field)
                if value is not None:
                    if not isinstance(value, str):
                        raise ResponseSchemaError(f"stream {field} must be text")
                    destination.append(value)
            current_finish = choice.get("finish_reason")
            if current_finish is not None:
                if (
                    not isinstance(current_finish, str)
                    or not current_finish
                    or finish_reason is not None
                ):
                    raise ResponseSchemaError("stream finish reason is invalid or duplicated")
                finish_reason = current_finish
            saw_choice = True
        chunk_usage = chunk.get("usage")
        if chunk_usage is not None:
            if not isinstance(chunk_usage, dict) or usage is not None:
                raise ResponseSchemaError("stream usage is invalid or duplicated")
            usage = chunk_usage

    if (
        not saw_choice
        or response_id is None
        or provider_model is None
        or finish_reason is None
        or usage is None
    ):
        raise ResponseSchemaError("stream is missing terminal response evidence")
    message = {
        "role": role or "assistant",
        "content": "".join(content_parts),
    }
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    return {
        "id": response_id,
        "model": provider_model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
    }


def _decode_provider_response_body(raw, *, streamed):
    """Decode JSON or a complete, explicitly terminated OpenAI SSE response."""

    if not streamed:
        return json.loads(raw)
    if not isinstance(raw, bytes):
        raise ResponseSchemaError("stream response body must be bytes")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ResponseSchemaError("stream response is not UTF-8") from None
    chunks = []
    saw_done = False
    for line in text.splitlines():
        if not line:
            continue
        if not line.startswith("data:") or saw_done:
            raise ResponseSchemaError("stream contains an invalid SSE field")
        data = line[5:].lstrip(" ")
        if data == "[DONE]":
            saw_done = True
            continue
        if not data:
            raise ResponseSchemaError("stream data event is empty")
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            raise ResponseSchemaError("stream data event is malformed JSON") from None
        chunks.append(chunk)
    if not saw_done:
        raise ResponseSchemaError("stream is missing the DONE event")
    return _aggregate_stream_chunks(chunks)


def _sanitized_http_status(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 100 <= value <= 599:
        return None
    return value


def _classify_provider_error(error):
    if isinstance(error, ResponseSchemaError):
        return "ResponseSchemaError"
    if isinstance(error, urllib.error.HTTPError):
        return "HTTPError"
    if isinstance(error, TimeoutError):
        return "TimeoutError"
    if isinstance(error, json.JSONDecodeError):
        return "MalformedResponse"
    if isinstance(error, (urllib.error.URLError, OSError)):
        return "NetworkError"
    return "ProviderError"


def _safe_exception_attribute(error, name):
    try:
        return getattr(error, name, None)
    except Exception:
        return None


def _identifier_hash(value, field):
    bounded = _bounded_identifier(value, field)
    return hashlib.sha256(bounded.encode("utf-8")).hexdigest()


def _request_id_hash(headers):
    try:
        value = _request_id(headers)
    except ResponseSchemaError:
        raise
    except Exception:
        raise ResponseSchemaError(
            "provider request identifier headers are invalid"
        ) from None
    if value is None:
        return None
    return _identifier_hash(value, "provider request id")


def _provider_exchange_inline(body: bytes, key: str, timeout_seconds: float, endpoint=YUNWU_URL):
    """Perform one HTTP exchange in the current process.

    This path is retained for intentional in-process test doubles. Production uses the
    isolated wrapper below so a provider that keeps a socket active cannot outlive the
    declared whole-call deadline.
    """

    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw_status = getattr(response, "status", None)
        if raw_status is None:
            getcode = getattr(response, "getcode", None)
            if getcode is not None:
                raw_status = getcode()
        status = _sanitized_http_status(raw_status)
        try:
            request_id_hash = _request_id_hash(
                getattr(response, "headers", None)
            )
        except ResponseSchemaError as error:
            error._skillrace_http_status = status
            raise
        return (
            status,
            request_id_hash,
            response.read(),
        )


def _provider_exchange_child(send, body: bytes, key: str, timeout_seconds: float, endpoint=YUNWU_URL):
    """Run one production exchange and return only redacted, serializable evidence."""

    try:
        status, request_id_hash, response_body = _provider_exchange_inline(
            body, key, timeout_seconds, endpoint
        )
        send.send(
            {
                "kind": "response",
                "status": status,
                "request_id_hash": request_id_hash,
                "body": response_body,
            }
        )
    except urllib.error.HTTPError as error:
        try:
            request_id_hash = _request_id_hash(
                _safe_exception_attribute(error, "headers")
            )
        except ResponseSchemaError:
            request_id_hash = None
        send.send(
            {
                "kind": "http-error",
                "status": _sanitized_http_status(
                    _safe_exception_attribute(error, "code")
                ),
                "request_id_hash": request_id_hash,
            }
        )
    except TimeoutError:
        send.send({"kind": "timeout"})
    except urllib.error.URLError:
        send.send({"kind": "network-error"})
    except ResponseSchemaError as error:
        send.send(
            {
                "kind": "schema-error",
                "status": _sanitized_http_status(
                    _safe_exception_attribute(error, "_skillrace_http_status")
                ),
            }
        )
    except Exception:
        send.send({"kind": "provider-error"})
    finally:
        send.close()


def _provider_exchange(body: bytes, key: str, timeout_seconds: float, endpoint=YUNWU_URL):
    """Return one response under an enforceable process-level wall-clock deadline."""

    # A monkeypatched transport is an intentional in-process test fixture. Keeping it
    # local preserves its assertions and has no production effect.
    if urllib.request.urlopen is not _ORIGINAL_URLOPEN:
        return _provider_exchange_inline(body, key, timeout_seconds)

    context = multiprocessing.get_context(_HTTP_PROCESS_CONTEXT)
    receive, send = context.Pipe(duplex=False)
    process = context.Process(
        target=_provider_exchange_child,
        args=(send, body, key, timeout_seconds, endpoint),
    )
    process.daemon = True
    process.start()
    send.close()
    try:
        if not receive.poll(timeout_seconds):
            process.terminate()
            process.join(timeout=1)
            raise ProviderWallClockTimeout(
                "provider whole-call deadline elapsed"
            )
        try:
            result = receive.recv()
        except EOFError as error:
            raise RuntimeError("provider transport exited without a response") from error
    finally:
        receive.close()
        if process.is_alive():
            process.terminate()
        process.join(timeout=1)

    if result.get("kind") == "response":
        return result["status"], result["request_id_hash"], result["body"]
    if result.get("kind") == "http-error":
        error = urllib.error.HTTPError(
            YUNWU_URL,
            result.get("status") or 599,
            "provider returned an HTTP error",
            None,
            None,
        )
        error._skillrace_request_id_sha256 = result.get("request_id_hash")
        raise error
    if result.get("kind") == "timeout":
        raise TimeoutError("provider transport timed out")
    if result.get("kind") == "network-error":
        raise urllib.error.URLError("provider transport failed")
    if result.get("kind") == "schema-error":
        error = ResponseSchemaError("provider response metadata is invalid")
        error._skillrace_http_status = result.get("status")
        raise error
    raise RuntimeError("provider transport failed")


def _validate_response(value, *, expected_models):
    if not isinstance(value, dict):
        raise ResponseSchemaError("response must be an object")
    if value.get("id") is not None:
        _bounded_identifier(value["id"], "response id")
    provider_model = _bounded_model(value.get("model"))
    if provider_model not in expected_models:
        raise ResponseSchemaError("response model does not match the request")
    choices = value.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ResponseSchemaError("response choices must be a non-empty list")
    first = choices[0]
    if not isinstance(first, dict) or not isinstance(first.get("message"), dict):
        raise ResponseSchemaError("first choice must contain a message object")
    message = first["message"]
    content = message.get("content")
    if not isinstance(content, str):
        raise ResponseSchemaError("message content must be text")

    usage = value.get("usage")
    if not isinstance(usage, dict):
        raise ResponseSchemaError("response usage must be an object")
    normalized_usage = {}
    for field in ("prompt_tokens", "completion_tokens"):
        amount = usage.get(field)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ResponseSchemaError(f"usage.{field} must be a non-negative integer")
        normalized_usage[field] = amount
    total = usage.get("total_tokens")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ResponseSchemaError("usage.total_tokens must be a non-negative integer")
    if total != (
        normalized_usage["prompt_tokens"]
        + normalized_usage["completion_tokens"]
    ):
        raise ResponseSchemaError("usage.total_tokens is inconsistent")
    normalized_usage["total_tokens"] = total
    return content, usage, normalized_usage, provider_model


def _recover_billing_usage(value):
    """Recover trustworthy token counts without assuming the response is usable."""
    if not isinstance(value, dict) or not isinstance(value.get("usage"), dict):
        return None
    usage = value["usage"]
    normalized = {}
    for field in ("prompt_tokens", "completion_tokens"):
        amount = usage.get(field)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            return None
        normalized[field] = amount
    total = usage.get("total_tokens")
    if total is None:
        total = normalized["prompt_tokens"] + normalized["completion_tokens"]
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        return None
    if total != normalized["prompt_tokens"] + normalized["completion_tokens"]:
        return None
    normalized["total_tokens"] = total
    prompt_details = usage.get("prompt_tokens_details")
    cached = None
    if isinstance(prompt_details, dict):
        cached = prompt_details.get("cached_tokens")
    if cached is None:
        cached = usage.get("prompt_cache_hit_tokens")
    if (
        not isinstance(cached, bool)
        and isinstance(cached, int)
        and 0 <= cached <= normalized["prompt_tokens"]
    ):
        normalized["cached_input_tokens"] = cached
    completion_details = usage.get("completion_tokens_details")
    reasoning_tokens = (
        completion_details.get("reasoning_tokens")
        if isinstance(completion_details, dict)
        else None
    )
    if (
        not isinstance(reasoning_tokens, bool)
        and isinstance(reasoning_tokens, int)
        and 0 <= reasoning_tokens <= normalized["completion_tokens"]
    ):
        normalized["reasoning_tokens"] = reasoning_tokens
    return normalized


def _attempt_record(
    *,
    event,
    status,
    call_id,
    retry_ordinal,
    model,
    temperature,
    max_tokens,
    reasoning,
    tag,
    skill,
    messages_sha256,
    request_sha256,
    request_bytes,
    accepted_model_aliases,
    timeout_seconds,
    retry_limit,
    retry_backoff_seconds,
    retry_policy_version,
    pricing_table_version=PRICING_TABLE_VERSION,
):
    record = {
        "schema": "skillrace-model-call-journal/2",
        "event": event,
        "status": status,
        "ts": _now(),
        "call_id": call_id,
        "operation_id": call_id,
        "retry_ordinal": retry_ordinal,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "reasoning": reasoning,
        "tag": tag,
        "skill": skill,
        "tag_sha256": hashlib.sha256(tag.encode("utf-8")).hexdigest(),
        "skill_sha256": (
            hashlib.sha256(skill.encode("utf-8")).hexdigest()
            if skill is not None else None
        ),
        "messages_sha256": messages_sha256,
        "request_sha256": request_sha256,
        "request_bytes": request_bytes,
        "endpoint": YUNWU_URL,
        "accepted_model_aliases": accepted_model_aliases,
        "timeout_seconds": timeout_seconds,
        "retry_limit": retry_limit,
        "retry_backoff_seconds": retry_backoff_seconds,
        "retry_policy_version": retry_policy_version,
        "pricing_table_version": pricing_table_version,
    }
    identity = {
        "operation_id": call_id,
        "retry_ordinal": retry_ordinal,
        "event": event,
    }
    record["event_id"] = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    return record


def _normalize_nonproduction_fixture_response(value, args, settings):
    """Give explicit offline fixtures the same redacted contract as :func:`chat`.

    This deliberately does not write a provider journal: the wrapper itself is the
    conspicuous test-only boundary.  It does construct the exact safe receipt shape
    so RQ3 exercises all artifact binding and validation code in offline tests.
    """

    if not isinstance(value, dict):
        raise ResponseSchemaError("fixture response must be an object")
    if "journal_terminal_receipt" in value:
        return value
    if not args or not isinstance(args[0], list):
        raise ResponseSchemaError("fixture chat messages must be a list")
    model = settings.get("model")
    provider_model = value.get("model")
    if provider_model != model:
        raise ResponseSchemaError("fixture response model does not match the request")
    content = value.get("content")
    if not isinstance(content, str):
        raise ResponseSchemaError("fixture response content must be text")
    operation_id = settings.get("operation_id")
    try:
        _bounded_identifier(operation_id, "fixture operation id")
    except ResponseSchemaError:
        raise ResponseSchemaError("fixture requires an explicit operation_id") from None
    response_id = value.get("id")
    provider_response_id_sha256 = _identifier_hash(
        response_id, "fixture provider response id"
    )
    request_id = value.get("request_id")
    provider_request_id_sha256 = (
        _identifier_hash(request_id, "fixture provider request id")
        if request_id is not None
        else None
    )
    usage = value.get("usage")
    if not isinstance(usage, dict):
        raise ResponseSchemaError("fixture usage must be an object")
    normalized_usage = {}
    for field in ("prompt_tokens", "completion_tokens"):
        amount = usage.get(field)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ResponseSchemaError(
                f"fixture usage.{field} must be a non-negative integer"
            )
        normalized_usage[field] = amount
    total = usage.get(
        "total_tokens",
        normalized_usage["prompt_tokens"] + normalized_usage["completion_tokens"],
    )
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total
        != normalized_usage["prompt_tokens"]
        + normalized_usage["completion_tokens"]
    ):
        raise ResponseSchemaError("fixture usage.total_tokens is inconsistent")
    normalized_usage["total_tokens"] = total
    cost = value.get("cost_provider_credits", value.get("cost_usd"))
    if (
        isinstance(cost, bool)
        or not isinstance(cost, (int, float))
        or not math.isfinite(float(cost))
        or cost < 0
    ):
        raise ResponseSchemaError(
            "fixture cost_provider_credits must be finite and non-negative"
        )
    temperature = settings.get("temperature", 0.0)
    max_tokens = settings.get("max_tokens", 2048)
    reasoning = settings.get("reasoning", True)
    tag = settings.get("tag", "chat")
    skill = settings.get("skill")
    _, request_identity = _chat_request_body_and_identity(
        args[0],
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        reasoning=reasoning,
    )
    retries = settings.get("retries", 3)
    timeout_seconds = settings.get("timeout_seconds", 180)
    retry_backoff_seconds = [2 * attempt for attempt in range(1, retries)]
    terminal = _attempt_record(
        event="terminal",
        status="success",
        call_id=operation_id,
        retry_ordinal=1,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        reasoning=reasoning,
        tag=tag,
        skill=skill,
        messages_sha256=request_identity["messages_sha256"],
        request_sha256=request_identity["request_sha256"],
        request_bytes=request_identity["request_bytes"],
        accepted_model_aliases=[],
        timeout_seconds=timeout_seconds,
        retry_limit=retries,
        retry_backoff_seconds=retry_backoff_seconds,
        retry_policy_version="bounded-linear-v1",
    )
    terminal.update(
        {
            "latency_ms": 0.0,
            "provider_response_id_sha256": provider_response_id_sha256,
            "provider_request_id_sha256": provider_request_id_sha256,
            "provider_model": provider_model,
            "billing_status": "known",
            "billing_currency": BILLING_CURRENCY,
            "usage": normalized_usage,
            "cost_provider_credits": float(cost),
            "price_provider_credits": round(float(cost), 6),
            "cost_usd": None,
            "in": normalized_usage["prompt_tokens"],
            "out": normalized_usage["completion_tokens"],
            "price_usd": None,
            "error_class": None,
            "http_status": 200,
        }
    )
    call_terminal = _attempt_record(
        event="call_terminal",
        status="success",
        call_id=operation_id,
        retry_ordinal=0,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        reasoning=reasoning,
        tag=tag,
        skill=skill,
        messages_sha256=request_identity["messages_sha256"],
        request_sha256=request_identity["request_sha256"],
        request_bytes=request_identity["request_bytes"],
        accepted_model_aliases=[],
        timeout_seconds=timeout_seconds,
        retry_limit=retries,
        retry_backoff_seconds=retry_backoff_seconds,
        retry_policy_version="bounded-linear-v1",
    )
    call_terminal.update(_terminal_extras(status="success"))
    call_terminal["last_retry_ordinal"] = 1
    return {
        "content": content,
        "usage": normalized_usage,
        "cost_provider_credits": float(cost),
        "cost_usd": None,
        "model": model,
        "operation_id": operation_id,
        "provider_model": provider_model,
        "provider_response_id_sha256": provider_response_id_sha256,
        "provider_request_id_sha256": provider_request_id_sha256,
        "billing_status": "known",
        "billing_currency": BILLING_CURRENCY,
        "journal_terminal_event_id": terminal["event_id"],
        "journal_terminal_receipt_sha256": canonical_json_hash(terminal),
        "journal_terminal_receipt": terminal,
        "journal_call_terminal_event_id": call_terminal["event_id"],
        "journal_call_terminal_receipt_sha256": canonical_json_hash(call_terminal),
        "journal_call_terminal_receipt": call_terminal,
    }


def _require_sha256(value, field, *, nullable=False):
    if value is None and nullable:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ResponseSchemaError(f"{field} must be a SHA-256 hex digest")
    return value


def validate_terminal_receipt(
    receipt,
    *,
    expected_model,
    expected_operation_id,
    expected_usage=None,
    expected_cost_provider_credits=None,
    expected_cost_usd=None,
    expected_request_identity=None,
    expected_tag=None,
    expected_skill=None,
):
    """Validate the redacted immutable success receipt copied into an artifact."""

    if not isinstance(receipt, dict):
        raise ResponseSchemaError("journal terminal receipt must be an object")
    if (
        receipt.get("schema") != "skillrace-model-call-journal/2"
        or receipt.get("event") != "terminal"
        or receipt.get("status") != "success"
    ):
        raise ResponseSchemaError("journal terminal receipt is not a success terminal")
    if (
        receipt.get("operation_id") != expected_operation_id
        or receipt.get("call_id") != expected_operation_id
    ):
        raise ResponseSchemaError("journal terminal receipt operation identity mismatch")
    retry_ordinal = receipt.get("retry_ordinal")
    if (
        isinstance(retry_ordinal, bool)
        or not isinstance(retry_ordinal, int)
        or retry_ordinal < 1
    ):
        raise ResponseSchemaError("journal terminal retry ordinal is invalid")
    expected_event_id = hashlib.sha256(
        canonical_json_bytes(
            {
                "operation_id": expected_operation_id,
                "retry_ordinal": retry_ordinal,
                "event": "terminal",
            }
        )
    ).hexdigest()
    if receipt.get("event_id") != expected_event_id:
        raise ResponseSchemaError("journal terminal event identity mismatch")
    if (
        receipt.get("model") != expected_model
        or receipt.get("provider_model") != expected_model
    ):
        raise ResponseSchemaError("journal terminal provider model mismatch")
    try:
        expected_pricing_table_version = rate_card_version_for_model(expected_model)
    except ValueError:
        expected_pricing_table_version = PRICING_TABLE_VERSION
    if receipt.get("pricing_table_version") != expected_pricing_table_version:
        raise ResponseSchemaError("journal terminal pricing table mismatch")
    if receipt.get("billing_status") != "known":
        raise ResponseSchemaError("journal terminal billing status is not known")
    if receipt.get("billing_currency") != BILLING_CURRENCY:
        raise ResponseSchemaError("journal terminal billing currency mismatch")
    usage = receipt.get("usage")
    if not isinstance(usage, dict):
        raise ResponseSchemaError("journal terminal usage is missing")
    normalized_usage = {}
    for field in ("prompt_tokens", "completion_tokens"):
        amount = usage.get(field)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ResponseSchemaError(f"journal terminal usage.{field} is invalid")
        normalized_usage[field] = amount
    total = usage.get("total_tokens")
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total
        != normalized_usage["prompt_tokens"]
        + normalized_usage["completion_tokens"]
    ):
        raise ResponseSchemaError("journal terminal usage.total_tokens is inconsistent")
    normalized_usage["total_tokens"] = total
    if expected_usage is not None and normalized_usage != expected_usage:
        raise ResponseSchemaError("journal terminal usage mismatch")
    if (
        receipt.get("in") != normalized_usage["prompt_tokens"]
        or receipt.get("out") != normalized_usage["completion_tokens"]
    ):
        raise ResponseSchemaError("journal terminal token accounting mismatch")
    cost = receipt.get("cost_provider_credits")
    if (
        isinstance(cost, bool)
        or not isinstance(cost, (int, float))
        or not math.isfinite(float(cost))
        or cost < 0
    ):
        raise ResponseSchemaError("journal terminal provider-credit cost is invalid")
    expected_cost = (
        expected_cost_provider_credits
        if expected_cost_provider_credits is not None
        else expected_cost_usd
    )
    if expected_cost is not None and float(cost) != float(expected_cost):
        raise ResponseSchemaError("journal terminal cost mismatch")
    if receipt.get("price_provider_credits") != round(float(cost), 6):
        raise ResponseSchemaError("journal terminal rounded provider-credit price mismatch")
    if receipt.get("cost_usd") is not None or receipt.get("price_usd") is not None:
        raise ResponseSchemaError("journal terminal must not infer a USD conversion")
    provider_response_id = _require_sha256(
        receipt.get("provider_response_id_sha256"),
        "provider response identity",
    )
    provider_request_id = _require_sha256(
        receipt.get("provider_request_id_sha256"),
        "provider request identity",
        nullable=True,
    )
    _require_sha256(receipt.get("messages_sha256"), "messages identity")
    _require_sha256(receipt.get("request_sha256"), "request identity")
    if expected_request_identity is not None:
        for field in (
            "messages_sha256",
            "request_sha256",
            "request_bytes",
            "temperature",
            "max_tokens",
            "reasoning",
        ):
            if receipt.get(field) != expected_request_identity.get(field):
                raise ResponseSchemaError(
                    f"journal terminal request identity mismatch ({field})"
                )
    if expected_tag is not None and receipt.get("tag") != expected_tag:
        raise ResponseSchemaError("journal terminal tag identity mismatch")
    if expected_skill is not None and receipt.get("skill") != expected_skill:
        raise ResponseSchemaError("journal terminal skill identity mismatch")
    if (
        receipt.get("http_status") is None
        or not isinstance(receipt.get("http_status"), int)
        or not 200 <= receipt["http_status"] < 300
        or receipt.get("error_class") is not None
        or receipt.get("endpoint") != YUNWU_URL
    ):
        raise ResponseSchemaError("journal terminal success transport is invalid")
    return {
        "operation_id": expected_operation_id,
        "event_id": expected_event_id,
        "provider_model": expected_model,
        "provider_response_id_sha256": provider_response_id,
        "provider_request_id_sha256": provider_request_id,
        "billing_status": "known",
        "billing_currency": BILLING_CURRENCY,
        "usage": normalized_usage,
        "cost_provider_credits": float(cost),
        "cost_usd": None,
        "receipt_sha256": canonical_json_hash(receipt),
    }


def validate_call_terminal_receipt(
    receipt,
    *,
    expected_model,
    expected_operation_id,
    expected_last_retry_ordinal,
    expected_request_identity=None,
    expected_tag=None,
    expected_skill=None,
):
    """Validate the immutable whole-operation success receipt."""

    if not isinstance(receipt, dict):
        raise ResponseSchemaError("journal call terminal receipt must be an object")
    if (
        receipt.get("schema") != "skillrace-model-call-journal/2"
        or receipt.get("event") != "call_terminal"
        or receipt.get("status") != "success"
    ):
        raise ResponseSchemaError(
            "journal call terminal receipt is not a success terminal"
        )
    if (
        receipt.get("operation_id") != expected_operation_id
        or receipt.get("call_id") != expected_operation_id
        or receipt.get("retry_ordinal") != 0
        or receipt.get("last_retry_ordinal") != expected_last_retry_ordinal
    ):
        raise ResponseSchemaError("journal call terminal operation identity mismatch")
    expected_event_id = hashlib.sha256(
        canonical_json_bytes(
            {
                "operation_id": expected_operation_id,
                "retry_ordinal": 0,
                "event": "call_terminal",
            }
        )
    ).hexdigest()
    if receipt.get("event_id") != expected_event_id:
        raise ResponseSchemaError("journal call terminal event identity mismatch")
    if receipt.get("model") != expected_model:
        raise ResponseSchemaError("journal call terminal model mismatch")
    try:
        expected_pricing_table_version = rate_card_version_for_model(expected_model)
    except ValueError:
        expected_pricing_table_version = PRICING_TABLE_VERSION
    if receipt.get("pricing_table_version") != expected_pricing_table_version:
        raise ResponseSchemaError("journal call terminal pricing table mismatch")
    if expected_request_identity is not None:
        for field in (
            "messages_sha256",
            "request_sha256",
            "request_bytes",
            "temperature",
            "max_tokens",
            "reasoning",
        ):
            if receipt.get(field) != expected_request_identity.get(field):
                raise ResponseSchemaError(
                    f"journal call terminal request identity mismatch ({field})"
                )
    if expected_tag is not None and receipt.get("tag") != expected_tag:
        raise ResponseSchemaError("journal call terminal tag identity mismatch")
    if expected_skill is not None and receipt.get("skill") != expected_skill:
        raise ResponseSchemaError("journal call terminal skill identity mismatch")
    if (
        receipt.get("billing_status") != "unknown"
        or receipt.get("billing_currency") is not None
        or receipt.get("usage") is not None
        or receipt.get("cost_provider_credits") is not None
        or receipt.get("price_provider_credits") is not None
        or receipt.get("cost_usd") is not None
        or receipt.get("provider_response_id_sha256") is not None
        or receipt.get("provider_request_id_sha256") is not None
        or receipt.get("provider_model") is not None
        or receipt.get("error_class") is not None
        or receipt.get("http_status") is not None
    ):
        raise ResponseSchemaError("journal call terminal summary is malformed")
    return {
        "operation_id": expected_operation_id,
        "event_id": expected_event_id,
        "last_retry_ordinal": expected_last_retry_ordinal,
        "receipt_sha256": canonical_json_hash(receipt),
    }


def validate_chat_result(
    value,
    *,
    expected_model,
    expected_operation_id,
    expected_request_identity=None,
    expected_tag=None,
    expected_skill=None,
):
    """Validate a production chat result before committing derived artifacts."""

    if not isinstance(value, dict) or not isinstance(value.get("content"), str):
        raise ResponseSchemaError("chat result is malformed")
    if value.get("model") != expected_model:
        raise ResponseSchemaError("chat result model mismatch")
    if value.get("operation_id") != expected_operation_id:
        raise ResponseSchemaError("chat result operation identity mismatch")
    usage = value.get("usage")
    if not isinstance(usage, dict):
        raise ResponseSchemaError("chat result usage is missing")
    normalized_usage = {}
    for field in ("prompt_tokens", "completion_tokens"):
        amount = usage.get(field)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ResponseSchemaError(f"chat result usage.{field} is invalid")
        normalized_usage[field] = amount
    total = usage.get("total_tokens")
    if total is None:
        total = normalized_usage["prompt_tokens"] + normalized_usage["completion_tokens"]
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total
        != normalized_usage["prompt_tokens"]
        + normalized_usage["completion_tokens"]
    ):
        raise ResponseSchemaError("chat result usage.total_tokens is inconsistent")
    normalized_usage["total_tokens"] = total
    cost = value.get("cost_provider_credits")
    if (
        isinstance(cost, bool)
        or not isinstance(cost, (int, float))
        or not math.isfinite(float(cost))
        or cost < 0
    ):
        raise ResponseSchemaError("chat result cost_provider_credits is invalid")
    if value.get("cost_usd") is not None:
        raise ResponseSchemaError("chat result must not infer a USD conversion")
    receipt = value.get("journal_terminal_receipt")
    validated = validate_terminal_receipt(
        receipt,
        expected_model=expected_model,
        expected_operation_id=expected_operation_id,
        expected_usage=normalized_usage,
        expected_cost_provider_credits=cost,
        expected_request_identity=expected_request_identity,
        expected_tag=expected_tag,
        expected_skill=expected_skill,
    )
    call_terminal = validate_call_terminal_receipt(
        value.get("journal_call_terminal_receipt"),
        expected_model=expected_model,
        expected_operation_id=expected_operation_id,
        expected_last_retry_ordinal=receipt["retry_ordinal"],
        expected_request_identity=expected_request_identity,
        expected_tag=expected_tag,
        expected_skill=expected_skill,
    )
    if value.get("provider_model") != validated["provider_model"]:
        raise ResponseSchemaError("chat result provider model mismatch")
    if value.get("billing_status") != validated["billing_status"]:
        raise ResponseSchemaError("chat result billing status mismatch")
    if value.get("billing_currency") != validated["billing_currency"]:
        raise ResponseSchemaError("chat result billing currency mismatch")
    for field in (
        "provider_response_id_sha256",
        "provider_request_id_sha256",
    ):
        if value.get(field) != validated[field]:
            raise ResponseSchemaError(f"chat result {field} mismatch")
    if value.get("journal_terminal_event_id") != validated["event_id"]:
        raise ResponseSchemaError("chat result journal terminal event mismatch")
    if value.get("journal_terminal_receipt_sha256") != validated["receipt_sha256"]:
        raise ResponseSchemaError("chat result journal terminal receipt hash mismatch")
    if value.get("journal_call_terminal_event_id") != call_terminal["event_id"]:
        raise ResponseSchemaError("chat result journal call terminal event mismatch")
    if (
        value.get("journal_call_terminal_receipt_sha256")
        != call_terminal["receipt_sha256"]
    ):
        raise ResponseSchemaError(
            "chat result journal call terminal receipt hash mismatch"
        )
    return validated


def _operation_records(ledger_path, operation_id):
    receipts = pathlib.Path(ledger_path).with_name(
        f"{pathlib.Path(ledger_path).name}.events"
    )
    if not receipts.is_dir():
        return []
    records = []
    for path in sorted(receipts.glob("*.json")):
        try:
            value = json.loads(path.read_bytes())
            if not isinstance(value, dict) or value.get("event_id") != path.stem:
                raise ValueError("invalid receipt")
        except Exception:
            raise JournalError("durable journal state is malformed") from None
        if isinstance(value, dict) and value.get("operation_id") == operation_id:
            records.append(value)
    return records


def _record_age_seconds(record):
    try:
        timestamp = datetime.datetime.fromisoformat(record["ts"])
        if timestamp.tzinfo is None:
            raise ValueError("timestamp has no timezone")
        age = (
            datetime.datetime.now(datetime.timezone.utc) - timestamp
        ).total_seconds()
    except Exception:
        raise JournalError("durable journal record has an invalid timestamp") from None
    return max(0.0, age)


def _terminal_extras(*, status, provider_model=None):
    return {
        "provider_response_id_sha256": None,
        "provider_request_id_sha256": None,
        "provider_model": provider_model,
        "billing_status": "unknown",
        "billing_currency": None,
        "usage": None,
        "cost_provider_credits": None,
        "price_provider_credits": None,
        "cost_usd": None,
        "in": None,
        "out": None,
        "price_usd": None,
        "latency_ms": None,
        "error_class": None,
        "http_status": None,
        "status": status,
    }


def chat(
    messages,
    model=DEFAULT_DEVELOPMENT_MODEL,
    temperature=0.0,
    max_tokens=2048,
    retries=3,
    reasoning=True,
    tag="chat",
    skill=None,
    *,
    ledger_path=None,
    journal_mode=None,
    accepted_model_aliases=(),
    operation_id=None,
    stale_intent_seconds=900,
    timeout_seconds=180,
):
    """Run one journaled chat completion and return content plus receipt provenance.

    reasoning=False disables the model's thinking (`enable_thinking: false`) — ~3x
    faster and much cheaper (reasoning tokens bill at the output rate). Use it for
    SkillRACE's own generation/judgment calls (we don't need their trace). The
    agent-under-test, which DOES need a reasoning trace, runs via pi, not here."""
    if (
        isinstance(retries, bool)
        or not isinstance(retries, int)
        or not 1 <= retries <= MAX_RETRIES
    ):
        raise ValueError(f"retries must be an integer between 1 and {MAX_RETRIES}")
    # Calls made by shared generator/checker helpers intentionally omit a mode.
    # Frozen experiment models receive strict production receipts; a model that
    # is explicitly catalogued only as a development candidate can never create
    # headline evidence accidentally.  Explicit ``production`` remains
    # fail-closed below for every non-frozen model.
    if journal_mode is None:
        journal_mode = (
            "development"
            if model in DEVELOPMENT_CANDIDATE_MODELS
            else "production"
        )
    elif journal_mode not in {"production", "development"}:
        raise ValueError(
            "journal_mode must be None, 'production', or 'development'"
        )
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not math.isfinite(temperature)
        or not 0 <= temperature <= 2
        or (
            max_tokens is not None
            and (
                isinstance(max_tokens, bool)
                or not isinstance(max_tokens, int)
                or not 1 <= max_tokens <= 1_000_000
            )
        )
        or not isinstance(reasoning, bool)
    ):
        raise ValueError("request parameters are invalid or out of bounds")
    _validate_journal_metadata(model, tag, skill)
    if (
        isinstance(stale_intent_seconds, bool)
        or not isinstance(stale_intent_seconds, (int, float))
        or not 0 <= stale_intent_seconds <= 86400
    ):
        raise ValueError("stale_intent_seconds must be between zero and 86400")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 3600
    ):
        raise ValueError("timeout_seconds must be between zero and 3600")
    if isinstance(accepted_model_aliases, str) or not isinstance(
        accepted_model_aliases, (tuple, list, set, frozenset)
    ):
        raise ValueError("accepted_model_aliases must be a bounded collection")
    if len(accepted_model_aliases) > 8:
        raise ValueError("accepted_model_aliases must contain at most eight values")
    accepted_models = {model}
    for alias in accepted_model_aliases:
        try:
            accepted_models.add(_bounded_model(alias, "accepted model alias"))
        except ResponseSchemaError:
            raise ValueError(
                "accepted_model_aliases contains an invalid value"
            ) from None
    try:
        resolved_ledger_path = _resolve_ledger_path(ledger_path)
    except Exception:
        if journal_mode == "production":
            raise JournalError("could not resolve model-call journal") from None
        resolved_ledger_path = _DISABLED_LEDGER
    pricing_known = has_known_provider_credit_rate(model)
    if not pricing_known and journal_mode == "production":
        raise UnknownPricingError("model pricing is unavailable") from None
    if journal_mode == "production" and model not in EXPERIMENT_MODELS:
        raise UnknownPricingError("model pricing is not frozen for production") from None
    pricing_table_version = (
        rate_card_version_for_model(model)
        if pricing_known
        else PRICING_TABLE_VERSION
    )
    key = yunwu_api_key()
    if not key:
        raise RuntimeError(
            f"{YUNWU_API_KEY_ENV} not set in environment "
            f"(or compatibility alias {YUNWU_API_KEY_COMPAT_ENV})"
        )
    body, request_identity = _chat_request_body_and_identity(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        reasoning=reasoning,
    )
    messages_sha256 = request_identity["messages_sha256"]
    request_sha256 = request_identity["request_sha256"]
    request_bytes = request_identity["request_bytes"]
    retry_backoff_seconds = [2 * attempt for attempt in range(1, retries)]
    retry_policy_version = "bounded-linear-v1"
    if operation_id is None:
        operation_id = uuid.uuid4().hex
    if (
        not isinstance(operation_id, str)
        or not operation_id
        or len(operation_id) > 256
        or any(
            character not in _SAFE_PROVIDER_ID_CHARACTERS
            for character in operation_id
        )
    ):
        raise ValueError("operation_id must be bounded safe text")
    call_id = operation_id

    def common_for(retry_ordinal):
        return {
            "call_id": call_id,
            "retry_ordinal": retry_ordinal,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "reasoning": bool(reasoning),
            "tag": tag,
            "skill": skill,
            "messages_sha256": messages_sha256,
            "request_sha256": request_sha256,
            "request_bytes": request_bytes,
            "accepted_model_aliases": sorted(accepted_models - {model}),
            "timeout_seconds": timeout_seconds,
            "retry_limit": retries,
            "retry_backoff_seconds": retry_backoff_seconds,
            "retry_policy_version": retry_policy_version,
            "pricing_table_version": pricing_table_version,
        }

    def persist_call_terminal(status, last_retry_ordinal):
        record = _attempt_record(
            event="call_terminal",
            status=status,
            **common_for(0),
        )
        record.update(_terminal_extras(status=status))
        record["last_retry_ordinal"] = last_retry_ordinal
        _persist(
            record,
            ledger_path=resolved_ledger_path,
            journal_mode=journal_mode,
        )
        return record

    existing = (
        [] if resolved_ledger_path is _DISABLED_LEDGER
        else _operation_records(resolved_ledger_path, operation_id)
    )
    if any(record.get("request_sha256") != request_sha256 for record in existing):
        raise OperationConflictError(
            "operation identifier conflicts with a durable request"
        ) from None
    expected_policy = {
        "request_bytes": request_bytes,
        "timeout_seconds": timeout_seconds,
        "retry_limit": retries,
        "retry_backoff_seconds": retry_backoff_seconds,
        "retry_policy_version": retry_policy_version,
    }
    if any(
        any(record.get(field) != value for field, value in expected_policy.items())
        for record in existing
    ):
        raise OperationConflictError(
            "operation identifier conflicts with a durable request policy"
        ) from None
    call_terminals = [
        record for record in existing if record.get("event") == "call_terminal"
    ]
    if call_terminals:
        if call_terminals[-1].get("status") == "outcome_unknown":
            raise OutcomeUnknownError("operation outcome is unknown") from None
        raise DuplicateOperationError("operation is already terminal") from None

    attempt_terminals = {
        record.get("retry_ordinal"): record
        for record in existing
        if record.get("event") == "terminal"
    }
    successful = [
        record for record in attempt_terminals.values()
        if record.get("status") == "success"
    ]
    if successful:
        persist_call_terminal("success", successful[-1]["retry_ordinal"])
        raise DuplicateOperationError("operation is already terminal") from None
    unknown = [
        record for record in attempt_terminals.values()
        if record.get("status") == "outcome_unknown"
    ]
    if unknown:
        persist_call_terminal("outcome_unknown", unknown[-1]["retry_ordinal"])
        raise OutcomeUnknownError("operation outcome is unknown") from None

    intents = {
        record.get("retry_ordinal"): record
        for record in existing
        if record.get("event") == "intent"
    }
    unmatched = [
        record
        for ordinal, record in intents.items()
        if ordinal not in attempt_terminals
    ]
    if unmatched:
        intent = sorted(unmatched, key=lambda record: record["retry_ordinal"])[0]
        if _record_age_seconds(intent) < stale_intent_seconds:
            raise OperationInProgressError("operation intent is still fresh") from None
        terminal = _attempt_record(
            event="terminal",
            status="outcome_unknown",
            **common_for(intent["retry_ordinal"]),
        )
        terminal.update(_terminal_extras(status="outcome_unknown"))
        _persist(
            terminal,
            ledger_path=resolved_ledger_path,
            journal_mode=journal_mode,
        )
        persist_call_terminal("outcome_unknown", intent["retry_ordinal"])
        raise OutcomeUnknownError("operation outcome is unknown") from None

    completed_errors = [
        ordinal
        for ordinal, record in attempt_terminals.items()
        if record.get("status") == "error" and isinstance(ordinal, int)
    ]
    first_attempt = max(completed_errors, default=0) + 1
    if first_attempt > retries:
        persist_call_terminal("error", max(completed_errors))
        raise DuplicateOperationError("operation is already terminal") from None
    if completed_errors:
        previous_ordinal = max(completed_errors)
        previous = attempt_terminals[previous_ordinal]
        required_backoff = retry_backoff_seconds[previous_ordinal - 1]
        remaining_backoff = required_backoff - _record_age_seconds(previous)
        if remaining_backoff > 0:
            time.sleep(remaining_backoff)
    last_error_class = None
    for attempt in range(first_attempt, retries + 1):
        common = common_for(attempt)
        intent_created = _persist(
            _attempt_record(event="intent", status="pending", **common),
            ledger_path=resolved_ledger_path,
            journal_mode=journal_mode,
        )
        if intent_created is False:
            raise OperationInProgressError(
                "operation intent is already durable"
            ) from None
        started = time.monotonic()
        provider_request_id_sha256 = None
        provider_response_id_sha256 = None
        provider_model = None
        provider_http_status = None
        journal_usage = None
        cost = None
        try:
            (
                provider_http_status,
                provider_request_id_sha256,
                response_body,
            ) = _provider_exchange(
                body,
                key,
                timeout_seconds,
                YUNWU_RESPONSES_URL if model in RESPONSES_MODELS else YUNWU_URL,
            )
            if (
                provider_http_status is None
                or not 200 <= provider_http_status < 300
            ):
                raise ResponseSchemaError("provider HTTP status must be 2xx")
            response_value = _decode_provider_response_body(
                response_body,
                streamed=model in STREAM_ONLY_MODELS,
            )
            if model in RESPONSES_MODELS:
                response_value = _normalize_responses_body(response_value)
            journal_usage = _recover_billing_usage(response_value)
            if isinstance(response_value, dict) and response_value.get("model") is not None:
                provider_model = _bounded_model(response_value["model"])
            if (
                journal_usage is not None
                and pricing_known
                and provider_model in accepted_models
            ):
                cost = provider_credits_for_known_model(
                    model,
                    journal_usage["prompt_tokens"],
                    journal_usage["completion_tokens"],
                    cached_input_tokens=journal_usage.get(
                        "cached_input_tokens", 0
                    ),
                )
            if (
                isinstance(response_value, dict)
                and response_value.get("id") is not None
            ):
                provider_response_id_sha256 = _identifier_hash(
                    response_value["id"], "response id"
                )
            content, usage, validated_usage, provider_model = _validate_response(
                response_value,
                expected_models=accepted_models,
            )
            if journal_usage is None:
                journal_usage = validated_usage
            elif any(
                journal_usage[field] != validated_usage[field]
                for field in ("prompt_tokens", "completion_tokens", "total_tokens")
            ):
                raise ResponseSchemaError(
                    "validated response usage differs from billing recovery"
                )
        except ProviderWallClockTimeout:
            terminal = _attempt_record(
                event="terminal", status="outcome_unknown", **common
            )
            terminal.update(_terminal_extras(status="outcome_unknown"))
            terminal["latency_ms"] = round(
                (time.monotonic() - started) * 1000, 3
            )
            _persist(
                terminal,
                ledger_path=resolved_ledger_path,
                journal_mode=journal_mode,
            )
            persist_call_terminal("outcome_unknown", attempt)
            raise OutcomeUnknownError(
                "provider whole-call deadline elapsed; outcome is unknown"
            ) from None
        except Exception as e:  # noqa: BLE001 — surface after retries
            provider_http_status = provider_http_status or _sanitized_http_status(
                _safe_exception_attribute(e, "code")
            )
            if provider_http_status is None:
                provider_http_status = _sanitized_http_status(
                    _safe_exception_attribute(e, "_skillrace_http_status")
                )
            if provider_request_id_sha256 is None:
                inherited_hash = _safe_exception_attribute(
                    e, "_skillrace_request_id_sha256"
                )
                if isinstance(inherited_hash, str) and re.fullmatch(
                    r"[0-9a-f]{64}", inherited_hash
                ):
                    provider_request_id_sha256 = inherited_hash
                else:
                    try:
                        provider_request_id_sha256 = _request_id_hash(
                            _safe_exception_attribute(e, "headers")
                        )
                    except ResponseSchemaError as identifier_error:
                        e = identifier_error
            last_error_class = _classify_provider_error(e)
            terminal = _attempt_record(
                event="terminal", status="error", **common
            )
            terminal.update(
                {
                    "latency_ms": round((time.monotonic() - started) * 1000, 3),
                    "provider_response_id_sha256": provider_response_id_sha256,
                    "provider_request_id_sha256": provider_request_id_sha256,
                    "provider_model": provider_model,
                    "billing_status": (
                        "known" if journal_usage is not None and cost is not None
                        else "unknown"
                    ),
                    "billing_currency": (
                        BILLING_CURRENCY if cost is not None else None
                    ),
                    "usage": journal_usage,
                    "cost_provider_credits": cost,
                    "price_provider_credits": (
                        round(cost, 6) if cost is not None else None
                    ),
                    "cost_usd": None,
                    "in": (
                        journal_usage["prompt_tokens"]
                        if journal_usage is not None else None
                    ),
                    "out": (
                        journal_usage["completion_tokens"]
                        if journal_usage is not None else None
                    ),
                    "price_usd": None,
                    "error_class": last_error_class,
                    "http_status": provider_http_status,
                }
            )
            _persist(
                terminal,
                ledger_path=resolved_ledger_path,
                journal_mode=journal_mode,
            )
            if attempt < retries:
                time.sleep(retry_backoff_seconds[attempt - 1])
            continue

        terminal = _attempt_record(event="terminal", status="success", **common)
        terminal.update(
            {
                "latency_ms": round((time.monotonic() - started) * 1000, 3),
                "provider_response_id_sha256": provider_response_id_sha256,
                "provider_request_id_sha256": provider_request_id_sha256,
                "provider_model": provider_model,
                "billing_status": "known" if cost is not None else "unknown",
                "billing_currency": BILLING_CURRENCY if cost is not None else None,
                "usage": journal_usage,
                "cost_provider_credits": cost,
                "price_provider_credits": (
                    round(cost, 6) if cost is not None else None
                ),
                "cost_usd": None,
                "in": journal_usage["prompt_tokens"],
                "out": journal_usage["completion_tokens"],
                "price_usd": None,
                "error_class": None,
                "http_status": provider_http_status,
            }
        )
        _persist(
            terminal,
            ledger_path=resolved_ledger_path,
            journal_mode=journal_mode,
        )
        call_terminal = persist_call_terminal("success", attempt)
        return {
            "content": content,
            "usage": usage,
            "cost_provider_credits": cost,
            "cost_usd": None,
            "model": model,
            # The provider's raw identifiers never leave this client.  RQ3 stores
            # this redacted copy of the exact immutable receipt, which was made
            # durable above, and binds its artifact to both its event id and hash.
            "operation_id": operation_id,
            "provider_model": provider_model,
            "provider_response_id_sha256": provider_response_id_sha256,
            "provider_request_id_sha256": provider_request_id_sha256,
            "billing_status": terminal["billing_status"],
            "billing_currency": terminal["billing_currency"],
            "journal_terminal_event_id": terminal["event_id"],
            "journal_terminal_receipt_sha256": canonical_json_hash(terminal),
            "journal_terminal_receipt": dict(terminal),
            "journal_call_terminal_event_id": call_terminal["event_id"],
            "journal_call_terminal_receipt_sha256": canonical_json_hash(
                call_terminal
            ),
            "journal_call_terminal_receipt": dict(call_terminal),
        }
    persist_call_terminal("error", retries)
    raise RuntimeError(
        f"Yunwu chat failed after {retries} attempts: {last_error_class}"
    ) from None


def extract_json(text):
    """Tolerant JSON extraction: strips ``` fences, then parses the first
    balanced [..] or {..}. Raises ValueError if nothing parses."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if t.count("```") >= 2 else t.strip("`")
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    try:
        return json.loads(t)
    except Exception:
        pass
    for open_c, close_c in (("[", "]"), ("{", "}")):
        i = t.find(open_c)
        if i < 0:
            continue
        depth = 0
        for j in range(i, len(t)):
            if t[j] == open_c:
                depth += 1
            elif t[j] == close_c:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[i:j + 1])
                    except Exception:
                        break
    raise ValueError(f"no parseable JSON in model output: {text[:200]!r}")
