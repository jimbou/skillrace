---
name: js-feature
description: Implement a small JavaScript feature with a Node test and make the test pass (TDD-style). Use for self-contained JS coding tasks that must be verified by running a test.
---

# Implement a JS feature with tests

Follow these steps in order:

1. **Implement** the requested feature in the implementation file the task names.
2. **Write a test** file using Node's built-in `assert` (`require('node:assert')`)
   that exercises every requirement, including error/edge cases.
3. **Run** the test with `node <testfile>` using the bash tool.
4. If it **fails**, read the error output, fix the implementation (not the test),
   and re-run until it passes.
5. **Report** the final passing `node` output.

Rules:
- Use only Node built-ins — do not run `npm install`.
- Verify by actually running the test; never assume a write or fix worked.
- Do not weaken or delete the test to make it pass.
