---
name: hello
description: Smoke-test skill for verifying the SkillRACE pi-base image. Use when asked to verify the environment or say hello.
---

# Hello / environment check

When this skill is invoked:

1. Run the shell command `echo skillrace-ok` using the bash tool.
2. Confirm the output was exactly `skillrace-ok`.
3. Reply with a one-line confirmation that the environment works.

Do not do anything else.
