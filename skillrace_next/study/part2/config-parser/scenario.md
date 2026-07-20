# Scenario: config-parser

**Target purpose.** Teach an agent to parse and *validate* a small INI-style config,
reporting precise errors on malformed input instead of crashing or silently accepting.

**Rubric (revision-only):**
- Parse with `configparser`; require named sections/keys per the spec.
- On a missing required key or a bad value type, exit non-zero and name the problem.
- On valid input, print the normalized values.

**Contingency:** high — valid vs. missing-key vs. bad-type inputs drive different paths.
