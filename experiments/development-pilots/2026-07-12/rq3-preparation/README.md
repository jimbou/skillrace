# RQ3 preparation development probe

These are **development-only** connectivity/provenance probes, not headline RQ3 inputs
or outcomes. On 2026-07-12 the production `prepare_scenario` path was invoked once for
`argparse-cli` with each selected model. No generated skill content or hidden-test
outcome was used to change a prompt, method, scenario, oracle, or inclusion decision.
The headline driver must create fresh prepared inputs under its frozen model-track
result root and must not reuse these directories.

Both calls completed with valid `/2` journal provenance and produced the same normalized
benchmark-template hash
`137dcbd9e284185470ee71dfea90ca121606429406c7959588e7939ee58c9243` while producing
different model-specific base-skill hashes. An offline rerun with `yunwu_key` removed
resumed the GLM receipt without attempting a provider call.

The copied hidden benchmark exists here only because private scenario preparation is the
boundary being exercised. These copies are never public-phase campaign inputs.
