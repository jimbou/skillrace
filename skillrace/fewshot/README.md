# Few-shot example for the Episode Segmenter agent

The segmenter agent is given these two files **verbatim** on every call, as a worked
example of the input it will see and the output it must produce:

- `segmenter_example_input.txt` — a realistic `simplified_trace.txt` (a `build-python-cli`
  run, rendered in the exact format `skillrace/simplify_trace.py` produces).
- `segmenter_example_output.json` — the expected agent output for that input.

A different domain (getting a Python CLI to run, not frontend) is used on purpose, so
the agent learns the *method* of splitting rather than overfitting to one task type.

## What this example is designed to teach

- **Decision-level boundaries, not generic phases.** Each episode is one contingent
  sub-goal: reproduce → survey deps → install the missing one → diagnose the data gap →
  bridge it → verify.
- **Environment-contingent decisions (the interesting branch points).** This is the
  point of the rewrite: the run forks on **what the environment already provides**.
  - *requests/tabulate are already installed, PyYAML is not* → episode 3 installs only
    the missing one. Negating that guard later yields a run where PyYAML is **already
    present** (episodes 2–3 collapse), or where `pip install` **fails offline** (a new
    branch the skill must handle).
  - *data.yaml is missing but data.json is present* → episode 5 converts what's
    available. Negating that yields runs where data.yaml already exists (skip it), where
    **no** data file exists (must synthesize), or where data.json is malformed.
  These are exactly the "some stuff available, some not" divergences the tree branches
  on and the guard extractor mutates into new test cases.
- **The input is UNSEGMENTED — segmentation is a real merge.** The trace is a flat list
  of tool calls with `reasoning:` shown inline wherever the agent's thinking shifts.
  There are **10 reasoning shifts but only 6 episodes**: episodes 2–5 each **merge two
  consecutive reasoning blocks** that serve one sub-goal (e.g. ep 2 = "check
  requirements" + "verify which actually import" → one *survey deps* episode). The agent
  must group, not just copy reasoning boundaries. Each episode's `opening_reasoning` is
  the **first** reasoning block inside it.
- **Low-decision stretches stay one episode.** Episode 2 is four probe commands under
  one purpose (survey deps) → one episode.
- **Outcomes are read from tool RESULTS, never narration.** Episode 3's outcome is the
  *grounded partial progress* ("import error gone, but now FileNotFoundError: data.yaml")
  taken from the actual tracebacks — exactly the false-victory trap to avoid.
- **Episode count tracks the target.** 20 tool calls → target ≈ 6 → 6 episodes.

## What the agent does NOT produce

`opening_reasoning` is **not** in the example output. The deterministic assembler adds
it afterward — the verbatim reasoning of the turn that owns each episode's `start_call`
— so the guard signal is never paraphrased. The agent only emits
`{start_call, end_call, intent, what_it_did, outcome}`.

See [episode-segmenter.md](../../docs/design/episode-segmenter.md) and
[episode-summarizer.md](../../docs/design/episode-summarizer.md).
