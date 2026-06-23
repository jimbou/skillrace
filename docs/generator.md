# Generator — produce test cases

> **Implementation** — the test-case generator (`gen_agent` / `generator.py`). Design context: [design/baselines.md](./design/baselines.md), [design/guard-synthesizer.md](./design/guard-synthesizer.md).

**Purpose:** given a skill, generate **K diverse `(prompt, env)` test cases** and
save them. That's all it does — it does **not** run anything and does **not** check
properties (those are the [Runner](./runner.md) and [Property Checker](./property-checker.md),
separate components). No behavioral feedback (the floor baseline / SkillRACE seed phase).

Implementation: `skillrace/gen_agent.py` (hybrid, the default) and
`skillrace/generator.py` (a faster non-agentic fallback). Provider client +
cost ledger: `skillrace/closeai.py`.

---

## Inputs / outputs

```bash
python -m skillrace.gen_agent --skill-dir skills/<skill> \
    --base skillrace/<skill>:base --k 3 --model qwen3.6-flash --out out/<skill>-gen
```

| Input | Meaning |
|-------|---------|
| `--skill-dir` | the skill to generate tests for (`SKILL.md` + `repo/`) |
| `--base` | the per-skill base image the envs build `FROM` |
| `--k` | how many test cases |
| `--model` | generation model (default `qwen3.6-flash`) |
| `--out` | where to write the cases |

It needs **no properties** — generation is independent of checking.

**Output:**

```
out/<skill>-gen/
  ideas.json                  # the K NL ideas
  cases/caseN/Dockerfile      # the env (FROM <base> + a test-specific tail)
  cases/caseN/candidate.json  # { summary, prompt, base_image, idea_index, skill, dockerfile }
  gen_trace.jsonl             # the agent's pi session (auditable)
  accounting.json             # model + total in/out tokens + price
```

`candidate.json` is the hand-off artifact the **Runner** later consumes.

---

## How it works — two model steps

**We propose diverse NL ideas; a pi agent realizes + builds each.**

### Step 1 — Propose (one direct call; controls diversity)

`PROPOSER_SYS` (verbatim, fixed for every skill):

```text
You design diverse TEST-CASE IDEAS for a coding-agent skill. Each idea is a
(task, environment) pair described in plain natural language — NOT code, NOT a
Dockerfile. **Derive the KIND of task and environment from the skill's stated
purpose in the provided SKILL.md — do not assume a domain.** The TASK is the kind of
thing to ask the agent to do that fits that purpose (illustrative only, across
skills: 'build an HTML landing page about <topic> using <tools>', 'fix the failing
tests', 'rebase this branch'). The ENVIRONMENT is the starting state the agent finds
(illustrative: 'an empty project', 'a repo using <framework> version X', 'a project
with this specific structure', 'a repo with <thing> in a broken state'). Each
environment must be a GENUINE, UNSOLVED starting point — the task must still need
doing. Explore VARIETY of tasks, tools, framework/versions, and structures.
```

Proposer **user** template (`«»` = placeholders):

```text
«SKILL_CONTEXT»                 ← SKILL.md (purpose) + the base's /workspace files

Already-covered ideas (make NEW ones clearly distinct):
«DIGEST»                        ← one-line summaries already produced (cross-run dedup)

Propose «K» NEW, diverse test-case ideas. Return ONLY a JSON array of «K» objects
with keys: "summary" (<=12 words), "task" (NL task to ask the agent), "env" (NL
description of the starting environment). No prose, no code fences.
```

- **`«SKILL_CONTEXT»`** = `SKILL.md` text + every file under `repo/`.
- **`«K»`**, **`«DIGEST»`** = batch size and the running list of prior summaries.

### Step 2 — Realize (the pi agent writes Dockerfiles, builds, self-verifies)

The skill is copied to `<out>/skill/`; the agent runs on the host with
`bash,read,write,edit`. Agent prompt template (verbatim, `«»` = placeholders):

```text
You are realizing a set of «K» TEST CASES for a coding-agent skill. The skill is in
./skill/ — read ./skill/SKILL.md for its purpose.

Here are «K» test IDEAS (each a task + an environment, in natural language):

«IDEAS»                         ← the K {summary, task, env} from step 1, numbered

For EACH idea N (1..«K»), produce a concrete test case = (prompt, env):
- prompt: the exact task to give the agent-under-test — faithful to idea N's task and
  the skill's purpose.
- env: a Dockerfile that begins EXACTLY with `FROM «BASE»` and then ADDS the starting
  state idea N's environment describes. The base already provides the toolchain, git,
  and a /workspace project — build ON it; do NOT add a second FROM. Create whatever
  the scenario needs with `RUN cat > /workspace/<path> <<'EOF' ... EOF` heredocs,
  version pins, repo state, etc.

Steps for each idea N:
  1. mkdir -p ./cases/case<N>
  2. Write ./cases/case<N>/Dockerfile (starts with `FROM «BASE»`).
  3. Build it: docker build -t skillrace-gen-case<N> ./cases/case<N>  — if it FAILS,
     read the error, fix the Dockerfile, rebuild until it builds.
  4. Confirm the env is a GENUINE, UNSOLVED starting point; adjust if needed.
  5. Write ./cases/case<N>/candidate.json with exactly:
     {"summary": "<=12 words", "prompt": "<the task>", "base_image": "«BASE»", "idea_index": <N>}
  Keep ONLY cases that build. When all are done, print a short numbered summary.
```

- **`«BASE»`** = the per-skill base image; **`«IDEAS»`** = the proposed ideas;
  **`«K»`** = count. The skill is read by the agent from `./skill/SKILL.md`.

---

## Cost

Both calls log to the permanent ledger (`~/.skillrace/cost_ledger.jsonl`, override
`$SKILLRACE_LEDGER`) with tags `generate.propose` and `generate.agent`
(`{ts, tag, skill, model, in, out, price_usd}`). `accounting.json` also summarizes
the run's totals (model confirmation + in/out tokens + price).

---

## Fallback: structured generator (`generator.py`)

A non-agentic alternative: same propose call, then a direct-model `realize`
(prompt + Dockerfile tail) + a model **repair** loop on build failure, parallelized.
~2× faster, finer control, but lower validity (~2/3 vs 3/3) without the agent's
self-verification. Same `candidate.json` output. Prompts (`REALIZER_SYS`,
`REPAIR_SYS`) live in `generator.py`; knobs: `--k --model --temperature
--build-retries --max-parallel --no-reasoning`.

---

## Skill-agnostic

The generator's code and prompts are fixed; it derives everything from `SKILL.md` +
`repo/`. Adding a skill needs no generator changes. Demonstrated identically on
`fix-failing-test` and `build-python-cli`.

Next in the pipeline: [Runner](./runner.md) runs a case; then
[Property Checker](./property-checker.md) checks it.
