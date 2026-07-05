# Scenario: sqlite-query

**Target purpose.** Teach an agent to answer a question about data in a SQLite database
by writing a script that queries it and prints the answer.

**Rubric (revision-only):**
- Inspect the schema first (`PRAGMA table_info` / `sqlite_master`) rather than assuming
  column names.
- Use parameterized SQL, and aggregate in SQL rather than in Python where natural.
- Handle NULLs correctly (e.g. `AVG` ignores NULLs; `COUNT(col)` excludes them).
- Print only the answer.

**Contingency:** high — depends on schema shape and the presence of NULLs.
