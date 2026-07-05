<a href="../README.md"><img src="../skillrace-icon.png" alt="SkillRACE" width="54" align="right"></a>

# Dataset selection protocol (pre-registered)

> This document is written **before** any campaign is run on the mined skills, and is
> committed to the repository with a timestamp, so that skill selection cannot be
> influenced by which skills happen to make \tool look good. It defines the sources,
> the inclusion/exclusion criteria, the procedure, and the decision log. The purpose
> is to make the answer to *"did you cherry-pick skills that favor your method?"* a
> matter of record rather than of trust.

We build **two** datasets, held to the same discipline:

- **D1 — the bug-finding suite** (\tool vs. baselines, RQ1): skills mined from public
  sources whose runs we test for property violations.
- **D2 — the skill-generation scenarios** (RQ3): task families with *held-out* tests,
  used to measure whether \tool's feedback improves an LLM-generated skill. D2's tests
  are authored under a stricter leakage rule (§5).

---

## 1. Why a protocol at all

The headline claim (Greybox → \tool) is a comparison of test-generation methods on a
fixed set of skills. If the skills were chosen after seeing results, the comparison is
meaningless. Two failure modes we explicitly guard against:

1. **Outcome-driven inclusion** — keeping a skill because \tool found more bugs on it.
2. **Difficulty gerrymandering** — over-representing skills whose behavior is strongly
   environment-contingent (where reasoning-guided search is expected to win) and
   dropping skills where it is not.

Both are neutralized by fixing the criteria and the source list in advance, logging
every include/exclude decision with its reason at mining time, and reporting results
**per skill** (never only pooled), so a reader can see the full distribution including
skills where \tool does not separate from the baselines.

---

## 2. Sources (fixed in advance)

We draw candidates only from these sources, enumerated before mining:

| ID | Source | Rationale |
|----|--------|-----------|
| S1 | `github.com/anthropics/skills` (official Agent Skills) | canonical, artifact-producing skills |
| S2 | GitHub code search `path:.claude/skills filename:SKILL.md` | in-the-wild community skills |
| S3 | GitHub code search `filename:SKILL.md` sorted by stars | popular skills across ecosystems |
| S4 | Public "awesome-claude-skills" / "awesome-agent-skills" aggregators | curated community index (stars as popularity signal) |
| S5 | `skillsmp.com` REST API (`/api/v1/skills/search`), coding-agent skills, ranked by stars | large programmatic pool without GitHub-auth; **OpenClaw/ClawHub skills excluded by filter** |

\emph{Two caveats on S5, applied during triage:}
\begin{itemize}
\item \textbf{Repo-stars, not skill-stars.} S5's popularity field is the host
repository's star count, so a skill that is an incidental file in a famous monorepo
inherits a huge number. We therefore cap admitted skills at a small number per
author/repo (default 1--2) so one monorepo cannot dominate the suite, and we do not
treat stars as a quality measure — only as a coarse ordering.
\item \textbf{Doing-skills vs.\ advisory-skills.} Many marketplace entries are
\emph{review/advice} guidance (``conventions for\ldots'', ``use when reviewing\ldots'')
rather than \emph{produce-an-artifact} tasks. D1 requires the latter, because the oracle
checks a produced artifact; advisory skills without a runnable artifact are excluded
under X1.
\end{itemize}
The S5 pool is crawled by \texttt{skillrace.crawl\_skillsmp} (fixed term list) and
first-pass triaged by \texttt{skillrace.triage\_candidates}; the final include/exclude
per candidate remains a logged human judgment (\S4).

We record, per candidate, the source ID and a permalink/commit so the corpus is
reconstructable. We do **not** author skills ourselves for D1 (that is D2's job); D1
skills are found, not made.

---

## 3. Inclusion / exclusion criteria (fixed in advance)

A candidate skill enters D1 iff it satisfies **all** inclusion criteria and **none** of
the exclusion criteria. Each criterion is a yes/no question decided at mining time and
logged.

**Inclusion (all required):**
- **I1 — Mechanically checkable artifact.** The skill's success can be decided by a
  script: a suite that passes, a CLI that runs and emits a checkable value, a project
  that builds, a service that answers. (If success is only judgeable by a human, the
  skill cannot have a trustworthy oracle and is excluded — see X1.)
- **I2 — Dockerizable without credentials.** The environment builds in a container with
  no secrets, paid APIs, or network services beyond a package index.
- **I3 — Procedural guidance, not a one-liner.** The `SKILL.md` prescribes a
  multi-step procedure (so there is behavior to segment and branch on), not a single
  fixed command.
- **I4 — Short-horizon.** A typical task completes in tens of tool calls under our step
  cap, not hundreds.

**Exclusion (any triggers removal):**
- **X1 — Subjective success.** Success is aesthetic or open-ended with no checkable
  criterion (e.g. "make the landing page look modern").
- **X2 — Non-reproducible environment.** Depends on a live external service, a specific
  paid model, GPU, or hardware.
- **X3 — Trivial or duplicate.** Guidance is a single command, or the skill duplicates
  one already admitted (same task family, same structure).
- **X4 — Unsafe to run** even sandboxed (e.g. requires disabling the sandbox).

**Note on environment-contingency.** Whether a skill's behavior depends on the starting
state (the property that favors reasoning-guided search) is **recorded but is NOT an
inclusion criterion.** We deliberately admit skills across the contingency spectrum and
label each `high` / `medium` / `low`. Reporting is stratified by this label so that a
reader sees \tool's performance on low-contingency skills too, rather than only where it
is expected to shine. Excluding low-contingency skills would be the gerrymandering we
are trying to avoid.

---

## 4. Procedure

1. **Enumerate** candidates from S1–S4 until the candidate pool is exhausted or reaches
   ~2–3× the target suite size (target: \~30 admitted skills).
2. For each candidate, in the order discovered, **apply I1–I4 and X1–X4** and record the
   verdict + one-line reason in the decision log (`candidates/skill-suite-candidates.md`).
   The decision is made from the `SKILL.md` and repo contents **before** any \tool run.
3. For each **admitted** skill, author: a `Containerfile.base`, a natural-language
   property specification (the SBE properties), and the per-skill **applicability
   matrix** (which of the fixed invariants apply — a rebasing skill cares about
   force-push; a fix-test skill cares about test integrity). The applicability matrix is
   derived from the fixed invariant catalog by a simple relevance rule, also logged.
4. **Freeze** the admitted set. Any skill that fails to Dockerize during setup is removed
   with reason `build-failed` and **the removal is logged**; it is not silently dropped,
   and we report the count.
5. Run all three methods on the frozen set under identical budget.

The decision log is append-only and committed; the frozen set is tagged.

---

## 5. D2: skill-generation scenarios and the leakage rule

D2 measures whether \tool's findings improve an LLM-generated skill. Its integrity rests
on one rule:

> **The held-out tests and their checks are authored independently of every revision
> loop, and are never shown to any tester or reviser.**

Concretely: the hidden tests (prompt + environment) and their pass-checks are written by
hand (or by a *different* model, from the scenario's target purpose) and committed under
`scenarios/<name>/tests/`. Nothing generated by \tool, greybox, or the floor is derived
from, or allowed to see, these tests. The reviser prompt is byte-identical across
conditions; only the feedback payload differs. A difference in held-out pass rate is
therefore attributable to feedback quality, not to test leakage or a better reviser.

Each scenario also fixes, in advance, the **base skill** all conditions start from (a
single zero-shot LLM-generated `SKILL.md`), so the four conditions differ only in the
feedback used to revise that one starting skill.

---

## 6. Reporting commitments

- Results are reported **per skill**, not only pooled, with the contingency label shown.
- Every excluded/removed candidate appears in the decision log with its reason; we report
  the funnel (candidates → admitted → built → analyzed).
- The greybox granularity sweep is reported for all three levels; the headline uses the
  best level (§ greybox adaptation doc), and the sweep table is shown.
- No skill is added or dropped after results are seen. If a skill must be dropped for a
  mechanical reason (build breaks under a dependency change), it is dropped for **all**
  methods and the drop is logged.
