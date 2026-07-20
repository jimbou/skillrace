# Scenario: regex-validate

**Target purpose.** Teach an agent to implement input validators to a precise spec,
accepting exactly the valid inputs and rejecting the rest.

**Rubric (revision-only):**
- Anchor patterns (`^...$`) so partial matches don't pass.
- Enumerate the edge cases the spec implies (boundaries, empty input, near-misses) and
  test against them before finishing.
- Keep the validator a pure function returning a boolean.

**Contingency:** medium — depends on the near-miss inputs present in the environment.
