# Runner — run one test case

> **Implementation** — the `run_case` command. Design spec: [design/runner.md](./design/runner.md).

**Purpose:** take **one** generated test case and do the actual run — build its env
image and run the **agent under test** (the skill, baked into the base) on the case's
prompt, capturing the trace + cost + a workspace diff, and **leaving the container
running** for the Property Checker. That's all — it does **not** generate cases
([Generator](./generator.md)) and does **not** check properties
([Property Checker](./property-checker.md)).

Implementation: `skillrace/run_case.py`. (Design rationale for the Runner role:
[design/runner.md](./design/runner.md).)

---

## Inputs / outputs

```bash
python -m skillrace.run_case --case out/<skill>-gen/cases/case2 \
    --skill-dir skills/<skill> \
    --model glm-4.5-flash --out runs/<skill>-case2
```

| Input | Meaning |
|-------|---------|
| `--case` | a case dir from the generator (`Dockerfile` + `candidate.json`) |
| `--skill-dir` | trusted host skill directory, mounted read-only at `/trusted-skill` |
| `--model` | the **agent-under-test** model (default `glm-4.5-flash`) |
| `--out` | the run directory to write |
| `--wall-clock` | timeout (seconds) before the container is killed |

The skill name and prompt come from `candidate.json`; the skill content itself always
comes from the explicit read-only host mount, never from the candidate image.

**Output (a run dir):**

```
runs/<skill>-case2/
  run.json            # skill, prompt, base/env image, container name, model, termination
  raw/session.jsonl   # the trace (reasoning + tool calls + observations)
  logs/workspace.diff # what the agent changed (git diff)
  cost.json           # turns + in/out tokens + price
  agent_stdout.txt
```

`run.json.container` is the **still-running** container the agent left — the
[Property Checker](./property-checker.md) `docker exec`s its state checks into that
exact container (most faithful), then destroys it. There is **no `docker commit`**.

---

## How it works

1. **Build** the env image from `case/Dockerfile` (base layers cached; only the tail
   rebuilds).
2. **Run the agent under test** inside the container, under a wall-clock timeout, on
   host networking, key injected at runtime. The skill is baked into the base at
   `/skills/<skill>`. Invocation (run inside the container; `«»` = placeholders):

   ```text
   cd /workspace && git add -A && git commit -q -m "skillrace: pre-agent baseline" || true;
   pi --provider yunwu --model «MODEL» --print \
      --session /logs/session.jsonl --skill /skills/«SKILL» "$PI_PROMPT" </dev/null;
   cd /workspace && git add -A && git diff --cached HEAD > /logs/workspace.diff
   ```

   - **`«MODEL»`** = agent-under-test model; **`«SKILL»`** = skill name (from
     `candidate.json`); **`$PI_PROMPT`** = the case's `prompt` (passed via `-e`).
   - The **pre-agent baseline commit** makes the post-run `git diff` show exactly what
     the agent changed (used by trace/integrity checks).
3. **Leave the container running.** The container is started detached
   (`docker run -d … sleep infinity`) and the agent runs inside it via `docker exec`,
   so when the agent finishes the container stays **alive** (recorded as
   `run.json.container`) for the Property Checker to `exec` state checks into. No
   `docker commit`. On timeout: kill + remove (no live container).
4. **Leave a timebomb.** A detached process (`start_new_session`, survives this
   process exiting) force-removes the container + env image after
   `--cleanup-grace` seconds (default 300) **if the checker hasn't** — so a missing
   or hung checker can't leak the container. The checker's prompt cleanup normally
   beats it (making the `docker rm -f` a no-op).
5. **Capture** the session trace → `raw/session.jsonl`, the diff, and `cost.json`.

---

## Cost

The agent-under-test run logs to the permanent ledger
(`~/.skillrace/cost_ledger.jsonl`) with tag `run.agent`
(`{ts, tag, skill, model, in, out, price_provider_credits}`); the same totals are in `cost.json`.

---

## Decoupling

The Runner is its own command: generate cases whenever, run any case later, any
number of times, with whatever model/out you choose. It only reads a case dir and
writes a run dir — file contracts in, file contracts out. The
[Property Checker](./property-checker.md) consumes the run dir afterward.
