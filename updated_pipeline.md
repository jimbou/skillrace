updated design is really two connected experiments:

Bug discovery and repair: Can SkillRACE find and fix more defects in an existing skill than Random or VeriGrey?
Skill improvement: Does the skill improved using SkillRACE generalize better to unseen tests?
Core objects

For each skill S, define:

Natural-language properties P: Requirements that must hold for the skill to be correct.
Example: “The generated server must start successfully.”
Example: “The server must respond to an HTTP request.”
Test case T:
A task prompt x.
An initial environment E
0
	​

, usually represented by a Dockerfile and supporting files.
Execution budget B:
Maximum wall-clock time, tool calls, or both.
Run output:
Final artifact/workspace A.
Complete agent trace τ, including reasoning, tool calls, and outputs.
Executable checks C
T
	​

:
Bash or Python programs corresponding to the natural-language properties.
Each check returns pass, fail with a diagnostic, or inconclusive.
Part I: Finding and repairing bugs in an existing skill
A. Initial setup

For each evaluated skill:

Select an existing skill S
0
	​

.
Define the natural-language correctness properties P.
Choose the agent harness, such as Pi.
Freeze the weaker agent model, such as Qwen3.6-Flash or GLM-4.7-Flash.
Freeze the stronger verifier/patcher model, such as Codex with GPT-5.6 Terra.
Fix the execution budget B.
Give SkillRACE, VeriGrey, and Random the same:
Initial skill.
Agent model.
Runner.
Budget.
Container policy.
Property specification.
Verifier model.
B. Generate a test

The method under evaluation proposes a test:

T
i
	​

=(x
i
	​

,E
0,i
	​

)

where x
i
	​

 is the prompt and E
0,i
	​

 is the Docker environment.

The generation strategy differs by method:

Random: Generates a fresh prompt and environment independently.
VeriGrey-inspired: Mutates tests using novelty in tool-use sequences as feedback.
SkillRACE: Selects a reasoning edge or unexplored branch from its behavior tree and generates a prompt/environment intended to exercise it.

The generated test should be validated before spending an agent run—for example, ensuring that the Docker image builds and that the intended initial condition actually exists.

C. Run the skill
Build a clean container from E
0,i
	​

.
Mount an empty artifact directory.
Start Pi inside the container.
Provide:
The current skill.
The test prompt x
i
	​

.
The fixed execution budget B.
Let the weaker model perform the task.
Preserve:
The final artifact.
The full agent trace.
Tool outputs and exit codes.
Timeout or budget-exhaustion information.
D. Construct and execute the property checks

For every natural-language property p∈P:

Give the stronger verifier:
The property.
The prompt.
The Dockerfile and initial environment.
The skill.
The final artifact, read-only.
Ask it to produce an executable Bash or Python check.
Explicitly prohibit it from modifying or repairing the artifact.
Execute every check in the final container state.
Record:
Pass.
Fail and diagnostic message.
Inconclusive, if the checker itself is invalid or cannot determine the result.

This produces a result vector:

R
i
	​

={(p,status,diagnostic)∣p∈P}
E. Update the exploration state

For SkillRACE:

Segment the run into episodes.
Extract each episode’s:
Purpose.
Observed outcome.
Reasoning for the next action.
Merge the run into the existing behavior tree.
Create or update reasoning-labelled edges.
Record whether the generated test:
Reached its intended branch.
Reached a different new branch.
Produced no new behavior.
Missed the targeted condition.
Associate failed properties and diagnostics with the relevant run and episodes.

VeriGrey instead updates its tool-sequence novelty state. Random retains no feedback state.

F. Patch the skill

If one or more checks fail:

Give the patcher:
The current skill.
Failed natural-language properties.
Failure diagnostics.
Prompt and environment.
Relevant episodes and observed outcomes.
Method-specific feedback:
SkillRACE receives reasoning/tree evidence.
VeriGrey receives its tool-sequence evidence.
Random receives only the task and failure evidence.
Ask it to modify only the skill, not the test or artifact.
Produce a candidate patched skill S
′
.
G. Replay the exact test
Delete the previous artifact directory.
Rebuild or restore the same initial environment E
0,i
	​

.
Run Pi again with:
The exact same prompt x
i
	​

.
The patched skill S
′
.
The same weaker model.
The same execution budget.
Run the same executable checks used for the original run.
Compare the before-and-after results.

A candidate defect is repaired when:

At least one previously failing check now passes.
No previously passing check regresses.
The improvement is confirmed by replay.

However, one failed property should not automatically equal one distinct bug. Several checks may fail because of the same underlying defect. Deduplicate using something like:

(property group,failure signature,root-cause category)
H. Continue the campaign

This is where I strongly recommend separating two notions.

Discovery campaign: always test the original skill

For measuring “how many bugs does each method find?”, every newly generated test should run against the same original skill S
0
	​

.

Otherwise, after each repair, the target keeps changing:

Later methods are testing different skills.
A repair may hide or introduce defects.
Bug yield becomes dependent on patch ordering.
Results across methods become difficult to compare.

Therefore:

T
1
	​

(S
0
	​

),T
2
	​

(S
0
	​

),…,T
n
	​

(S
0
	​

)

Each discovered failure can still receive an independent patch and exact-case replay, but that patch is not carried into the next discovery run.

This produces two clean metrics:

Distinct bugs discovered in S
0
	​

.
Discovered bugs that the generated patch successfully repairs.
Improvement campaign: use cumulative patches

When the goal is explicitly to improve a skill iteratively, carry the updated skill forward:

S
0
	​

→S
1
	​

→S
2
	​

→⋯→S
n
	​


That belongs in Part II.

Part II: Generating and iteratively improving a new skill
A. Prepare a skill-generation scenario

Start with a skill scenario G, not an existing skill.

A scenario describes the general purpose of the desired skill, for example:

Create a skill that guides an agent in building and debugging a small REST API.

For each scenario, prepare a benchmark containing multiple tests:

H
G
	​

={T
1
	​

,T
2
	​

,…,T
m
	​

}

Each test contains:

Prompt.
Docker environment.
Predefined executable correctness checks.

Here the checks can and should be authored before the experiment because the benchmark designer already knows the expected final behavior.

B. Split exploration and held-out tests

Divide the benchmark into:

Development/exploration tests: Available to SkillRACE, VeriGrey, or Random during improvement.
Held-out tests: Never visible to test generators, patchers, or skill revisers.

This separation is essential. Otherwise, the improved skill may merely encode solutions for the tests used during revision.

C. Generate the initial skill
Give the skill-generation model only the scenario G.
Ask it to generate an initial skill S
0
	​

.
Use exactly the same S
0
	​

 as the starting point for all three methods.

Create independent copies:

S
0
Race
	​

,S
0
VeriGrey
	​

,S
0
Random
	​


They initially have identical contents.

D. Iteratively improve the skill

For each method and each iteration i:

Generate or select a new development test.
Execute the current skill S
i
	​

 with the weaker model.
Run the test’s executable checks.
Collect failed checks and diagnostics.
Update the method’s exploration state.
Ask the patcher to revise the current skill.
Replay the exact test with the revised skill.
Accept the revision only if:
It fixes at least one failed check.
It does not regress checks that previously passed on that test.
Ideally, it does not regress a small retained regression set.
Carry the accepted skill into the next iteration:
S
i+1
	​

={
S
i
′
	​

S
i
	​

	​

if the patch is accepted
otherwise
	​


After the fixed campaign budget, each method produces one final skill:

S
Race
,S
VeriGrey
,S
Random
E. Evaluate generalization

Run every final skill on every held-out test using:

The same weaker execution model.
The same budgets.
Fresh containers.
The same executable checks.
Multiple repetitions if model execution is stochastic.

Report:

Percentage of executable checks passed.
Percentage of tests for which all checks pass.
Mean and median pass rate per scenario.
Number of scenarios on which each method produces the best skill.
Regressions relative to the original zero-shot skill.
Agent runs, tokens, cost, and wall-clock time.

The central comparison is:

PassRate(S
Race
,H
G
	​

)vs.PassRate(S
VeriGrey
,H
G
	​

)vs.PassRate(S
Random
,H
G
	​

)