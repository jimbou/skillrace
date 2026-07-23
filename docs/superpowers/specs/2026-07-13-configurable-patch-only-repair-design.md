# Configurable Patch-Only Skill Repair Design

**Date:** 2026-07-13

## Objective

For every definite public execution failure, produce exactly one independent patched
copy of the original skill without executing, testing, checking, or replaying that
patch. A later, separately launched confirmation phase will run the saved test with the
patched skill. The patch generator is configurable as either a single direct model call
or a constrained Pi coding-agent invocation.

The intended headline configuration is:

```json
{
  "skillrace": "pi",
  "greybox": "direct",
  "random": "direct"
}
```

This is an intentionally method-assisted, full-system comparison. SkillRACE may exploit
the diagnostic artifacts that it uniquely constructs; the baselines receive only their
ordinary shared failure evidence. Results must be described as repair-validated
end-to-end defect yield, not as an equal-information comparison of patching ability.

## Frozen Configuration

The experiment protocol records a `repair` object containing:

```json
{
  "enabled": true,
  "timeout_seconds": 300,
  "max_output_tokens": 4000,
  "temperature": 0.0,
  "reasoning": true,
  "backend_by_method": {
    "skillrace": "pi",
    "greybox": "direct",
    "random": "direct"
  }
}
```

Each backend value is exactly `direct` or `pi`. Backend selection occurs once from the
frozen protocol and cannot be overridden per failure. The patch receipt records the
resolved backend, model, timeout and request identity. A later sensitivity experiment
may freeze a different mapping, including all-direct or all-Pi, without changing code.

## Common Input and Output Contract

Every patch begins from the byte-identical original skill. Patches never accumulate.
The common input contains:

- the complete original `SKILL.md`;
- the exact test prompt;
- the environment description supplied with the test;
- relevant test input files;
- the failed artifact or its bounded, mechanically generated representation;
- exact checker errors and failed executable conditions.

There is no separate dependency/tool-version report. Hidden confirmation data and
future replay results are unavailable.

Both backends may change only `SKILL.md`. The output package must retain the original
file set and byte content for every other skill file. The orchestrator rejects an empty
patch, an invalid skill package, an escaped output path, or any non-`SKILL.md` change.
The patcher is not asked for a rationale. Its only semantic output is the fixed skill.
The default repair style is conservative and additive: preserve useful guidance, add or
clarify the missing contingency near the relevant section, and rewrite/remove existing
text only when that text directly caused the failure.

## Method-Specific Evidence

Random and VeriGrey receive only the common input. Their raw agent sessions remain
archived for reproducibility but are not provided to their patch generator.

SkillRACE additionally receives all bounded diagnostic evidence already produced by its
search execution:

- ordered reasoning episodes and reasoning/thinking summaries;
- tool calls and tool results attached to each episode;
- behavior-tree path;
- selected property and guard mutation;
- intended branch, observed branch and branch classification;
- targeted or serendipitous discovery label;
- paths to the read-only saved case and run evidence available to the patch workspace.

Evidence is selected deterministically. When a size limit is necessary, the common core
is preserved first, then SkillRACE evidence is retained in execution order: failure-
adjacent episodes, their tool interactions, branch/guard evidence, tree path and earlier
episodes. Truncation and byte counts are recorded; no model summarizes evidence before
the patcher sees it.

## Direct Backend

The direct backend performs one journaled model call. Its system prompt states that the
model must output only the complete replacement `SKILL.md` and must not propose or claim
execution. The user prompt contains the common evidence as canonical JSON and the
original skill text. The normalized response is copied into an isolated skill package.

The call uses the frozen model, temperature, reasoning setting, output limit, timeout
and one operation identity. Provider retries follow the shared exactly-once journal
policy; they recover transport errors and do not constitute additional semantic patch
attempts.

## Pi Backend

The Pi backend launches one fresh confined container with a 300-second default wall
clock. It mounts:

- an isolated original-skill copy as writable;
- one staged `repair-context.json` as read-only, containing the common evidence and,
  only for SkillRACE, its bounded diagnostic evidence;
- a short repair prompt and system prompt as read-only;
- the reviewed guided SDK runner as read-only;
- an output location for minimal accounting artifacts.

The runner uses the SDK shipped in the pinned Pi 0.73.1 image and starts Pi in the
writable skill directory with medium thinking and only these built-in tools enabled:

```text
read,grep,edit,write
```

SDK resource discovery is disabled for skills, project extensions, prompt templates,
themes and context files. One reviewed inline policy extension remains active solely to
enforce the repair boundary. It allows reads/searches only of `/workspace/SKILL.md` and
`/evidence/repair-context.json`, requires successful full reads of both before mutation,
allows exactly one edit/write only to `SKILL.md`, and blocks every subsequent tool call.
Before those reads finish, `grep` and repeat reads of an already-consumed input are
blocked. Immediately after both mandatory reads finish, the policy disables `read` and
`grep`, leaving only `edit` and `write`; this prevents unproductive inspection loops
without withholding any required evidence.
The container requires provider egress for inference, but the model has no bash,
browser, HTTP, package-installation, checker, Docker or task-execution tool. No
confirmation image or hidden oracle is mounted.

The initial user prompt contains only the objective and the two required paths; it does
not duplicate the complete skill or evidence. Pi therefore follows an explicit
read→reason→edit workflow. The system prompt forbids rerunning the failure, executing
the skill, invoking the checker, running tests, validating the patch, repairing the
failed artifact, or iterating patch-and-test. After editing `SKILL.md` once, it must
stop. The normal target is three or four turns, with an SDK abort boundary at ten turns
and the independent 300-second container timeout as the final kill boundary.

SDK session and compact operational event files may exist only as ephemeral sources for
usage and failure accounting. They are deleted after extracting input, output and
cache-read token counts, provider-credit cost, turns, tool-call count, blocked-call
count, remaining-required-read count and terminal status. No Pi patcher trace or repair
rationale enters the experiment artifact.

## Patch-Only Lifecycle

The implemented repair lifecycle separates patch generation from exact replay:

1. select every definite failed public execution;
2. stage and hash its method-appropriate evidence;
3. create one crash-safe patch intent;
4. invoke the frozen backend exactly once;
5. validate the resulting skill structurally without executing it;
6. write a terminal patch receipt and patch-only campaign ledger;
7. stop.

A later confirmation command consumes the immutable patched-skill receipt and performs
the single fresh replay. The patcher cannot trigger this command. Existing historical
combined patch/replay artifacts remain readable, but new schedules use the patch-only
schema.

## Minimal Stored Artifacts

For each patch, retain only:

- the patched skill package;
- an automatically derived `SKILL.md` diff;
- original and patched package hashes;
- evidence hash;
- method, model and backend;
- timeout and terminal status;
- input/output token counts, provider-credit cost and wall time;
- crash-safe intent and terminal receipt hashes.

Do not retain a patch rationale, raw model response, or persistent Pi trace. Sensitive
provider credentials are never written.

## Terminal Outcomes

Patch status is one of `completed`, `timeout`, `error`, `invalid_patch`, or
`outcome_unknown`. A terminal or outcome-unknown patch is never attempted again under
the same operation identity. A timeout receives no second semantic patch attempt. Only
`completed` patches are eligible for later confirmation.

## Tests and Acceptance Criteria

Automated tests must prove:

1. protocol parsing accepts only `direct` and `pi` and fixes one backend per method;
2. the intended default mapping resolves SkillRACE to Pi and both baselines to direct;
3. the shared evidence contains exact prompt/environment/failure data but no separate
   dependency-version report;
4. baseline evidence excludes all SkillRACE-only fields;
5. SkillRACE evidence retains ordered episodes, reasoning blocks, tool calls/results,
   tree, guard and branch evidence within the frozen bound;
6. the direct backend performs one call and outputs only a replacement `SKILL.md`;
7. the Pi launch uses only the four read/edit tools, disables skills/extensions/prompt
   templates, and uses an empty Pi home plus isolated working directory;
8. neither backend can invoke replay, checker or task execution;
9. non-`SKILL.md` changes, empty patches and invalid packages fail closed;
10. timeout and crash recovery never create a second semantic patch attempt;
11. patch-only ledgers contain no replay result, rationale, raw response or Pi trace;
12. the later confirmation interface accepts only a completed immutable patch receipt.

The implementation is complete when focused tests, the full unit suite, Python
compilation, shell syntax checks and a bounded no-execution Pi smoke test all pass, and
no repair container remains running.
