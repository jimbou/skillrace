"""Minimal CloseAI (OpenAI-compatible) chat client — stdlib only.

Used by SkillRACE's *model-driven* steps (generation, and later the judgment steps).
These go DIRECT to the provider (not through pi), so temperature is controllable
(D-PI-1). The agent-under-test is the only thing that runs via pi.
"""
from __future__ import annotations
import datetime
import json
import os
import pathlib
import time
import urllib.request
import urllib.error

CLOSEAI_URL = "https://api.openai-proxy.org/v1/chat/completions"

# Permanent, append-only ledger of EVERY model call across all steps (generate / run
# / check). Override location with SKILLRACE_LEDGER; default is a stable home path.
LEDGER_PATH = os.environ.get("SKILLRACE_LEDGER",
                             os.path.expanduser("~/.skillrace/cost_ledger.jsonl"))


def log_usage(tag, model, in_tokens, out_tokens, skill=None):
    """Append one usage record to the permanent ledger; returns the priced cost."""
    pin, pout = PRICES.get(model, (0.0, 0.0))
    price = (in_tokens * pin + out_tokens * pout) / 1e6
    rec = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "tag": tag, "skill": skill, "model": model,
           "in": in_tokens, "out": out_tokens, "price_usd": round(price, 6)}
    try:
        p = pathlib.Path(LEDGER_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass
    return price

# USD per 1M tokens (input, output) — mirror images/pi-base/models.closeai.json.
PRICES = {
    "qwen3.5-flash": (0.024, 0.18),
    "qwen3.5-plus": (0.072, 0.45),
    "qwen3.6-flash": (0.144, 0.88),
    "deepseek-v4-flash": (0.15, 0.30),
    "glm-5": (0.48, 2.16),
}


def chat(messages, model="qwen3.6-flash", temperature=0.0, max_tokens=2048, retries=3,
         reasoning=True, tag="chat", skill=None):
    """One chat-completions call. Returns {content, usage, cost_usd, model}.

    reasoning=False disables the model's thinking (`enable_thinking: false`) — ~3x
    faster and much cheaper (reasoning tokens bill at the output rate). Use it for
    SkillRACE's own generation/judgment calls (we don't need their trace). The
    agent-under-test, which DOES need a reasoning trace, runs via pi, not here."""
    key = os.environ.get("CLOSE_API_KEY")
    if not key:
        raise RuntimeError("CLOSE_API_KEY not set in environment")
    payload = {
        "model": model, "messages": messages,
        "temperature": temperature, "max_tokens": max_tokens,
    }
    if not reasoning:
        payload["enable_thinking"] = False
    body = json.dumps(payload).encode()
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                CLOSEAI_URL, data=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            r = json.load(urllib.request.urlopen(req, timeout=180))
            msg = r["choices"][0]["message"]
            u = r.get("usage", {}) or {}
            pin, pout = PRICES.get(model, (0.0, 0.0))
            cost = (u.get("prompt_tokens", 0) * pin + u.get("completion_tokens", 0) * pout) / 1e6
            log_usage(tag, model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0), skill)
            return {"content": msg.get("content") or "", "usage": u, "cost_usd": cost, "model": model}
        except Exception as e:  # noqa: BLE001 — surface after retries
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"CloseAI chat failed after {retries} attempts: {type(last).__name__}: {last}")


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
