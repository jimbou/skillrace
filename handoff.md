# SkillRACE handoff

**Last updated:** 2026-07-15

The complete historical record is
[docs/2026-07-14-session-handoff.md](docs/2026-07-14-session-handoff.md). Its earlier
pre-run Bash/semantic-audit sections are preserved as development history and are
superseded by the July 15 continuation at the end of that file.

## Current stopping point

- No headline RQ1 or RQ3 experiment has been run.
- The active RQ1 order is candidate sanity → agent → immutable final snapshot → blinded
  post-run Python checker authoring → isolated execution.
- The checker author receives only the task prompt, environment description, one NL
  property, available tools, and final workspace paths. It receives no contents,
  trace/diff contents, result, verdict, campaign feedback, or method identity.
- Python exit `0`/`1`/`2` means holds/violated/not considered. Source is compiled
  locally; one syntax failure gets one targeted retry, then only that property is
  excluded. There is no semantic-audit LLM call and no generated Bash in active RQ1.
- The active `post-run-python-check-v2` prompt also forbids invented signatures, CLI
  syntax, formats, headers, bounds, and expected values; it tells the runtime program to
  inspect documentation/source/help and exit 2 if the expectation is underdetermined.
- Each valid checker runs in a fresh networkless child of the immutable final snapshot.
  Fixed checks and human-authored RQ3 hidden Bash checks are unchanged.
- The post-run manifest/fingerprint records snapshot/tree identity, prompt/policy,
  scripts, exclusions, operation/receipt identities, input/output/cache-read tokens,
  provider-credit cost, and unknown-cost status.
- The same executor path is used by Random, VeriGrey-inspired, and SkillRACE. The paper
  must call it a blinded post-run path-adaptive generated oracle, not an independent
  pre-run oracle.
- TDD covers the path-only prompt, the two concrete json-parser checker bugs, one retry
  and per-property exclusion, cache identity, campaign order, outcome-unknown handling,
  three-state Python exits, fresh-child isolation, fair scheduling, and RQ3 compatibility.
- The complete offline pytest suite passes after one compatibility correction to an old
  scheduling test helper. Artifact smoke, compileall, and diff checks must be rerun once
  more before any live validation.
- The refreshed offline audit still finds 30 RQ1 skills/90 properties and 10 RQ3
  scenarios/30 public properties with valid/unique schemas. No property change was
  needed. The expanded saved inventory has 308 Bash scripts: 116 generated-development
  diagnostics and 192 human-authored RQ3 hidden checks. The old generated scripts retain
  the same vacuity/interface/heredoc/final-tree defects and are not reusable evidence.
- Three fresh development-only live campaigns were attempted. GLM-4.7/`validator-agent`
  and DeepSeek-V3.2/`log-parser` each completed one agent and authored four v1 Python
  checks. Manual review invalidated all five reported violations because the checks
  guessed input whitespace, the 16:00 boundary, CSV schema, or CLI syntax. A v2
  DeepSeek/`csv-workbench` campaign exhausted its two candidate attempts before any agent
  or checker. A checker-only stdin probe was locally invalid (multiprocessing cannot
  import `<stdin>`), made three immediate ProviderError terminals with no tokens/cost,
  and is not v2 evidence. There is still no defensible saved failure for patch/replay.

## Historical paid diagnostics

Do not resume any July 14/15 campaign or terminal operation identity. The GLM-4.7 run's
reported failures were manually invalidated (malformed heredoc and final-tree/diff
confusion). DeepSeek-V3.2's old batched semantic audit rejected every usable checker.
All recorded tokens, cache reads, costs, outcome-unknown operations, interrupted intents,
and artifact paths remain in the dated handoff. They are diagnostics, never headline
results.

The guided Pi patch-only chain remains mechanically complete. Its saved json-parser
replay returned `same_failure`, but manual review showed that the originating generated
checkers were invalid, so it counts as zero confirmed defects and cannot seed another
chain.

### Latest live accounting

- GLM-4.7 validator campaign: proposal 834 input/98 output, realization 1,247/485,
  agent 8,579 input/2,111 output/18,432 cache-read, and four checker calls totaling
  1,663 input/3,319 output. The agent took 45.6 s and the complete execution 155.009 s.
  The immutable receipts were written before a GLM-4.7 tariff was available, so their
  provider-credit cost fields remain unknown. The user subsequently supplied the
  three-tier tariff and clarified that cache reads use the applicable input rate. A
  per-request retrospective calculation from the journal estimates 90.711 credits for
  the agent and 0.064438 for proposal, realization, and checker generation: 90.775438
  credits total. This interpretation is not yet encoded or frozen and must not replace
  the receipt values until the pricing design is approved and tested.
- DeepSeek-V3.2 log-parser campaign: proposal 887/102/0 tokens and 0.002080 credits;
  realization 1,331/797/0 and 0.005053; agent receipt 8,694 input/4,564 output/78,720
  cache-read and 0.188520; four checkers totaled 1,985 input/3,085 output/0 cache-read and
  0.013225. The agent took 170.7 s and the complete execution 280.726 s. Total known
  provider credits for this campaign are 0.208878.
- DeepSeek v2 CSV pre-agent campaign: two proposals/realizations totaled 3,147 input,
  4,929 output, zero recorded cache reads, and 0.021081 credits. Attempt 1 was
  sanity-rejected; attempt 2 returned an exactly 4,000-token truncated realization.
  Zero agent executions and zero checker calls occurred.
- The invalid checker-only stdin diagnostic produced three immediate local
  `ProviderError` terminals under operation `4eeeacd2a8694d17b8a1e142fb27e5f0`, with no
  HTTP status, tokens, or recorded cost. Do not retry or reuse that identity.

## Resume here

1. Obtain one valid live v2 checker sample under a fresh normal campaign identity; do not
   use the failed stdin helper or resume any campaign above.
2. Only after that validation, obtain a different manually defensible saved failure and
   run exactly one fresh failure → patch → independent exact replay → verified RQ1 cell.
   Only a replay passing every originally failed property is a confirmed defect.
3. Keep all token/cache/cost/time/terminal receipts. Never reuse prior operation IDs and
   never tune prompts to make SkillRACE win on a selected skill.
4. Then complete the bounded cross-method/model pilot, choose/freeze the two model
   tracks, promote the lightweight draft identities/schedules, and only then run the
   headline RQ1/RQ3 study.

At this stopping point no campaign, agent, generator, checker, patcher, replay, delayed
cleanup process, or Docker container is running. A final process/container audit on
2026-07-15 also found no unmatched recent operation intents. The worktree contains
extensive intentional uncommitted work; do not reset, discard, overwrite, or clean
unrelated changes.
