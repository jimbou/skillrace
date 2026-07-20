# DeepSeek v4 Flash bounded pilot v3

This fresh-output pilot includes the generated-task grounding correction (`90e82a0`) and
the bounded tree-alignment format correction (`b4047b9`). The `pilot` and `pilot-v2`
outputs are immutable failed pilot evidence and are not inputs to this run.

Verify the eight frozen cells before launching:

```bash
.venv/bin/python -c "from pathlib import Path; from skillrace_next.pilot import verify_pilot_schedule; print(verify_pilot_schedule(Path.cwd(), Path('skillrace_next/study/pilot-v3/schedule.json')))"
```

Run these commands sequentially. Do not rerun an unfavorable scientific outcome.

```bash
.venv/bin/python -m skillrace_next part1 --live --config skillrace_next/study/pilot-v3/part1/file-check/config.json --s0-dir skills/file-check --s0-receipt skillrace_next/study/part1/file-check/s0-receipt.json --skill-id file-check --properties skillrace_next/study/part1/file-check/properties.json
.venv/bin/python -m skillrace_next part1 --live --config skillrace_next/study/pilot-v3/part1/js-feature/config.json --s0-dir skills/js-feature --s0-receipt skillrace_next/study/part1/js-feature/s0-receipt.json --skill-id js-feature --properties skillrace_next/study/part1/js-feature/properties.json
.venv/bin/python -m skillrace_next part1 --live --config skillrace_next/study/pilot-v3/part1/csv-workbench/config.json --s0-dir skills/csv-workbench --s0-receipt skillrace_next/study/part1/csv-workbench/s0-receipt.json --skill-id csv-workbench --properties skillrace_next/study/part1/csv-workbench/properties.json
.venv/bin/python -m skillrace_next part1 --live --config skillrace_next/study/pilot-v3/part1/fix-failing-test/config.json --s0-dir skills/fix-failing-test --s0-receipt skillrace_next/study/part1/fix-failing-test/s0-receipt.json --skill-id fix-failing-test --properties skillrace_next/study/part1/fix-failing-test/properties.json
.venv/bin/python -m skillrace_next part1 --live --config skillrace_next/study/pilot-v3/part1/regex-expert/config.json --s0-dir skills/regex-expert --s0-receipt skillrace_next/study/part1/regex-expert/s0-receipt.json --skill-id regex-expert --properties skillrace_next/study/part1/regex-expert/properties.json
.venv/bin/python -m skillrace_next part2 --live --config skillrace_next/study/pilot-v3/part2/text-template/config.json --scenario skillrace_next/study/part2/text-template/scenario.md --heldout-test skillrace_next/study/part2/text-template/heldout/t1/test-case.json
.venv/bin/python -m skillrace_next part2 --live --config skillrace_next/study/pilot-v3/part2/csv-stats/config.json --scenario skillrace_next/study/part2/csv-stats/scenario.md --heldout-test skillrace_next/study/part2/csv-stats/heldout/t1/test-case.json
.venv/bin/python -m skillrace_next part2 --live --config skillrace_next/study/pilot-v3/part2/fix-failing-test/config.json --scenario skillrace_next/study/part2/fix-failing-test/scenario.md --heldout-test skillrace_next/study/part2/fix-failing-test/heldout/t1/test-case.json
```
