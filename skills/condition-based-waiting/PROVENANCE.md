# Provenance: condition-based-waiting

- **Source:** `obra/superpowers`, historical standalone skill at commit
  [`48410c7f1973bd66569a627ef27ef6619e0ba923`](https://github.com/obra/superpowers/commit/48410c7f1973bd66569a627ef27ef6619e0ba923),
  paths `skills/condition-based-waiting/SKILL.md` and `example.ts`.
- **Fidelity:** both upstream files are vendored byte-for-byte.
- **License:** MIT, copyright 2025 Jesse Vincent; full upstream text is stored in
  `LICENSE.upstream`.
- **Selection:** this was an admitted, pending S4 candidate in the pre-headline D1
  decision log. It replaces no observed result and was prepared before headline runs.
- **Forward-test note (2026-07-11):** blind baseline agents already chose valid join
  or bounded polling patterns on two small Python races. This confirms medium rather
  than high contingency for the selected model; inclusion is independent of that
  outcome. A fresh skill-enabled agent then fixed a stale cached-readiness loop by
  re-reading the condition inside the bounded poll; the unchanged behavioral assertion
  passed 100/100 repetitions.
