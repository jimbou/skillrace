# Adaptive Episode Granularity

## Purpose

SkillRACE episodes must expose concrete reasoning choices that can later be selected
and mutated. Broad phases such as "implementation", "debugging", and "verification"
are not sufficiently specific.

An episode is one contiguous, concrete reasoning attempt. Adjacent tool calls belong
to the same episode only when they pursue the same hypothesis, repair, or validation
objective. A newly observed failure, changed hypothesis, distinct repair, or new
validation objective begins another episode.

## Episode-count guidance

For `N` projected tool calls, the soft target is:

```text
min(N, min(20, 8 + ceil(max(0, N - 8) / 8)))
```

This gives:

- 5 calls: 5 episodes
- 9 calls: 9 episodes
- 20 calls: 10 episodes
- 50 calls: 14 episodes
- 100 calls: 20 episodes

The target guides the model but does not override semantic boundaries. The splitter
must not invent episodes, split a single tool call, or start an episode where the
trace has no new reasoning boundary.

## Required episode detail

Each episode must make its mutation opportunity understandable without rereading the
entire trace:

- `purpose` names the concrete objective, hypothesis, bug, or validation target;
- `what_it_did` identifies the relevant investigation, command, and code change; and
- `outcome` records the observed tool result, including the exact failure fixed,
  remaining failure, or validation evidence.

For example, "debug failing tests" is too broad. A suitable purpose is "repair
recursive cloning of primitive object properties"; its outcome should state the
observed `TypeError`, whether the object-property guard fixed it, and whether a
different array-element failure appeared.

## Prompt and example changes

The splitter prompt must explicitly reject generic lifecycle phases and tell the
model to preserve separate discoveries and repair attempts. The worked example must
demonstrate roughly ten detailed episodes rather than six broad phases.

The existing ordered-partition, source-grounding, correction-loop, evidence, and
provider-provenance requirements remain unchanged.

## Verification

Focused offline tests must first prove the new target values and prompt requirements.
Fresh live contracts must then run both `lab/deepseek-v4-flash` and
`lab/qwen3.6-flash`.

Manual inspection must include the genuine nine-call `js-feature` trace. It should
produce detailed episodes for requirements, implementation, test construction,
individual failure discoveries, individual repairs, and final verification rather
than three broad phases. At least one additional real study skill must also be
inspected before accepting the change.
