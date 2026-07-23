# Artifact status

**Latest stopping record:** [docs/2026-07-14-session-handoff.md](docs/2026-07-14-session-handoff.md).
It contains the guided repair implementation, exact paid-run accounting, generated
checker failures, supported-model boundaries, and the ordered remaining work.

This file separates implemented infrastructure from measurements that have actually
been run. It is deliberately conservative: passing offline tests is not reported as a
successful model experiment.

## Ready and currently verified

- The D1 draft headline manifest now contains **30 redistributable public skills** across
  20 families: 26 high-contingency and 4 medium-contingency. Four development-used
  skills are outside the headline set, and three public candidates with absent or unsafe
  redistribution terms are excluded. Source pins, hashes, provenance, embedded upstream
  licenses, the frozen 628-row S5 pool, and the complete popularity-ordered disposition
  of rows 0--445 are machine-audited. The historical 22-skill boundary is retained as
  the balanced pre-result suite recorded on July 11; the artifact does not claim those
  22 were a literal prefix of the S5 popularity array because the surviving record does
  not establish that. The July 12 pre-result continuation stops at its eighth strict
  additional admit, bringing D1 to its fixed target of 30.
- All **30 construction images** and both model overlays per skill are built. The two
  generic Skillgen overlays and 60 D1 track images (62 total) match their immutable
  draft locks and pass fresh networkless runtime audits for Pi 0.73.1, the exact
  one-model catalog, the intended single skill, and a clean workspace.
- D2 contains **10 scenarios**, **100 hidden tests**, and 192 executable checks. Its
  contracts now pin the Pi 0.73.1 construction runtime and deterministically project only
  the model catalog for GLM/DeepSeek hidden executions. The old oracle evidence was
  explicitly invalidated before this migration; the replacement matrix now validates all
  100 references, rejects all 100 starting states, kills all 215 assigned
  negative/criterion pairs, and reports zero pending or failed evidence records.
- The RQ3 pipeline enforces public/hidden staging, separate confirmation outside the
  30-run search budget, one independent original-skill patch/exact-case replay for every
  failed public execution, equal byte-bounded feedback envelopes, one revision per
  feedback producer, all-four-condition hidden evaluation, strict all-ten-test
  denominators, resumable receipts, and recursive provenance verification.
- RQ1's manifest driver now schedules grouped unchanged-skill confirmation and per-raw-
  failure repair only after each 30-run campaign is terminal. Its strict analyzer rejects
  missing/tampered campaign, confirmation, or repair receipts and generates confirmed-
  defect yield, discovery/AUC/censoring, repair, cost, paired family-bootstrap, CSV,
  JSON, TeX, and plot-source artifacts.
- Model calls use a durable, redacted operation journal with exact request identity,
  crash recovery, conservative billing, and production fail-closed pricing.
- RQ1 property checks are authored after the agent from only the task/environment,
  property, available tools, and final workspace paths. The author sees no contents,
  trace/diff, result, verdict, or method identity. Generated standalone Python checks
  are fingerprinted and executed independently in fresh networkless final-state
  snapshots with host timeouts. A July 13
  regression now explicitly snapshots an agent container whose `sleep` entrypoint was
  overridden, proving that every isolated checker child overrides that inherited
  entrypoint too. This closes the staging failure seen in the first live component run.
- The active RQ1 checker has no semantic-audit LLM call and generates no Bash. Python
  syntax is checked locally; one syntax failure gets one targeted retry, then only that
  property is marked not considered. Exit `0`/`1`/`2` means holds/violated/not
  considered. Author calls omit the output ceiling, use a 120-second timeout, and bind
  operation/receipt identity, input/output/cache-read tokens, cost, final snapshot/tree,
  scripts, and exclusions into the post-run manifest. Fixed checks and human-authored
  RQ3 hidden checks are unchanged.
- Fresh v1 live validation reached one GLM-4.7 validator run and one DeepSeek-V3.2
  log-parser run. All eight Python checks executed, but manual review invalidated all
  five reported violations: the checks guessed whitespace, a time boundary, CSV
  columns/order, or CLI syntax. None is a saved failure and no patch call was made.
  The generic v2 prompt now forbids such guesses, requires runtime docs/source/help
  inspection, and exits 2 when underdetermined. Focused offline tests pass; a fresh v2
  CSV campaign had one sanity rejection and one invalid realization, so v2 still lacks a
  valid live end-to-end sample.
- The July 15 offline re-audit reviewed all 90 RQ1 properties across 30 skills and all
  30 RQ3 public properties across 10 scenarios. IDs, evidence kinds, and uniqueness all
  pass; no property change was needed. The expanded 308 saved-Bash plus eight generated
  Python diagnostic inventory confirmed
  that invalid vacuous-success and guessed-interface behavior is
  concentrated in generated development checks, not the 192 human-authored RQ3 hidden
  checks. See `docs/2026-07-14-checker-suite-audit.md`.

## Not yet a result

- Both the main campaign protocol and D1 suite manifest remain **draft**. They have not
  been frozen for headline execution.
- D1 selection, image construction, dual-model overlay, and runtime audit are complete at
  30 skills, but the suite remains `draft` until those already recorded immutable
  input/image identities are promoted into the recursive frozen manifest.
- Concrete complete RQ1/RQ3 schedules for both model tracks are checked in and validated,
  but remain draft. The next reproducibility milestone is to finish image/runtime checks,
  run the bounded development-only campaign pilot, and write final freeze hashes before
  any headline result is inspected.
- **No headline RQ1 or RQ3 measurements have been run.** The paper's result fields are
  therefore placeholders and must not be interpreted as evidence that SkillRACE wins.
- Four July 14 development-only checker-gate campaigns produced zero started-agent runs.
  They are diagnostics, not results: two DeepSeek realizations were unparsable, one GLM
  checker call became outcome-unknown, one candidate already solved its task, and five
  attempts failed pre-agent checker mechanics. Paid-call accounting is in the dated
  handoff. No failure/patch/replay chain was eligible.
- July 15 uncapped GLM-4.7/DeepSeek-V3.2 pilots are also diagnostics. GLM completed one
  agent run, but manual review invalidated both reported failures (a malformed checker
  heredoc and a final-tree checker that ignored the agent diff). DeepSeek correctly
  stopped pre-agent after its batched audit rejected every usable checker. No current
  failure is eligible for patch/replay.
- Dated direct and Pi probes for both `glm-4.5-flash` and `deepseek-v4-flash`, plus public
  rate evidence, provider-credit accounting, reasoning usage, exact single-model
  configurations, and Pi 0.73.1 image IDs, are archived and pass an offline audit. They
  establish the intended integration but do not promise that a provider route is available
  at every later time. The full track protocols still require the bounded paid pilot and
  final recursive freeze hashes. Headline RQ3 will make twenty fresh provenance-preserving
  base generations (ten per model track) in private prepared scenario copies. Two
  development-only preparation calls have already verified exact model routing,
  cross-track benchmark identity, `/2` receipts, and offline resume; those artifacts are
  prohibited from headline reuse.
- The exactly-once parallel campaign, confirmation, and repair paths have focused passing
  tests. On July 13 the complete no-live suite ran to completion, and the offline artifact
  gate passed with required D1 images and D2 runtime evidence. A clean-checkout artifact
  rehearsal still remains.
- The first five-cell development smoke reached both Yunwu models but produced zero agent
  starts because all completed cells exhausted generic realization/build attempts. It
  exposed lost failure diagnostics/cost and unsafe parallel retry coordinates. Those
  infrastructure defects are corrected (exact failure preservation and frozen
  `epoch_size=1`). The incomplete run and its one deliberately unresumed unknown operation
  are documented under
  `experiments/development-pilots/2026-07-12/` and are prohibited from headline reuse.
- A later two-execution DeepSeek/SkillRACE component smoke did reach Pi twice, completed
  both agent runs, and exercised candidate generation, trace folding, fallback selection,
  repair bookkeeping, and the development-only confirmation ledger. It found no defect.
  Its three generated property scripts were inconclusive only because an inherited Docker
  entrypoint made each checker child exit before staging; the regression above fixes and
  tests that infrastructure error. This output is development-only and is not evidence
  about method effectiveness.
- A fresh re-run after that fix was blocked before candidate construction because
  `deepseek-v4-flash` returned HTTP 429 with Yunwu's explicit “current group upstream
  load is saturated” message for the documented minimal request, the non-thinking
  request, and the thinking-enabled request. It consumed zero agent-execution budget and
  is recorded as a failed development preflight, not a result. A successful direct
  preflight for each selected model is now a prerequisite for the remaining live smoke.
- A GLM-only three-method development launch also produced zero agent starts. Its first
  candidate exposed and corrected an over-broad textual ban on a harmless workspace
  `#!/usr/bin/env python3` shebang; the shared runtime fingerprint still protects actual
  runtime files. Two later GLM realizer calls remained open beyond their requested timeout,
  so the development process was terminated and its two unresolved operations are treated
  as unknown and non-resumable. The complete record is
  `experiments/development-pilots/2026-07-13/glm-three-method-diagnostic.md`.
- `deepseek-v3.2` is reachable as a development candidate: it produced a valid structured
  realization, completed a short Pi skill/task probe, and passed a direct call through the
  production wall-clock transport. It remains outside the current frozen two-track
  catalog, with a dated provisional development rate but no reviewable rate-card archive
  or D1 image lock. Before headline runs, the final model list will be hardcoded; V3.2
  may be added only by completing those same artifacts and preflights. Until then, it is
  excluded from all headline costs and outcomes.
- The July 13 GLM/V3.2 bounded diagnostics exposed three pre-headline validity issues.
  GLM's Yunwu route repeatedly reached the 180-second direct-call deadline and one
  generated environment spent minutes in an unnecessary package-network build. V3.2
  exposed a build-repair response that dropped required setup, sanity contracts that
  confused Python modules with executable commands or contained invalid one-line Python,
  and checker scripts copied as UID-1000 mode-0600 files that capability-dropped root
  could not read. Generic prompt regressions now require complete minimal repair tails
  and executable sanity contracts; checker scripts are streamed into each isolated child
  with checker-owned mode-0600 staging. A subsequent real V3.2 execution recorded native
  reasoning and executed its checks, then revealed a semantically reversed generated
  oracle. Headline analysis now conservatively requires both unchanged-skill reproduction
  and successful patched-skill exact-case replay. All of these runs remain development
  diagnostics, not effectiveness evidence.
- A subsequent fresh V3.2 bounded gate completed proposal, two counted Pi executions,
  checking, one patch, one patched exact replay, one unchanged-skill confirmation, and
  recursive development analysis. It fixed a relative campaign-path resolution defect;
  both post-search replays timed out, so the gate reports zero repair-validated defects.
  The complete record is
  `experiments/development-pilots/2026-07-13/bounded-development-gate.md`.
- On July 14 the new guided Pi patcher completed a real saved-failure patch with both
  mandatory evidence reads, one `SKILL.md` edit, immutable accounting, and an
  independently launched exact replay. The replay returned `same_failure`, correctly
  yielding zero confirmed defects. Manual inspection showed that this development case
  is not a valid positive repair gate: `valid-json-out` imposed JSON stdout although the
  task requested a pandas DataFrame, and `parses-valid` skipped the actual
  `parse_sensor_data` callable and invoked `main` with an incompatible argument. An
  inventory of 284 generated development/scenario check scripts found 21 missing-artifact
  vacuity patterns and 12 stdout-as-JSON patterns. The pre-run semantic checker audit is
  now implemented and offline-verified. A broader fresh scan found 24/16 triage matches
  and manually confirmed the two original bugs; the differing heuristic counts are not
  defect totals. No paid follow-up was launched because the remaining non-json saved
  failure is an invalid already-solved start-state timeout, while older smoke failures use
  obsolete checker/campaign contracts. The required next chain still needs a defensible
  current-format saved failure.
- No archival DOI or conference artifact package has been produced yet.

## Gate before paid headline runs

The project may freeze and start the expensive study only after one manually defensible
failure passes the bounded
patch/replay/analysis gate, the complete offline suite passes, exact completion-order
replay is proven, the three methods pass their information-boundary and 30-run accounting
checks, per-failure repair is exactly-once and post-search, all runtime evidence is
current, both final models pass direct and Pi preflight, the bounded five-cell dual-model
campaign pilot leaves complete artifacts, and protocol/model/image/dataset/analysis
hashes are recorded before headline results are inspected.
