# D1 skill-suite decision log

> Append-only decision log for the bug-finding suite (D1), produced by applying
> [`docs/dataset-protocol.md`](../docs/dataset-protocol.md) §3 criteria to candidates
> from the fixed sources §2. Each row records the verdict **and its reason**, decided
> from the skill's `SKILL.md`/repo **before** any \tool run. Contingency
> (high/med/low) is *recorded, not used for inclusion* (protocol §3).
>
> Mined 2026-07-05 from S1 (`anthropics/skills`) and S4 (awesome-* aggregators).
> **S2/S3 (GitHub code-search crawl) enumeration is still pending** — it needs an
> authenticated GitHub API sweep, not doable from a plain web fetch; the funnel below
> is therefore partial and will grow.

## Pre-headline boundary correction (2026-07-11; before headline campaigns)

The quantitative headline is now the complete set of **22 prepared, redistributable
public skills**, selected as the **first 22 candidates** that passed the protocol in the
same fixed popularity-driven candidate order. The four original in-repository skills
(`build-python-cli`, `fix-failing-test`, `frontent-design`, and
`mcp-server-patterns`) are development-only because they were used while building and
piloting the system. `frontent-design` also remains outside the code-behavior boundary
because its strongest success criteria are presentational proxies.

This correction is independent of performance: no original development skill can
enter the headline, and public removals are limited to three pre-result artifact-license
failures (`cli-typer-scripts`, `json-serialization`, `json-tools`). Their content is
not redistributed. The already-admitted S4 candidate `condition-based-waiting` was
prepared from a historical MIT snapshot, producing the first 22 headline skills. It is
medium contingency: two blind baseline exercises already chose valid synchronization, while a
fresh skill-enabled stale-state exercise followed the skill and passed 100 repetitions.

The boundary is enforced by `experiments/manifests/rq1-skills.draft.json`,
`experiments/manifests/third-party-skills.json`, `skillrace.d1_audit`, and
`skillrace.third_party_audit`. All 22 headline images pass strict runtime smoke.

The same inclusion/exclusion protocol and candidate order remain active. This log is not
closed until we append 8 additional qualifying public skills to reach a 30-skill target.

## Funnel (so far)

| Stage | Count |
|-------|-------|
| Candidates examined (S1 + S4, distinct task families) | 30 |
| Admitted to D1 (code-behavior only) | 11 |
| Excluded — presentational/document artifact (X1, refined) | 4 |
| Excluded — subjective success (X1) | 11 |
| Excluded — not published / wishlist entry | 4 |
| Excluded — duplicate task family (X3) | 3 |
| S5 (skillsmp) crawl | 628 candidates → triaged; see below |

Target suite size is \~30 (protocol §4). The S1+S4 yield is 11 admitted code-behavior
families; the **S5 (skillsmp) crawl** supplies the rest (a large pool of coding skills,
triaged in `D1-proposed-admits.md`). No skill is added or dropped after results are seen.

## Criterion refinement (2026-07-05): code-behavior, not presentational artifacts

D1 tests **coding** skills, so I1 (mechanically checkable artifact) is read strictly:
the artifact must be **code whose behavior is checkable** — a suite that passes, a CLI
that computes a value, a query that returns the right answer, a build that succeeds. A
skill whose deliverable is a **document** (a Word file, a slide deck, a PDF layout) is
**excluded under X1**: its success is presentational (formatting, layout, "does it look
professional"), and a script can only check shallow structure ("valid OOXML, contains
the word 'Invoice'"), which does not capture whether the skill did its job. This
retroactively excludes the document-generation family below and is the same reason
`frontend-design` is only a borderline, low-contingency member.

## Admitted (code-behavior skills)

| Skill | Source | Artifact oracle | Contingency | Note |
|-------|--------|-----------------|:-----------:|------|
| `fix-failing-test` | S4/repo | previously-failing suite passes; tests unedited | high | prepared + built |
| `build-python-cli` | repo | CLI runs and produces the correct value | high | prepared + built |
| `mcp-server-patterns` | S1 | project builds; `tsc --noEmit` clean; server answers | high | prepared + built |
| `test-driven-development` | S4 | final suite passes; test-before-impl visible in trace | high | vendored + built |
| `regex-expert` | S5 | validator accepts/rejects exactly per spec; anchored | med | vendored + built |
| `sql-queries` | S5 | query returns the correct answer on a SQLite DB | high | vendored + built |
| `frontend-design` | S1 | renderable artifact; viewport/responsive floor | **low** | checkable *floor* only; borderline (see refinement) |
| `systematic-debugging` | S4 | previously-failing suite passes | high | vendor+build pending |
| `using-git-worktrees` | S4 | expected worktree/branch git state | med | reversibility properties bite; pending |
| `finishing-a-development-branch` | S4 | branch merged; clean history; no failing tests committed | high | pending |
| `condition-based-waiting` | S4 | async tests pass without flakiness (repeated runs agree) | med | borderline; pending |

7 built + 4 pending vendor/build. The S5 pool (`D1-proposed-admits.md`) supplies more
code-behavior candidates (debugging, refactoring, config-parsing, API-calling, …).

## Excluded, with reason

**Presentational / document artifact (X1) — success is subjective, weak oracle
(refined criterion above):** `docx`, `pdf`, `pptx`, `xlsx` — the deliverable is a
document whose quality is not mechanically checkable beyond shallow structure. (A narrow
*computational* xlsx task — "compute these formula values" — could be admitted as a
data-processing skill, but the general document-authoring skill is excluded.)

**Subjective success (X1) — no trustworthy mechanical oracle:**

**Subjective success (X1) — no trustworthy mechanical oracle:**
`algorithmic-art`, `brand-guidelines`, `canvas-design`, `doc-coauthoring`,
`internal-comms`, `theme-factory`, `slack-gif-creator` (visual content),
`testing-anti-patterns`, `requesting-code-review`, `receiving-code-review`,
`brainstorming`/`writing-plans`/`executing-plans` (planning prose).

**Not published — wishlist entries in the aggregator (cannot mine what does not exist):**
`api-development`, `database-migration`, `refactoring-patterns`, `security-review`
(and the other `(community-needed)` rows). *Note:* several of these — `sql-query-builder`,
`csv-processing`, `data-visualization` — are excellent **checkable** families, so they
are used to author **D2** scenarios (which we build) rather than D1 (which we mine).

**Duplicate task family (X3):**
`web-artifacts-builder`/`artifacts-builder` (dup of `frontend-design`),
`root-cause-tracing` (dup of `systematic-debugging`), `mcp-builder` counted once
across S1/S4.

**Credentials/external service (X2):** `claude-api` (needs a paid API key).

**Meta / thin (X3 / subjective):** `skill-creator`, `template-skill`, `writing-skills`,
`sharing-skills`, `verification-before-completion`, `defense-in-depth` (too thin to
segment into contingent branches, or success not checkable).

## Prepared & build-verified (offline)

Fully prepared skill directories (SKILL.md + `Containerfile.base` + `properties.json` +
`applicability.json`) whose base image **builds offline** against
`skillrace/skillgen-base` (python3 + pytest + git + pi) or their own base:

- `fix-failing-test` — base image built ✓
- `build-python-cli` — base image built ✓
- `mcp-server-patterns` — base image built ✓ (needs npm at build; already built)
- `frontent-design` — base image built ✓
- `test-driven-development` — vendored 2026-07-05 from `obra/superpowers` (abridged;
  see `PROVENANCE.md`), harness authored, base image built ✓
- `regex-expert` — **vendored verbatim** from `RightNow-AI/openfang` (via skillsmp S5),
  harness authored, base image built ✓
- `sql-queries` — **vendored verbatim** from `anthropics/knowledge-work-plugins` (via
  skillsmp S5), SQLite-subset harness authored, base image built ✓

**Final suite: 28 skills authored (24 base-build-verified offline + 4 build-deferred
for pip/npm deps).** Full grouped list in `candidates/D1-final-suite.md`. Built via the
instances-per-family strategy (§ user decision): cli ×3, refactor ×3, sql ×4, unit-test
×2, parser ×2, plus singleton families. The fixed-invariant catalog and applicability
matrices are in `skills/INVARIANTS.md` and each `skills/*/applicability.json`.

**Honest ceiling note:** the skillsmp pool cleanly yields ~28 *distinct* code-behavior
skills; beyond that it is dominated by forks (e.g. many identical superpowers-derived
TDD copies, some translated) and tool-coupled/presentational entries, which we did NOT
admit. Reaching a literal 30+ would require broadening sources (direct GitHub
`.claude/skills` crawl, other marketplaces) rather than padding with near-duplicates.

### skillsmp (S5) crawl + triage (done 2026-07-05)

- `skillrace.crawl_skillsmp` pulled **628 unique candidates** (OpenClaw excluded) →
  `candidates/skillsmp-pool.{md,json}`.
- `skillrace.triage_candidates` tagged them (verdict + project-coupled + dedup) →
  `candidates/skillsmp-triaged.md`; a balanced 36-across-12-families proposal is in
  `candidates/D1-proposed-admits.md` (proposal — confirm per §3).
- Sample logged decisions from vendoring: `regex-expert`, `sql-queries` **admitted**
  (portable, checkable, built); `refactor-safely` **excluded** — X (tool-coupled: needs
  the source repo's custom `refactor_tool`/knowledge-graph MCP tools, not portable);
  `log-parser` (agentscope) **deferred** — ships bundled `scripts/` that must be vendored
  with it before it is a faithful test subject.

## Remaining to finalize D1 (network-gated — cannot complete offline)

1. **Crawl to ~30** (S2/S3): GitHub code-search API sweep — needs an authenticated
   GitHub token; not doable offline.
2. **Dep-heavy admitted skills** (`docx`, `pdf`, `pptx`, `xlsx` → `pip`; `mcp-builder`,
   `webapp-testing` → `npm`/Playwright): their bases need package installs that require
   network. Author their harness now; build-verify when network is available.
3. **Vendor remaining real SKILL.md** for the S4 process/git skills
   (`systematic-debugging`, `using-git-worktrees`, `finishing-a-development-branch`,
   `condition-based-waiting`) from `obra/superpowers`; these build offline once vendored.
4. Tag the frozen set; record any `build-failed` removals.

**Status:** D1 currently has **5 prepared, build-verified skills**; the path to the full
suite is documented above and blocked only by network access, not by design.
