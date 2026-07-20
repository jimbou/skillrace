# DeepSeek v4 Flash bounded pilot

This is the approved pre-headline pilot:

- Part I: `file-check`, `js-feature`, `csv-workbench`, `fix-failing-test`, and
  `regex-expert`;
- Part II: `text-template`, `csv-stats`, and `fix-failing-test`;
- methods: Random, VeriGrey, and SkillRACE;
- two iterations per method;
- one replicate and one held-out repetition;
- Part II uses `t1` only; `t2` through `t10` remain reserved;
- every non-verifier role uses `lab/deepseek-v4-flash`;
- checker authoring uses Codex `gpt-5.6-terra` with medium reasoning.

`schedule.json` binds all eight configs and their inputs. Verify it before running:

```bash
.venv/bin/python -c "from pathlib import Path; from skillrace_next.pilot import verify_pilot_schedule; root=Path.cwd(); print(verify_pilot_schedule(root, root/'skillrace_next/study/pilot/schedule.json'))"
```

Run the cells sequentially in this order. Stop on a persistent provider failure. Do not
retry a model because its task, artifact, patch, or checker result is unfavorable.

```bash
.venv/bin/python -m skillrace_next part1 --live \
  --config skillrace_next/study/pilot/part1/file-check/config.json \
  --s0-dir skills/file-check \
  --s0-receipt skillrace_next/study/part1/file-check/s0-receipt.json \
  --skill-id file-check \
  --properties skillrace_next/study/part1/file-check/properties.json

.venv/bin/python -m skillrace_next part1 --live \
  --config skillrace_next/study/pilot/part1/js-feature/config.json \
  --s0-dir skills/js-feature \
  --s0-receipt skillrace_next/study/part1/js-feature/s0-receipt.json \
  --skill-id js-feature \
  --properties skillrace_next/study/part1/js-feature/properties.json

.venv/bin/python -m skillrace_next part1 --live \
  --config skillrace_next/study/pilot/part1/csv-workbench/config.json \
  --s0-dir skills/csv-workbench \
  --s0-receipt skillrace_next/study/part1/csv-workbench/s0-receipt.json \
  --skill-id csv-workbench \
  --properties skillrace_next/study/part1/csv-workbench/properties.json

.venv/bin/python -m skillrace_next part1 --live \
  --config skillrace_next/study/pilot/part1/fix-failing-test/config.json \
  --s0-dir skills/fix-failing-test \
  --s0-receipt skillrace_next/study/part1/fix-failing-test/s0-receipt.json \
  --skill-id fix-failing-test \
  --properties skillrace_next/study/part1/fix-failing-test/properties.json

.venv/bin/python -m skillrace_next part1 --live \
  --config skillrace_next/study/pilot/part1/regex-expert/config.json \
  --s0-dir skills/regex-expert \
  --s0-receipt skillrace_next/study/part1/regex-expert/s0-receipt.json \
  --skill-id regex-expert \
  --properties skillrace_next/study/part1/regex-expert/properties.json

.venv/bin/python -m skillrace_next part2 --live \
  --config skillrace_next/study/pilot/part2/text-template/config.json \
  --scenario skillrace_next/study/part2/text-template/scenario.md \
  --heldout-test skillrace_next/study/part2/text-template/heldout/t1/test-case.json

.venv/bin/python -m skillrace_next part2 --live \
  --config skillrace_next/study/pilot/part2/csv-stats/config.json \
  --scenario skillrace_next/study/part2/csv-stats/scenario.md \
  --heldout-test skillrace_next/study/part2/csv-stats/heldout/t1/test-case.json

.venv/bin/python -m skillrace_next part2 --live \
  --config skillrace_next/study/pilot/part2/fix-failing-test/config.json \
  --scenario skillrace_next/study/part2/fix-failing-test/scenario.md \
  --heldout-test skillrace_next/study/part2/fix-failing-test/heldout/t1/test-case.json
```
