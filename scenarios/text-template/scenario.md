# Scenario: text-template

**Target purpose.** Teach an agent to render a text template by substituting `{{name}}`
placeholders from a data file, to a precise spec.

**Rubric (revision-only):**
- Substitute every `{{key}}` with its value from the data.
- Define and implement the behavior for a missing key (the spec says: leave the
  placeholder untouched) rather than crashing or inserting "None".
- Do not touch text that merely looks similar (e.g. single braces `{x}`).

**Contingency:** medium — missing keys and near-miss syntax drive different paths.
