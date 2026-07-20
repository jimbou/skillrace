# DeepSeek v4 Flash bounded pilot v8

This fresh-output pilot includes validated-image workspace seeding and authoritative
environment-baseline verification through `758c27b`. All earlier pilot outputs remain
immutable evidence and are not inputs. Run only cells whose earlier pilot roots lack a
valid terminal scientific result.

Verify the eight frozen cells before launching:

```bash
.venv/bin/python -c "from pathlib import Path; from skillrace_next.pilot import verify_pilot_schedule; print(verify_pilot_schedule(Path.cwd(), Path('skillrace_next/study/pilot-v8/schedule.json')))"
```

The only remaining invalid pilot cell is Part II `fix-failing-test`. Run it once in the
fresh v8 root:

```bash
.venv/bin/python -m skillrace_next part2 --live --config skillrace_next/study/pilot-v8/part2/fix-failing-test/config.json --scenario skillrace_next/study/part2/fix-failing-test/scenario.md --heldout-test skillrace_next/study/part2/fix-failing-test/heldout/t1/test-case.json
```
