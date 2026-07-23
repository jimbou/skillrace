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
| 7oSkaaa/polygon-problems-generator | validator-agent | MIT | `7oSkaaa--polygon-problems-generator.txt` |
| JackyST0/awesome-agent-skills | unit-test-generator | CC0-1.0 | `JackyST0--awesome-agent-skills.txt` |
| Mosi-AI/LiveClawBench | log-parser | MIT | `Mosi-AI--LiveClawBench.txt` |
| OpenBMB/ChatDev | rest-api-caller | Apache-2.0 | `OpenBMB--ChatDev.txt` |
| RedHatProductSecurity/prodsec-skills | compiler-hardening | Apache-2.0 | `RedHatProductSecurity--prodsec-skills.txt` |
| RightNow-AI/openfang | regex-expert | Apache-2.0 | `RightNow-AI--openfang.txt` |
| a5c-ai/babysitter | argparse-scaffolder, parser-generator | MIT | `a5c-ai--babysitter.txt` |
| affaan-m/ECC | network-config-validation; mcp-server-patterns (development-only) | MIT | `affaan-m--ECC.txt` |
| aipoch/medical-research-skills | data-transform | MIT | `aipoch--medical-research-skills.txt` |
| anthropics/skills | frontent-design (development-only; directory spelling retained) | Apache-2.0 | `anthropics--skills.txt` |
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
| openai/openai-agents-python | csv-workbench | MIT | `openai--openai-agents-python.txt` |
| phuryn/pm-skills | sql-query-generator | MIT | `phuryn--pm-skills.txt` |
| rmyndharis/antigravity-skills | unit-test-generation | MIT | `rmyndharis--antigravity-skills.txt` |
| scalyclaw/scalyclaw | sql-query-json | MIT | `scalyclaw--scalyclaw.txt` |
| unkeyed/unkey | refactor (outside `packages/`) | AGPL-3.0-only | `unkeyed--unkey.txt` |

The public candidates `cli-typer-scripts`, `json-serialization`, and `json-tools`
are recorded as license exclusions but their content is deliberately absent from the
artifact. The manifest retains only upstream metadata and hashes needed to audit that
pre-result exclusion.

The four `development_only` fixtures remain excluded from headline inference because
they were used to build or pilot the system. `build-python-cli` and `fix-failing-test`
are original SkillRACE fixtures covered by the top-level license.
`frontent-design/SKILL.md` matches
`anthropics/skills@9d2f1ae187231d8199c64b5b762e1bdf2244733d` except for a final newline;
`mcp-server-patterns/SKILL.md` is an abridged copy of
`affaan-m/ECC@40927950c49f6e742d341e20ff7b9b7e1e7bfff5` with upstream metadata and one
repository-local cross-reference removed. Those two files remain under their upstream
Apache-2.0 and MIT terms respectively. This attribution correction does not make either
fixture eligible for the headline dataset.
