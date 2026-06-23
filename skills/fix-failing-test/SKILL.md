---
name: fix-failing-test
description: Make a failing test suite pass by fixing the implementation code. Use when tests fail because of a bug in the code (not the test).
---

# Fix the failing test

1. Run the test suite (`python3 -m pytest -q`) to see exactly what fails.
2. Read the failing test to understand the expected behavior.
3. Locate and fix the bug in the **implementation** code.
4. Re-run the tests to confirm they pass.
5. Report what the bug was and how you fixed it.

Rules:
- **Never modify, weaken, delete, or skip the test** to make it pass — fix the code.
- Verify by actually re-running the tests; do not assume the fix worked.
