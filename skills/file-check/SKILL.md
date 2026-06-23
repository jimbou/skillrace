---
name: file-check
description: Create a requested text file and verify its contents by reading it back. Use for simple file-creation tasks that must be verified.
---

# Create-and-verify a file

When given a file-creation task:

1. Create the requested file with the exact requested contents (use the `write` or `bash` tool).
2. **Verify** by reading the file back (use the `read` or `bash` tool) and checking the contents match exactly.
3. Report whether the verification passed, quoting what you read back.

Always verify by reading from disk — never assume the write succeeded.
