from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Callable
import urllib.error
import urllib.request
import uuid

from ..storage import atomic_write_json
from .providers import (
    estimate_cost,
    qualified_model,
    resolve_model,
    write_pi_models,
)


_AVAILABLE_TOOLS = {"read", "bash", "edit", "write", "grep", "find", "ls"}


_PI_RUNNER = r'''#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || index + 1 >= process.argv.length) throw new Error(`missing ${name}`);
  return process.argv[index + 1];
}

async function loadPiSdk() {
  const candidates = [
    "/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/dist/index.js",
    "/usr/local/lib/node_modules/@mariozechner/pi-coding-agent/dist/index.js",
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return import(pathToFileURL(candidate).href);
  }
  throw new Error("pinned Pi SDK is unavailable");
}

const provider = argument("--provider");
const modelId = argument("--model");
const keyEnvironment = argument("--key-environment");
const maxTurns = Number.parseInt(argument("--max-turns"), 10);
const allowedTools = argument("--allowed-tools").split(",").filter(Boolean);
const promptPath = argument("--prompt-path");
const accountingDir = argument("--accounting-dir");
const tracePath = path.join(accountingDir, "trace.jsonl");
const eventPath = path.join(accountingDir, "tool-events.jsonl");
const usagePath = path.join(accountingDir, "usage.json");
const authPath = path.join(accountingDir, "auth.json");
fs.mkdirSync(accountingDir, { recursive: true });

let session;
let turnCount = 0;
let toolCallCount = 0;
let status = "error";

function record(value) {
  fs.appendFileSync(eventPath, `${JSON.stringify(value)}\n`, "utf8");
}

function copySessionTrace() {
  const candidates = fs.readdirSync(accountingDir)
    .filter((name) => name.endsWith(".jsonl") && !["trace.jsonl", "tool-events.jsonl"].includes(name))
    .sort();
  if (candidates.length) fs.copyFileSync(path.join(accountingDir, candidates[0]), tracePath);
}

function collectUsage() {
  const usage = {
    requested_model: modelId,
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    total_tokens: 0,
    turns: 0,
    tool_call_count: toolCallCount,
  };
  if (!fs.existsSync(tracePath)) return usage;
  for (const line of fs.readFileSync(tracePath, "utf8").split("\n")) {
    if (!line) continue;
    try {
      const message = JSON.parse(line).message || {};
      if (message.role !== "assistant") continue;
      const item = message.usage || {};
      usage.input_tokens += Number(item.input || 0);
      usage.output_tokens += Number(item.output || 0);
      usage.cache_read_tokens += Number(item.cacheRead || 0);
      usage.cache_write_tokens += Number(item.cacheWrite || 0);
      usage.total_tokens += Number(item.totalTokens || 0);
      usage.turns += 1;
      if (!usage.model && message.model) usage.model = message.model;
    } catch {}
  }
  return usage;
}

try {
  if (!Number.isInteger(maxTurns) || maxTurns < 1 || maxTurns > 12) {
    throw new Error("max turns must be in 1..12");
  }
  const prompt = fs.readFileSync(promptPath, "utf8");
  const {
    AuthStorage,
    createAgentSession,
    DefaultResourceLoader,
    ModelRegistry,
    SessionManager,
    SettingsManager,
  } = await loadPiSdk();
  const authStorage = AuthStorage.create(authPath);
  const providerKey = process.env[keyEnvironment];
  if (!providerKey) throw new Error(`${keyEnvironment} is not set`);
  authStorage.setRuntimeApiKey(provider, providerKey);
  const modelCatalog = "/root/.pi/agent/models.json";
  const modelRegistry = ModelRegistry.create(authStorage, modelCatalog);
  const model = modelRegistry.find(provider, modelId);
  if (!model) throw new Error(`model not found: ${provider}/${modelId}`);
  const settingsManager = SettingsManager.inMemory({
    compaction: { enabled: false },
    retry: { enabled: true, maxRetries: 1, baseDelayMs: 2000 },
  });
  const evidenceExtension = (pi) => {
    pi.on("tool_call", (event) => {
      toolCallCount += 1;
      record({ type: "tool_call", tool: event.toolName, input: event.input || {} });
    });
    pi.on("tool_result", (event) => {
      record({ type: "tool_result", tool: event.toolName, error: Boolean(event.isError) });
    });
  };
  const resourceLoader = new DefaultResourceLoader({
    cwd: "/workspace",
    agentDir: "/root/.pi/agent",
    settingsManager,
    extensionFactories: [evidenceExtension],
    noExtensions: true,
    noSkills: true,
    noPromptTemplates: true,
    noThemes: true,
    noContextFiles: true,
  });
  await resourceLoader.reload();
  const created = await createAgentSession({
    cwd: "/workspace",
    agentDir: "/root/.pi/agent",
    model,
    thinkingLevel: "medium",
    authStorage,
    modelRegistry,
    resourceLoader,
    tools: allowedTools,
    sessionManager: SessionManager.create("/workspace", accountingDir),
    settingsManager,
  });
  session = created.session;
  if (created.extensionsResult.errors.length) {
    throw new Error(JSON.stringify(created.extensionsResult.errors));
  }
  session.subscribe((event) => {
    if (event.type === "turn_end") {
      turnCount += 1;
      record({ type: "turn_end", turn: turnCount });
      if (turnCount >= maxTurns && session.isStreaming) void session.abort();
    } else if (event.type === "agent_end") {
      record({ type: "agent_end" });
    }
  });
  await session.prompt(prompt);
  status = "completed";
  session.dispose();
  session = undefined;
  copySessionTrace();
  fs.writeFileSync(usagePath, JSON.stringify({ status, ...collectUsage() }, null, 2));
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  if (session) session.dispose();
  copySessionTrace();
  fs.writeFileSync(usagePath, JSON.stringify({ status, error: message.slice(-500), ...collectUsage() }, null, 2));
  console.error(`bounded Pi invocation failed: ${message}`);
  process.exitCode = 1;
} finally {
  try { fs.rmSync(authPath, { force: true }); } catch {}
}
'''


@dataclass(frozen=True)
class PiRequest:
    operation_id: str
    model: str
    prompt_path: Path
    output_dir: Path
    image: str
    allowed_tools: tuple[str, ...]
    max_turns: int
    timeout_seconds: int
    mounts: tuple[tuple[Path, str, str], ...] = ()
    provider: str = "yunwu"

    def __post_init__(self) -> None:
        if not self.operation_id or len(self.operation_id) > 256:
            raise ValueError("operation_id must be bounded nonempty text")
        if not self.model or not self.image:
            raise ValueError("model and image are required")
        resolve_model(self.provider, self.model)
        if not self.prompt_path.is_file():
            raise ValueError("prompt_path must name a file")
        if not self.allowed_tools or not set(self.allowed_tools) <= _AVAILABLE_TOOLS:
            raise ValueError("allowed_tools must be an explicit supported set")
        if not 1 <= self.max_turns <= 12:
            raise ValueError("max_turns must be in 1..12")
        if not 1 <= self.timeout_seconds <= 3600:
            raise ValueError("timeout_seconds must be in 1..3600")
        if any(mode not in {"ro", "rw"} for _, _, mode in self.mounts):
            raise ValueError("mount mode must be ro or rw")


@dataclass(frozen=True)
class PiResult:
    operation_id: str
    model: str
    status: str
    trace_path: Path
    usage: dict[str, Any]
    stderr: str
    receipt_path: Path
    return_code: int | None
    wall_seconds: float
    timeout_seconds: int


@dataclass(frozen=True)
class ProviderProbe:
    operation_id: str
    model: str
    status: str
    content: str
    usage: dict[str, Any]
    attempts: int
    receipt_path: Path


SubprocessRunner = Callable[..., subprocess.CompletedProcess[str]]


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "[REDACTED]") if secret else text


def _load_usage(accounting: Path, trace: Path) -> dict[str, Any]:
    usage_path = accounting / "usage.json"
    if usage_path.is_file():
        try:
            value = json.loads(usage_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            value = {}
        if isinstance(value, dict):
            return value
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "turns": 0,
    }
    if trace.is_file():
        for line in trace.read_text(encoding="utf-8").splitlines():
            try:
                message = json.loads(line).get("message", {})
            except (json.JSONDecodeError, AttributeError):
                continue
            if message.get("role") != "assistant":
                continue
            item = message.get("usage") or {}
            totals["input_tokens"] += int(item.get("input", 0) or 0)
            totals["output_tokens"] += int(item.get("output", 0) or 0)
            totals["cache_read_tokens"] += int(item.get("cacheRead", 0) or 0)
            totals["cache_write_tokens"] += int(item.get("cacheWrite", 0) or 0)
            totals["total_tokens"] += int(item.get("totalTokens", 0) or 0)
            totals["turns"] += 1
    return totals


def run_pi(
    request: PiRequest,
    injected_subprocess_runner: SubprocessRunner = subprocess.run,
) -> PiResult:
    selected = resolve_model(request.provider, request.model)
    key = os.environ.get(selected.key_environment)
    if not key:
        raise RuntimeError(f"{selected.key_environment} is not set")
    output = request.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    accounting = output / "accounting"
    accounting.mkdir(exist_ok=True)
    runner_path = output / "pi_runner.mjs"
    runner_path.write_text(_PI_RUNNER, encoding="utf-8")
    models_path = write_pi_models(output / "models.json", selected)
    trace_path = output / "trace.jsonl"
    mounts = [
        "-v",
        f"{request.prompt_path.resolve()}:/input/prompt.txt:ro",
        "-v",
        f"{accounting.resolve()}:/accounting",
        "-v",
        f"{runner_path.resolve()}:/runtime/pi_runner.mjs:ro",
        "-v",
        f"{models_path.resolve()}:/root/.pi/agent/models.json:ro",
    ]
    for source, destination, mode in request.mounts:
        mounts.extend(("-v", f"{source.resolve()}:{destination}:{mode}"))
    command = [
        "docker",
        "run",
        "--rm",
        "--network=host",
        "-e",
        selected.key_environment,
        *mounts,
        "-w",
        "/workspace",
        request.image,
        "node",
        "/runtime/pi_runner.mjs",
        "--provider",
        selected.provider,
        "--model",
        selected.upstream_model,
        "--key-environment",
        selected.key_environment,
        "--max-turns",
        str(request.max_turns),
        "--allowed-tools",
        ",".join(request.allowed_tools),
        "--prompt-path",
        "/input/prompt.txt",
        "--accounting-dir",
        "/accounting",
    ]
    started = time.monotonic()
    return_code: int | None = None
    stdout = ""
    stderr = ""
    status = "error"
    try:
        completed = injected_subprocess_runner(
            command,
            capture_output=True,
            text=True,
            timeout=request.timeout_seconds,
            check=False,
        )
        return_code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        status = "completed" if return_code == 0 else "error"
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        status = "timeout"
    wall_seconds = round(time.monotonic() - started, 6)
    sanitized_stdout = _redact(str(stdout), key)
    sanitized_stderr = _redact(str(stderr), key)
    (output / "stdout.txt").write_text(sanitized_stdout, encoding="utf-8")
    (output / "stderr.txt").write_text(sanitized_stderr, encoding="utf-8")
    accounting_trace = accounting / "trace.jsonl"
    if not trace_path.exists() and accounting_trace.exists():
        shutil.copy2(accounting_trace, trace_path)
    usage = _load_usage(accounting, trace_path)
    estimated_cost = estimate_cost(selected, usage)
    receipt_path = output / "receipt.json"
    atomic_write_json(
        receipt_path,
        {
            "schema": "skillrace-pi-result/1",
            "operation_id": request.operation_id,
            "model": request.model,
            "provider": selected.provider,
            "qualified_model": qualified_model(selected),
            "upstream_model": selected.upstream_model,
            "status": status,
            "return_code": return_code,
            "timeout_seconds": request.timeout_seconds,
            "max_turns": request.max_turns,
            "allowed_tools": list(request.allowed_tools),
            "trace_path": str(trace_path),
            "usage": usage,
            "estimated_cost_usd": (
                str(estimated_cost) if estimated_cost is not None else "unpriced"
            ),
            "wall_seconds": wall_seconds,
            "stderr": sanitized_stderr[-1000:],
        },
    )
    return PiResult(
        operation_id=request.operation_id,
        model=request.model,
        status=status,
        trace_path=trace_path,
        usage=usage,
        stderr=sanitized_stderr,
        receipt_path=receipt_path,
        return_code=return_code,
        wall_seconds=wall_seconds,
        timeout_seconds=request.timeout_seconds,
    )


def _request_id_hash(headers: Any) -> str | None:
    for name in ("x-request-id", "request-id", "x-amzn-requestid"):
        value = headers.get(name) if headers is not None else None
        if value:
            return hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return None


def direct_provider_preflight(
    provider: str, model: str, evidence_dir: str | Path
) -> ProviderProbe:
    selected = resolve_model(provider, model)
    key = os.environ.get(selected.key_environment)
    if not key:
        raise RuntimeError(f"{selected.key_environment} is not set")
    output = Path(evidence_dir)
    output.mkdir(parents=True, exist_ok=True)
    operation_id = f"preflight.{uuid.uuid4().hex}"
    body = json.dumps(
        {
            "model": selected.upstream_model,
            "messages": [
                {
                    "role": "user",
                    "content": "Reply with exactly SKILLRACE_PREFLIGHT_OK.",
                }
            ],
            "temperature": 0,
            "max_tokens": 128,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    attempt_records: list[dict[str, Any]] = []
    content = ""
    usage: dict[str, Any] = {}
    status = "provider_error"
    for ordinal in range(2):
        started = time.monotonic()
        transient = False
        attempt: dict[str, Any] = {"ordinal": ordinal, "timeout_seconds": 60}
        try:
            request = urllib.request.Request(
                selected.base_url.rstrip("/") + "/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                attempt["http_status"] = int(response.status)
                attempt["request_id_sha256"] = _request_id_hash(response.headers)
                actual_cost = response.headers.get("x-litellm-response-cost")
                attempt["actual_cost_usd"] = (
                    str(actual_cost) if actual_cost is not None else None
                )
            value = json.loads(raw)
            provider_model = value.get("model")
            choices = value.get("choices")
            if not isinstance(provider_model, str) or not isinstance(choices, list) or not choices:
                raise ValueError("malformed provider response")
            message = choices[0].get("message", {})
            content = message.get("content") or ""
            if not isinstance(content, str) or "SKILLRACE_PREFLIGHT_OK" not in content:
                raise ValueError("preflight response content did not match")
            usage = dict(value.get("usage") or {})
            estimated_cost = estimate_cost(selected, usage)
            attempt["provider_model"] = provider_model
            attempt["response_id_sha256"] = (
                hashlib.sha256(str(value["id"]).encode("utf-8")).hexdigest()
                if value.get("id")
                else None
            )
            attempt["usage"] = usage
            attempt["estimated_cost_usd"] = (
                str(estimated_cost) if estimated_cost is not None else "unpriced"
            )
            attempt["status"] = "completed"
            status = "completed"
        except urllib.error.HTTPError as error:
            attempt["http_status"] = error.code
            attempt["status"] = "provider_error"
            transient = error.code == 429 or error.code >= 500
        except (urllib.error.URLError, TimeoutError):
            attempt["status"] = "provider_error"
            transient = True
        except (json.JSONDecodeError, ValueError) as error:
            attempt["status"] = "malformed_response"
            attempt["diagnostic"] = str(error)
            transient = True
        attempt["wall_seconds"] = round(time.monotonic() - started, 6)
        attempt_records.append(attempt)
        if status == "completed" or not transient:
            break
        if ordinal == 0:
            time.sleep(2)
    receipt_path = output / "preflight.json"
    atomic_write_json(
        receipt_path,
        {
            "schema": "skillrace-provider-probe/1",
            "operation_id": operation_id,
            "provider": selected.provider,
            "model": model,
            "qualified_model": qualified_model(selected),
            "upstream_model": selected.upstream_model,
            "status": status,
            "content": content,
            "usage": usage,
            "attempts": attempt_records,
        },
    )
    return ProviderProbe(
        operation_id=operation_id,
        model=model,
        status=status,
        content=content,
        usage=usage,
        attempts=len(attempt_records),
        receipt_path=receipt_path,
    )


def direct_yunwu_preflight(model: str, evidence_dir: str | Path) -> ProviderProbe:
    return direct_provider_preflight("yunwu", model, evidence_dir)
