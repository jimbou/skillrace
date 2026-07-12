# Third-party notices

SkillRACE evaluates public agent skills without claiming ownership of their text.
The authoritative, machine-checked inventory is
[`experiments/manifests/third-party-skills.json`](experiments/manifests/third-party-skills.json):
it records the exact upstream commit/path, local `SKILL.md` SHA-256, fidelity,
experimental disposition, license identifier, and commit-pinned license evidence.

Exact upstream license files for every distributed headline source are embedded in
[`licenses/third-party/`](licenses/third-party/). This repository's top-level MIT
license applies only to original SkillRACE material and does not replace those terms.

| Upstream | Distributed skill IDs | Terms | Local license copy |
|---|---|---|---|
| JackyST0/awesome-agent-skills | unit-test-generator | CC0-1.0 | `JackyST0--awesome-agent-skills.txt` |
| RightNow-AI/openfang | regex-expert | Apache-2.0 | `RightNow-AI--openfang.txt` |
| a5c-ai/babysitter | parser-generator | MIT | `a5c-ai--babysitter.txt` |
| anthropics/knowledge-work-plugins | sql-queries | Apache-2.0 | `anthropics--knowledge-work-plugins.txt` |
| benchflow-ai/skillsbench | yaml-config | Apache-2.0 | `benchflow-ai--skillsbench.txt` |
| cuioss/plan-marshall | cli-argparse-fix | FSL-1.1-ALv2; non-commercial research is an expressly permitted purpose | `cuioss--plan-marshall.txt` |
| datadrivenconstruction/DDC_Skills_for_AI_Agents_in_Construction | json-parser | MIT | `datadrivenconstruction--DDC_Skills_for_AI_Agents_in_Construction.txt` |
| davila7/claude-code-templates | fastapi-endpoint | MIT | `davila7--claude-code-templates.txt` |
| fastapi/sqlmodel | sqlmodel-orm | MIT | `fastapi--sqlmodel.txt` |
| github/awesome-copilot | refactor-complexity-reduce | MIT | `github--awesome-copilot.txt` |
| kjuhwa/skills-hub | cli-subcommand-validator | MIT | `kjuhwa--skills-hub.txt` |
| luongnv89/claude-howto | code-refactor-fowler | MIT | `luongnv89--claude-howto.txt` |
| mastra-ai/mastra | debugging-difficult-bugs (outside `ee/`) | Apache-2.0 | `mastra-ai--mastra.txt` |
| obra/superpowers | condition-based-waiting, finishing-a-development-branch, systematic-debugging, test-driven-development, using-git-worktrees | MIT | `obra--superpowers.txt` |
| phuryn/pm-skills | sql-query-generator | MIT | `phuryn--pm-skills.txt` |
| rmyndharis/antigravity-skills | unit-test-generation | MIT | `rmyndharis--antigravity-skills.txt` |
| scalyclaw/scalyclaw | sql-query-json | MIT | `scalyclaw--scalyclaw.txt` |
| unkeyed/unkey | refactor (outside `packages/`) | AGPL-3.0-only | `unkeyed--unkey.txt` |

The public candidates `cli-typer-scripts`, `json-serialization`, and `json-tools`
are recorded as license exclusions but their content is deliberately absent from the
artifact. The manifest retains only upstream metadata and hashes needed to audit that
pre-result exclusion.

The four `development_only` skills in the D1 suite manifest are original project
fixtures and are covered by the top-level SkillRACE license.
