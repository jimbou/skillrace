<a href="../README.md"><img src="../skillrace-icon.png" alt="SkillRACE" width="54" align="right"></a>

# Pipeline walkthrough — what runs today

A hands-on, copy-pasteable guide to the components that are **implemented now**, in order.
Worked end-to-end on the `mcp-server-patterns` skill. Each step reads files and writes
files, so you can stop, inspect, and resume anywhere.

```text
 build base ─▶ 1 generate ─▶ 2 run agent ─▶ 3 check properties
                                     │
                                     └─▶ 4 segment ─▶ 5 fold into tree
```

| Step | Module | Model? | Docker? | Writes |
|------|--------|--------|---------|--------|
| 0 build base | `docker build` | no | builds image | `skillrace/<skill>:base` |
| 1 generate | `skillrace.generator` | yes (propose+realize+repair) | builds per-case images | `out/<gen>/cand-*.json` (+ `ideas.json`) |
| 2 run agent | `skillrace.run_case` | the **agent under test** (pi) | runs container | `runs/<run>/` (trace, diff, run.json) |
| 3 check properties | `skillrace.check_properties` | yes (authors bash checks) | execs in the live container | `runs/<run>/verdicts.json`, `checks/*.sh` |
| 4 segment | `skillrace.segment_agent` | the segmenter agent (pi) | no | `runs/<run>/episodes.json` |
| 5 fold | `skillrace.tree` | yes (purpose-merge/broaden) | no | `out/skillrace/<skill>/tree.json` |

Prereqs: `CLOSE_API_KEY` set; Docker; `python3 -m skillrace.<x>` run from the repo root.
The judgment model throughout is `qwen3.6-flash` (the single ablation-axis model).

---

## A skill needs three files

```text
skills/<skill>/
  SKILL.md            # the guidance under test
  properties.json     # NL properties/invariants (the correctness spec)
  Containerfile.base  # the per-skill base env (FROM skillrace/pi-base, + skill + empty workspace)
```

`properties.json` is a list of `{id, reads:"state"|"trace", nl:"..."}`. See
`skills/mcp-server-patterns/properties.json` for an example.

---

## Step 0 — build the per-skill base image (once)

```bash
docker build -t skillrace/mcp-server-patterns:base \
  -f skills/mcp-server-patterns/Containerfile.base skills/mcp-server-patterns
```

This layers the skill on the shared `skillrace/pi-base` (Node 20 + git + pi). Built once;
every generated test case is `FROM` it.

---

## Step 1 — generate test cases (prompt + environment)

```bash
python3 -m skillrace.generator \
  --skill mcp-server-patterns \
  --skill-dir skills/mcp-server-patterns \
  --base skillrace/mcp-server-patterns:base \
  --n 2 --k 3 \
  --model qwen3.6-flash \
  --source random \
  --out out/mcp-gen
```

- `--k` = ideas proposed per batch; `--n` = candidates to keep. Each idea is REALIZED into
  a `(prompt, Dockerfile tail)` and **built** (with a model-repair loop); only buildable
  ones are saved. Extra builds in the last batch are kept too (not wasted).
- **Writes:** `out/mcp-gen/cand-<id>.json` (each has `prompt` + `containerfile`),
  `ideas.json` (the NL ideas), `generator_state.json`.

**Materialize each candidate into a case dir** (the runner expects `Dockerfile` +
`candidate.json` side by side):

```bash
python3 - <<'PY'
import json, pathlib
gen = pathlib.Path("out/mcp-gen"); cases = gen/"cases"; cases.mkdir(exist_ok=True)
for cj in sorted(gen.glob("cand-*.json")):
    d = json.load(open(cj)); cd = cases/d["candidate_id"]; cd.mkdir(exist_ok=True)
    (cd/"Dockerfile").write_text(d["containerfile"])
    (cd/"candidate.json").write_text(json.dumps(d, indent=2))
    print("case:", d["candidate_id"], "->", d["provenance"]["summary"])
PY
```

---

## Step 2 — run the agent under test in a container

```bash
python3 -m skillrace.run_case \
  --case out/mcp-gen/cases/cand-2ad8d3c60be3 \
  --model qwen3.6-flash \
  --out runs/mcp-tools-resources
```

Builds the case's env image, starts a long-lived container, runs `pi … --skill …` on the
prompt, and captures everything. The container is **left alive** for Step 3 (a detached
timebomb removes it after `--cleanup-grace`, default 30 min, if the checker doesn't).

- `--wall-clock` (default 1800s) hard-caps the agent; `--cleanup-grace` (default 1800s).
- **Writes:** `runs/<run>/raw/session.jsonl` (the trace), `logs/workspace.diff` (what the
  agent changed), `run.json` (manifest incl. the live `container` name), `cost.json`.

---

## Step 3 — check the properties (in the same container)

```bash
python3 -m skillrace.check_properties \
  --run runs/mcp-tools-resources \
  --props skills/mcp-server-patterns/properties.json \
  --model qwen3.6-flash
```

For each NL property the model **writes a bash script** that tests it; the script runs
inside the run's container (which also has `/check/trace.jsonl` + `/check/workspace.diff`)
and its **exit code is the verdict** (0 = holds, non-zero = violated). The checker **owns
teardown** — it destroys the container when done (pass `--keep-container` to inspect).

- **Writes:** `runs/<run>/checks/<id>.sh` (the authored checks) and `verdicts.json`.

```text
[✓ holds] uses-official-mcp-sdk
[✗ VIOLATED] builds-clean   ← inspect checks/builds-clean.sh to see exactly what failed
...
```

> Steps 4–5 are independent of Step 3 (they only need the trace), so you can run them in
> parallel with, or instead of, property checking.

---

## Step 4 — segment the run into episodes

```bash
python3 -m skillrace.segment_agent \
  --run runs/mcp-tools-resources \
  --model qwen3.6-flash
```

Deterministically renders `raw/session.jsonl` into a `simplified_trace.txt` (flat,
numbered tool calls; reasoning inline; long outputs truncated) with a target episode
count, then a **pi agent** (given the baked-in few-shot example) splits it into episodes
with summaries; a deterministic assembler validates the spans and attaches each episode's
`opening_reasoning`.

- **Writes:** `runs/<run>/episodes.json` — a list of
  `{index, start_call, end_call, intent, what_it_did, outcome, opening_reasoning}`.
- Fallback: `python3 -m skillrace.segment --run …` is a single-call version (no agent),
  useful if the agent API times out on long traces.

---

## Step 5 — fold the run into the global behavior tree

```bash
python3 -m skillrace.tree \
  --episodes runs/mcp-tools-resources/episodes.json \
  --session  runs/mcp-tools-resources/raw/session.jsonl \
  --tree     out/skillrace/mcp-server-patterns/tree.json \
  --skill    mcp-server-patterns
```

Folds the run's episode line into the global tree: each episode is matched **by purpose**
against the current node's children — same purpose ⇒ merge (broaden the `intent`, record a
distinct `what_it_did` variant); new purpose ⇒ new node; the rest of the line grafts on. The
run id is derived from the run dir (`runs/mcp-tools-resources` → `mcp-tools-resources`);
override with `--run-id`.

- **Writes:** `out/skillrace/<skill>/tree.json` (nodes + members + per-run edges + run
  registry) and `tree.cache.json` (the cached merge verdicts).
- First run ⇒ the line *is* the tree. Run it again pointing at the **same** `--tree` to
  fold more runs in; shared prefixes merge, divergences branch.

```bash
# fold a second run into the SAME tree
python3 -m skillrace.tree \
  --episodes runs/mcp-transport-http/episodes.json \
  --session  runs/mcp-transport-http/raw/session.jsonl \
  --tree     out/skillrace/mcp-server-patterns/tree.json \
  --run-id   mcp-transport-http
```

The printout shows the tree; a node tagged `(runs: A,B)` is shared by two runs, and a node
with >1 child is a `«BRANCH»` — the point where the runs diverged.

---

## Full example (one skill, three runs)

```bash
SKILL=mcp-server-patterns
TREE=out/skillrace/$SKILL/tree.json

# 0. base (once)
docker build -t skillrace/$SKILL:base -f skills/$SKILL/Containerfile.base skills/$SKILL

# 1. generate + materialize cases  (see Step 1 for the materialize snippet)
python3 -m skillrace.generator --skill $SKILL --skill-dir skills/$SKILL \
  --base skillrace/$SKILL:base --n 2 --k 3 --model qwen3.6-flash --out out/mcp-gen

# 2–5 per case:
for CASE in cand-AAA cand-BBB; do
  RUN=runs/$CASE
  python3 -m skillrace.run_case   --case out/mcp-gen/cases/$CASE --model qwen3.6-flash --out $RUN
  python3 -m skillrace.check_properties --run $RUN --props skills/$SKILL/properties.json --model qwen3.6-flash
  python3 -m skillrace.segment_agent --run $RUN --model qwen3.6-flash
  python3 -m skillrace.tree --episodes $RUN/episodes.json --session $RUN/raw/session.jsonl \
    --tree $TREE --run-id $CASE --skill $SKILL
done
```

---

## Where things land

```text
out/<gen>/cand-*.json, ideas.json, cases/<id>/       # Step 1
runs/<run>/raw/session.jsonl, logs/workspace.diff,   # Step 2
          run.json, cost.json
runs/<run>/checks/*.sh, verdicts.json                # Step 3
runs/<run>/simplified_trace.txt, episodes.json       # Step 4
out/skillrace/<skill>/tree.json, tree.cache.json     # Step 5
~/.skillrace/cost_ledger.jsonl                        # every model call, all steps
```

## The assembled loop (one command per campaign)

Everything above is now orchestrated by `skillrace.loop` — one method, one skill,
one budget of agent runs. All three rungs share the seed generator, the runner,
and the property checks; only test generation differs:

```bash
python -m skillrace.loop --method skillrace \
    --skill fix-failing-test --skill-dir skills/fix-failing-test \
    --base skillrace/fix-failing-test:base \
    --props skills/fix-failing-test/properties.json \
    --budget 20 --seed-count 6 --out out/campaign/skillrace/fix-failing-test
# --method random | greybox (--greybox-level L0|L1|L2) | skillrace
```

New / changed components:

| Piece | Module | What it does |
|-------|--------|--------------|
| pre-run checks | `skillrace.compile_checks` | authors each property's bash check per CASE from (prompt + built-E0 probe) **before any agent run**; stored at `<case>/checks/`; byte-identical across methods |
| fixed core | `skillrace.fixed_checks` | universal invariants (force-push, destructive rm, repetition, budget) — pure Python, zero model |
| checker | `skillrace.check_properties` | now EXECUTES precompiled checks + fixed core; post-hoc authoring only behind `--author-post-hoc` |
| guards (C5) | `skillrace.guards` | branch → guard (outcome + opening-reasoning signals, disagreement flags) → property-guided frontier selection → synthesis → **agent-free validation** in the built container |
| greybox rung | `skillrace.greybox` | VeriGrey feedback/energy/scheduling verbatim over schematized tool events (L0/L1/L2); see `docs/design/greybox-verigrey-adaptation.md` |
| loop | `skillrace.loop` | seed phase + explore phase; per-iteration record in `campaign.json` incl. violations and (skillrace) divergence classification |
| skill eval | `skillrace.skill_eval` / `skillrace.revise_skill` | hidden-test scenario harness + the condition-blind skill reviser (claim 2) |

## Not yet wired (recorded, not silently dropped)

k=3 reproducibility regrade of flagged violations; the injected-violation
detection-rate harness; segmentation/merge calibration sets; cross-prefix tree
merge measurement. See `docs/build-plan.md`.
