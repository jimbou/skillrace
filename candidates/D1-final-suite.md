
## Draft headline D1 suite (reviewed 2026-07-11): 22 redistributable public skills

The four original in-repository skills were used while developing the pipeline, so
they remain development/qualitative cases and cannot enter the quantitative headline.
Of 25 public skills prepared before headline runs, three are excluded solely because
their redistribution rights are unsafe or unclear: `cli-typer-scripts` (no license
grant), `json-serialization` (commercial proprietary license), and `json-tools`
(declares proprietary terms but references a missing license file). Their text is not
shipped; only commit-pinned source metadata and hashes remain. The 22 headline skills
are the **first 22** that satisfied the fixed inclusion/exclusion protocol in the fixed
candidate order by popularity. The pre-recorded, MIT-licensed `condition-based-waiting`
candidate was completed before any headline result. This leaves **22 public headline
skills**, all build- and runtime-smoke-verified.

We still need to continue the same ordered mining protocol to add **8 more** qualifying
public skills, reaching a target of **30 public headline skills**, before the D1 manifest is
frozen.

The boundary is machine checked by `experiments/manifests/rq1-skills.draft.json` and
the 25 source commits/licenses/local byte hashes are pinned in
`experiments/manifests/third-party-skills.json`.

✓ = base image build/runtime verified. `*` = externally sourced skill included in the
draft headline. Rows without `*` are development-only.

| family | skill dir | props | contingency | status |
|--------|-----------|:----:|:-----------:|:------:|
| api | fastapi-endpoint * | 2 | high | ✓ built |
| build-python-cli | build-python-cli | 5 | high | ✓ built |
| cli | cli-argparse-fix * | 3 | high | ✓ built |
| cli | cli-subcommand-validator * | 3 | high | ✓ built |
| config | yaml-config * | 2 | high | ✓ built |
| async-testing | condition-based-waiting * | 5 | medium | ✓ built |
| debugging-difficult-bugs | debugging-difficult-bugs * | 2 | high | ✓ built |
| finishing-a-development-branch | finishing-a-development-branch * | 2 | high | ✓ built |
| fix-failing-test | fix-failing-test | 4 | high | ✓ built |
| frontent-design | frontent-design | 8 | low | ✓ built |
| mcp-server-patterns | mcp-server-patterns | 6 | high | ✓ built |
| parser | json-parser * | 3 | high | ✓ built |
| parser-generator | parser-generator * | 2 | medium | ✓ built |
| refactor | code-refactor-fowler * | 3 | high | ✓ built |
| refactor | refactor * | 3 | high | ✓ built |
| refactor | refactor-complexity-reduce * | 3 | high | ✓ built |
| regex-expert | regex-expert * | 3 | medium | ✓ built |
| sql | sql-query-generator * | 3 | high | ✓ built |
| sql | sql-query-json * | 3 | high | ✓ built |
| sql | sqlmodel-orm * | 3 | high | ✓ built |
| sql-queries | sql-queries * | 3 | high | ✓ built |
| systematic-debugging | systematic-debugging * | 2 | high | ✓ built |
| test-driven-development | test-driven-development * | 4 | high | ✓ built |
| unit-test | unit-test-generator * | 2 | high | ✓ built |
| unit-test-generation | unit-test-generation * | 2 | high | ✓ built |
| using-git-worktrees | using-git-worktrees * | 2 | medium | ✓ built |
