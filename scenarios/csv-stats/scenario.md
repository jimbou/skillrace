# Scenario: csv-stats

**Target purpose.** Teach an agent to build a small command-line tool that reports
statistics (sum, mean, count, min, max) over a column of a CSV file.

**What a good SKILL.md must teach (rubric — used only to judge revisions, never shown as a test):**
- Parse CSV with a real parser (the `csv` module), *not* `line.split(",")`, so quoted
  fields containing commas are handled.
- Locate the target column by header name, not a fixed index.
- Coerce values numerically and decide, explicitly, how to treat empty/missing cells
  (skip them for numeric aggregates rather than crashing or counting them as 0).
- Expose a clear CLI: `python3 stats.py <op> --column <name> --file <path>`, printing a
  single value, and exit non-zero on a missing column/file.

**Contingency:** high — behavior depends on quoting, missing cells, and header layout.
