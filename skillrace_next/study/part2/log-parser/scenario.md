# Scenario: log-parser

**Target purpose.** Teach an agent to parse structured log lines and aggregate them,
robustly skipping malformed lines.

**Rubric (revision-only):**
- Parse each line to (timestamp, level, message) by the documented format.
- Skip (and optionally count) lines that do not match, rather than crashing.
- Aggregate as requested (e.g. count by level) and print a stable, parseable summary.

**Contingency:** high — malformed lines and level distribution drive behavior.
