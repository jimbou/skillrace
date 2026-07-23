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

## Pre-headline suite closure (2026-07-12; before headline campaigns)

The quantitative headline draft now contains **30 prepared, redistributable public
skills**. The historical 22-skill suite is preserved exactly as the balanced pre-result
boundary recorded on July 11. The surviving July 5 records do not establish that those
22 were a literal prefix of the later frozen S5 popularity array, so the artifact does
not make that stronger claim. Four development-used fixtures (`build-python-cli`,
`fix-failing-test`, `frontent-design`, and `mcp-server-patterns`) remain outside the
headline regardless of public ancestry. `frontent-design` is also outside the
code-behavior boundary because its strongest criteria are presentational proxies.

This closure is independent of performance: no development-used skill can
enter the headline, and public removals are limited to three pre-result artifact-license
failures (`cli-typer-scripts`, `json-serialization`, `json-tools`). Their content is
not redistributed. Before any headline execution, the July 12 continuation walked the
frozen 628-row S5 pool in recorded popularity order and applied the fixed gates. It
partitioned every row through index 445, then stopped at the eighth additional strict
admit. The complete machine-readable disposition is
`candidates/D1-continuation-audit.json`.

The boundary is enforced by `experiments/manifests/rq1-skills.draft.json`,
`experiments/manifests/third-party-skills.json`, `skillrace.d1_audit`, and
`skillrace.third_party_audit`. The suite contains 30 headline skills across 20 families
(26 high- and four medium-contingency) with 90 predeclared properties.

Selection is now closed. Image/runtime verification and immutable freeze hashes remain
separate pre-experiment gates; no skill may be substituted based on pilot or headline
performance.

## Funnel (so far)

| Stage | Count |
|-------|-------|
| Candidates examined (S1 + S4, distinct task families) | 30 |
| Admitted to D1 (code-behavior only) | 11 |
| Excluded — presentational/document artifact (X1, refined) | 4 |
| Excluded — subjective success (X1) | 11 |
| Excluded — not published / wishlist entry | 4 |
| Excluded — duplicate task family (X3) | 3 |
| S5 (skillsmp) frozen crawl | 628 candidates |
| S5 continuation rows dispositioned before stop | 446 (indices 0--445) |
| Strict continuation admits | 8 |

The target suite size is 30 (protocol §4). The older S1/S4 and balanced S5 work produced
the preserved 22-skill boundary; the audited S5 continuation supplies eight more. The
authoritative disposition is `D1-continuation-audit.json`, not the earlier proposal in
`D1-proposed-admits.md`. No skill is added or dropped after results are seen.

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

## Prepared suite and continuation evidence

All 30 headline directories contain the exact or explicitly abridged public `SKILL.md`,
`Containerfile.base`, `properties.json`, `applicability.json`, and `PROVENANCE.md`.
The suite has 20 families and 90 fixed natural-language properties. Full membership and
current image status are listed in `candidates/D1-final-suite.md`.

The S5 evidence chain is:

1. `skillrace.crawl_skillsmp` produced 628 unique, non-OpenClaw candidates in
   `candidates/skillsmp-pool.json` (SHA-256
   `af2cb94b19c138e2a705f5312eaa872119bc3bebe599ec443985cf794eea8906`).
2. The July 5 triage/proposal files remain historical aids, not the authoritative final
   selection boundary.
3. `candidates/D1-continuation-audit.json` records every selected or rejected row from
   index 0 through the stop at 445, with one first-failing reason category and explicit
   evidence for near-boundary rejections.
4. `skillrace.d1_selection` rehashes the pool, enforces the complete partition,
   repository cap, exact eight-admit order, stop rule, prepared files, and final manifest
   order. `skillrace.d1_audit` composes this with provenance, licensing, property, and
   image checks.

The eight continuation admits, in frozen order, are `network-config-validation`,
`rest-api-caller`, `csv-workbench`, `argparse-scaffolder`, `data-transform`,
`compiler-hardening`, `validator-agent`, and `log-parser`.

## Remaining D1 freeze gates

Selection and skill preparation are complete. D1 becomes frozen only after all 30
container images pass strict runtime audit and the manifest records immutable input-tree
and image identities. A mechanical image failure is reported consistently for all
methods; it cannot trigger a performance-informed replacement.
