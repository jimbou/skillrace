# GLM/V3.2 campaign smoke: diagnostic sequence

**Date:** 2026-07-13  
**Classification:** development-only, incomplete, prohibited from headline reuse  
**Primary schedule:** `experiments/schedules/development-smoke.glm-v32.json`  
**Primary schedule SHA-256:** `94d6295a3125602683340f2424737ea6f845b07bdeebfef9e75679e85b7e491c`  
**Recovery schedule:** `experiments/schedules/development-smoke.v32.json`  
**Recovery schedule SHA-256:** `2bc8b888ba37b4345d99ba712ae2aa69337d50bda563fb44db66747df06fc567`

## Outcome

The planned five-cell, ten-execution smoke did **not** complete and produced no method
comparison. It was deliberately stopped whenever a cross-cutting validity defect made
further paid executions uninformative. The retained roots are diagnostic snapshots, not
resumable or combinable experiment results:

- `out/development-pilots/2026-07-13/glm-v32-five-cell-v1/`
- `out/development-pilots/2026-07-13/v32-two-cell-v1/`
- `out/development-pilots/2026-07-13/v32-two-cell-v2/`
- `out/development-pilots/2026-07-13/v32-two-cell-v3/`

Across the V3.2 snapshots, two complete Pi executions were reached in separate diagnostic
runs. They must not be pooled. The first cost 0.258083 Yunwu credits and records 17,528
input, 4,881 output, and 104,192 cache-read tokens. The later execution cost 0.083812
credits and records 9,051 input, 3,642 output, and 27,392 cache-read tokens. Both retained
native reasoning throughout tool use. `qwen3.5-flash` was not needed because
`deepseek-v3.2` exposed native reasoning successfully.

## What the sequence found

1. **GLM latency and heavyweight realization.** A valid GLM candidate repeatedly reached
   the direct compile-check call, which hit the fixed 180-second external-outcome limit.
   A later greybox realization tried an unnecessary `apt-get update` and package install.
   The random cell ended at the pre-agent attempt cap with zero counted executions; the
   greybox cell was stopped during the uninformative long build.
2. **Realization repair could discard working setup.** One V3.2 repair replaced the
   generated tail but dropped a required workspace file. The repair contract now asks for
   a complete replacement tail, the smallest correction, and preservation of working
   files, repositories, dependencies, and setup instructions.
3. **The sanity schema was underspecified.** Realizations treated Python module names as
   executables and generated invalid one-line `try/except` commands. The generic prompt
   now distinguishes commands from importable modules, requires every sanity command to
   execute in the initial image with exit status zero, and recommends a heredoc for
   multi-line Python.
4. **Checker staging lost read permission.** Host scripts were intentionally mode `0600`
   and owned by host UID 1000. `docker cp` retained that owner. The checker starts as root,
   but `--cap-drop=ALL` removes its discretionary-access override, so it could not read a
   foreign-UID mode-`0600` file. Scripts are now streamed into the already-running
   checker and created by its own process with mode `0600`. The immutable reviewed source
   hash remains the authority. A Docker integration regression covers this exact case.
5. **A generated oracle reversed its shell condition.** Once staging worked, a generated
   JSON check treated its own successful Python validation as a failure because of an
   inverted `if ! ...` branch. Replaying an unchanged skill would reproduce the same bad
   oracle and falsely confirm a defect. The analyzer therefore retains reproduction as
   an intermediate result and admits a group to headline yield only when the already-
   required independent patch also makes the exact case pass every originally failed
   property.

## Resulting validity rule

The headline metric is now explicitly **repair-validated distinct-defect yield**. A
failure group counts only when its unchanged-skill representative reproduces the same
property/signature and the representative's independent original-skill patch makes the
exact replay pass all originally failed properties. This adds no agent execution beyond
the patch-and-replay already required for every raw public failure. It is an end-to-end
method metric, not a detector-only metric, and that limitation must remain explicit in
the paper.

## Verification and remaining live gate

The affected regression suites, the complete non-live test suite, bytecode compilation,
the D1/D2 offline artifact smoke, and `git diff --check` pass after these fixes. No
headline result is claimed. Before full experiments, run one fresh bounded post-fix smoke
that reaches proposal, realization, Pi, property execution, patch, exact replay, grouped
confirmation, and analysis. Do not resume or promote any root listed above.
