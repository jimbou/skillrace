# Level-3 per-test environment (seed 0): baseline failing test, no mutation.
# Built per test; only the tail varies, so the skill-base layers are cache hits.
FROM skillrace/fix-failing-test:base
# >>> SKILLRACE TAIL >>>   (synthesizer writes ONLY this region)
# seed: no scenario change — the repo already ships the failing test.
# <<< SKILLRACE TAIL <<<
