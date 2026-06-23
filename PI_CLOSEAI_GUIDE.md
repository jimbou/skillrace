# Using `pi` with CloseAI — tiny guide

CloseAI is an OpenAI-compatible proxy. `pi` (`@mariozechner/pi-coding-agent`) talks to it
as a custom provider.

## 1. Setup (one time)

Export your key:
```bash
export CLOSE_API_KEY=sk-...        # put in ~/.bashrc to persist
```

Add the provider to `~/.pi/agent/models.json`. **The `apiKey` is the bare env-var name —
NOT `$CLOSE_API_KEY`** (the `$` form makes pi send the literal string → `401`):
```jsonc
{
  "providers": {
    "closeai": {
      "baseUrl": "https://api.openai-proxy.org/v1",
      "api": "openai-completions",
      "apiKey": "CLOSE_API_KEY",
      "authHeader": true,
      "models": [
        { "id": "qwen3.5-flash", "name": "Qwen 3.5 Flash", "reasoning": true,
          "input": ["text"], "contextWindow": 128000, "maxTokens": 8192,
          "cost": { "input": 0.024, "output": 0.18, "cacheRead": 0, "cacheWrite": 0 } }
        // add more model objects here (same shape, change id/cost)
      ]
    }
  }
}
```
Check it loaded: `pi --list-models qwen` (should list `closeai  qwen3.5-flash`).

## 2. Run it

Interactive:
```bash
pi --provider closeai --model qwen3.5-flash
```

Non-interactive / scripted (**must redirect stdin or it hangs**):
```bash
pi --provider closeai --model glm-5 --print --session /tmp/s.jsonl "your task" </dev/null
```

Useful flags: `--print` (one-shot), `--session <file>` (save the run as JSONL),
`--tools bash,read` (limit tools), `--no-tools` (chat only).

## 3. Which models to use

All of these expose a **reasoning trace** (verified end-to-end: each agent decision gets a
`thinking` block in the session, from the model's `reasoning_content`). Cheapest first
(USD / 1M tokens, in/out):

| Model | in | out | Notes |
|---|---|---|---|
| **qwen3.5-flash** | 0.024 | 0.18 | cheapest; great default |
| qwen3.5-plus | 0.072 | 0.45 | a bit stronger |
| qwen3.6-flash | 0.144 | 0.88 | newer |
| deepseek-v4-flash | 0.15 | 0.30 | DeepSeek reasoning |
| **glm-5** | 0.48 | 2.16 | most reliable; pi/skillprobe default |

**Do NOT use if you need the trace** (they hide reasoning — no `thinking` block in pi):
`gemini-*`, OpenAI `o4-mini` / `gpt-5.x`, `kimi-k2.5`. `deepseek-reasoner` is offline (400).

Rule of thumb: **qwen3.5-flash** for cheap+traceable, **glm-5** for reliability.

## 4. See the reasoning trace

The session JSONL holds it. Each `assistant` message has content blocks:
`thinking` (reasoning) → `toolCall` (action) → then a `toolResult`.
```bash
grep -c '"type":"thinking"' /tmp/s.jsonl          # how many reasoning blocks
python3 ~/test_models/show_traces.py /tmp/s.jsonl  # pretty per-decision view
```
A `thinking` block tagged `"thinkingSignature":"reasoning_content"` = the trace came through.

## 5. Gotchas
- `apiKey` in models.json = **bare env-var name** (`CLOSE_API_KEY`), no `$`.
- Non-interactive `pi --print` **hangs without `</dev/null`**.
- `"reasoning": true` in models.json is just a hint — the trace only appears if the model
  actually returns `reasoning_content` (Qwen / GLM / DeepSeek families do).
- Reasoning models are slower and bill many trace tokens at the **output** rate.
- Base URL `api.openai-proxy.org/v1` is canonical; `api.closeai-asia.com/v1` also works.
