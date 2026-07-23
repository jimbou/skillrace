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

- `purpose` names the concrete artifact or component and the exact objective,
  hypothesis, bug, repair, or validation target;
- `what_it_did` identifies the relevant investigation and the exact command, symbol,
  file, or code change; and
- `outcome` records the observed tool result, including the exact error or failed
  assertion, whether the repair fixed it, the next failure revealed, or the concrete
  validation evidence.

For example, "debug failing tests" is too broad. A suitable purpose is "repair
recursive cloning of primitive object properties"; its outcome should state the
observed `TypeError`, whether the object-property guard fixed it, and whether a
different array-element failure appeared.

Generic lifecycle descriptions such as "implement the feature", "debug the code",
"run tests", or "perform final verification" are invalid when the trace provides a
more concrete technical objective. The prompt must tell the model to make each record
standalone and to internally reject and rewrite vague records before responding.

## Conservative merge admission

Tree alignment must merge episodes only when they share a concrete technical purpose.
Sharing a broad workflow, language, skill, or lifecycle phase is insufficient.

The same-purpose judgment must return false for:

- different features that both happen to be implemented with tests;
- a narrow sub-goal compared with an episode that also contains materially additional
  work;
- different bugs or validation targets described only as "debugging" or "checking";
  and
- any pair whose only common purpose would be a generic label such as "implement
  functionality and write tests" or "create and verify a file".

After a same-purpose judgment, purpose broadening is an admission check rather than
an unconditional rewrite. It may remove incidental filenames or wording, but the
result must still name the shared component, behavior, bug, repair, or validation
target and must truthfully describe every member. It must not add work absent from a
member. If no such concise purpose exists, the broadening judgment rejects the merge
and the incoming episode becomes a separate node.

Different concrete approaches to the same admitted purpose remain separate
`what_it_did_variants`. Two approaches must not be grouped merely because both use
the same language, skill, testing workflow, or generic tool sequence.

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
than three broad phases.

The tree test must show that two `deepClone` episodes with the same concrete objective
can merge while `deepClone`, `findMissingNumber`, and `toKebabCase` implementation
episodes remain separate roots. A CSV-file creation episode must not merge with an
episode that combines file creation and materially additional analysis merely because
file creation is a shared subset. At least one additional real study skill must also
be inspected before accepting the change.
