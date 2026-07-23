# SkillRACE documentation map

Use these documents in this order when checking the evaluation:

1. [July 14 session handoff](2026-07-14-session-handoff.md) — exact stopping point,
   implemented guided repair, paid-run evidence, checker-validity blocker, and ordered
   remaining work.
2. [Evaluation guide for reviewers](evaluation-reviewer-guide.md) — current high-level
   experiment contract, accounting, fairness, metrics, RQ3 grading, parallelism, validity
   limitations, and unfinished work.
3. [Implementation status](implementation-status.md) — what is built and verified today,
   what remains, and which claims are not yet results.
4. [Approved evaluation specification](superpowers/specs/2026-07-11-skillrace-evaluation-design.md)
   — detailed design decisions behind the lean protocol.
5. [RQ3 artifact guide](rq3-artifact-guide.md) and [data contracts](data-contracts.md) —
   exact artifact layouts and schemas.
6. Files under `design/` — component-level implementation rationale.

`superpowers/plans/` contains historical task plans. Plans explain how code was built;
they are not the current experimental protocol. Some preserve earlier proposals for extra
ablations, six RQ3 conditions, three hidden repeats, or Greybox-level sweeps. Those ideas
were removed for cost and are superseded by the evaluation guide and approved
specification. Draft/pilot outputs likewise are never headline evidence.

At present both model-track protocols and the D1 manifest remain `draft`. D1/RQ3 images
and draft schedules are built and audited, but checker semantic validity, the bounded
cross-method/model pilot, final model choice, and identity promotion remain unfinished.
Guided per-failure patching and independent replay are live-validated, but the latest
development chain exposed invalid generated checkers and produced zero confirmed
defects. No headline result has been measured.
