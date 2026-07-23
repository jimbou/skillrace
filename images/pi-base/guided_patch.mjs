#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const accountingDir = process.env.PI_ACCOUNTING_DIR || "/accounting";
fs.mkdirSync(accountingDir, { recursive: true });
const eventPath = path.join(accountingDir, "guided-events.jsonl");
const summaryPath = path.join(accountingDir, "guided-summary.json");

function record(event) {
  fs.appendFileSync(eventPath, `${JSON.stringify(event)}\n`, "utf8");
}

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`missing required environment variable: ${name}`);
  return value;
}

function resolveToolPath(value) {
  if (typeof value !== "string" || value.length === 0) return "";
  return path.resolve("/workspace", value);
}

async function loadPiSdk() {
  const candidates = [
    "/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/dist/index.js",
    "/usr/local/lib/node_modules/@mariozechner/pi-coding-agent/dist/index.js",
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return import(pathToFileURL(candidate).href);
  }
  throw new Error("the pinned Pi SDK is not installed in a known global location");
}

let session;
let turnCount = 0;
let toolCallCount = 0;
let mutationCount = 0;
let mutationSucceeded = false;
let blockedCallCount = 0;
let requiredReads;

try {
  const provider = required("PI_PROVIDER");
  const modelId = required("PI_MODEL");
  const skillPath = path.resolve(required("PI_REPAIR_SKILL_PATH"));
  const contextPath = path.resolve(required("PI_REPAIR_CONTEXT_PATH"));
  const prompt = fs.readFileSync(required("PI_REPAIR_PROMPT_PATH"), "utf8");
  const systemPrompt = fs.readFileSync(required("PI_SYSTEM_PROMPT_PATH"), "utf8");
  const thinkingLevel = process.env.PI_THINKING_LEVEL || "medium";
  const maxTurns = Number.parseInt(process.env.PI_MAX_TURNS || "10", 10);
  const allowedTools = required("PI_ALLOWED_TOOLS").split(",").filter(Boolean);
  const expectedTools = ["read", "grep", "edit", "write"];
  if (allowedTools.join(",") !== expectedTools.join(",")) {
    throw new Error("guided repair tool set must be read,grep,edit,write");
  }
  if (!Number.isInteger(maxTurns) || maxTurns < 2 || maxTurns > 12) {
    throw new Error("PI_MAX_TURNS must be an integer in 2..12");
  }

  requiredReads = new Set([skillPath, contextPath]);
  const readablePaths = new Set([skillPath, contextPath]);
  const {
    AuthStorage,
    createAgentSession,
    DefaultResourceLoader,
    ModelRegistry,
    SessionManager,
    SettingsManager,
  } = await loadPiSdk();

  const authStorage = AuthStorage.create("/pi-home/auth.json");
  const providerKey = process.env.yunwu_key;
  if (providerKey) authStorage.setRuntimeApiKey(provider, providerKey);
  const modelRegistry = ModelRegistry.create(authStorage, "/pi-home/models.json");
  const model = modelRegistry.find(provider, modelId);
  if (!model) throw new Error(`model not found in isolated catalog: ${provider}/${modelId}`);

  const settingsManager = SettingsManager.inMemory({
    compaction: { enabled: false },
    retry: { enabled: true, maxRetries: 1, baseDelayMs: 2000 },
  });

  // Pi 0.73.1 accepts bare inline factories. Newer Pi releases additionally
  // accept named {name, factory} wrappers, but the experiment image is pinned.
  const repairPolicy = (pi) => {
      const block = (reason) => {
        blockedCallCount += 1;
        record({ type: "tool_blocked", reason });
        return { block: true, reason };
      };

      pi.on("tool_call", (event) => {
        toolCallCount += 1;
        const toolPath = resolveToolPath(event.input?.path);
        record({ type: "tool_call", tool: event.toolName, path: toolPath || null });

        if (mutationCount > 0) {
          return block("The one repair mutation was already submitted; stop now.");
        }
        if (event.toolName === "read") {
          if (!readablePaths.has(toolPath)) {
            return block("Read only the required skill and repair-context files.");
          }
          if (!requiredReads.has(toolPath)) {
            return block("That input was already read; read the other required input now.");
          }
          return;
        }
        if (event.toolName === "grep" && requiredReads.size > 0) {
          return block("Read both required files directly before using search.");
        }
        if (event.toolName === "grep") {
          if (!readablePaths.has(toolPath)) {
            return block("Search only the required skill and repair-context files.");
          }
          return;
        }
        if (event.toolName === "edit" || event.toolName === "write") {
          if (requiredReads.size > 0) {
            return block("Read both required files completely before editing.");
          }
          if (toolPath !== skillPath) {
            return block("Only /workspace/SKILL.md may be changed.");
          }
          mutationCount += 1;
          return;
        }
        return block("Tool is outside the guided repair policy.");
      });

      pi.on("tool_result", (event) => {
        const toolPath = resolveToolPath(event.input?.path);
        let resultUpdate;
        if (event.toolName === "read" && !event.isError && readablePaths.has(toolPath)) {
          const pendingBefore = requiredReads.size;
          requiredReads.delete(toolPath);
          if (pendingBefore > 0 && requiredReads.size === 0) {
            pi.setActiveTools(["edit", "write"]);
            resultUpdate = {
              content: [
                ...event.content,
                {
                  type: "text",
                  text: "Both required inputs are now complete; make the single SKILL.md edit now.",
                },
              ],
            };
          }
        }
        if (
          (event.toolName === "edit" || event.toolName === "write")
          && !event.isError
          && toolPath === skillPath
        ) {
          mutationSucceeded = true;
        }
        record({
          type: "tool_result",
          tool: event.toolName,
          path: toolPath || null,
          error: Boolean(event.isError),
        });
        return resultUpdate;
      });

      pi.on("after_provider_response", (event) => {
        record({ type: "after_provider_response", status: event.status });
      });
  };

  const resourceLoader = new DefaultResourceLoader({
    cwd: "/workspace",
    agentDir: "/pi-home",
    settingsManager,
    extensionFactories: [repairPolicy],
    noExtensions: true,
    noSkills: true,
    noPromptTemplates: true,
    noThemes: true,
    noContextFiles: true,
    systemPrompt,
  });
  await resourceLoader.reload();

  const created = await createAgentSession({
    cwd: "/workspace",
    agentDir: "/pi-home",
    model,
    thinkingLevel,
    authStorage,
    modelRegistry,
    resourceLoader,
    tools: allowedTools,
    sessionManager: SessionManager.create("/workspace", accountingDir),
    settingsManager,
  });
  session = created.session;
  if (created.extensionsResult.errors.length) {
    throw new Error(
      `Pi extension diagnostics: ${JSON.stringify(created.extensionsResult.errors)}`,
    );
  }

  session.subscribe((event) => {
    if (event.type === "turn_end") {
      turnCount += 1;
      record({ type: "turn_end", turn: turnCount });
      if (turnCount >= maxTurns && session.isStreaming) void session.abort();
    } else if (event.type === "agent_end") {
      record({ type: event.type });
    }
  });

  await session.prompt(prompt);
  if (!mutationSucceeded) {
    throw new Error("Pi finished without one successful SKILL.md mutation");
  }
  fs.writeFileSync(summaryPath, JSON.stringify({
    status: "completed",
    turn_count: turnCount,
    tool_call_count: toolCallCount,
    mutation_count: mutationCount,
    required_reads_remaining: requiredReads.size,
    blocked_call_count: blockedCallCount,
  }, null, 2));
  session.dispose();
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  fs.writeFileSync(summaryPath, JSON.stringify({
    status: "error",
    error: message.slice(-500),
    turn_count: turnCount,
    tool_call_count: toolCallCount,
    mutation_count: mutationCount,
    required_reads_remaining: requiredReads ? requiredReads.size : null,
    blocked_call_count: blockedCallCount,
  }, null, 2));
  if (session) session.dispose();
  console.error(`guided Pi repair failed: ${message}`);
  process.exitCode = 1;
}
