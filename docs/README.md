# SkillRACE documentation map

Use these documents in this order when checking the evaluation:

1. [Evaluation guide for reviewers](evaluation-reviewer-guide.md) — current high-level
   experiment contract, accounting, fairness, metrics, RQ3 grading, parallelism, validity
   limitations, and unfinished work.
2. [Implementation status](implementation-status.md) — what is built and verified today,
   what remains, and which claims are not yet results.
3. [Approved evaluation specification](superpowers/specs/2026-07-11-skillrace-evaluation-design.md)
   — detailed design decisions behind the lean protocol.
4. [RQ3 artifact guide](rq3-artifact-guide.md) and [data contracts](data-contracts.md) —
   exact artifact layouts and schemas.
5. Files under `design/` — component-level implementation rationale.

`superpowers/plans/` contains historical task plans. Plans explain how code was built;
they are not the current experimental protocol. Some preserve earlier proposals for extra
ablations, six RQ3 conditions, three hidden repeats, or Greybox-level sweeps. Those ideas
were removed for cost and are superseded by the evaluation guide and approved
specification. Draft/pilot outputs likewise are never headline evidence.

At present both the main protocol and D1 manifest are marked `draft`, CloseAI paid calls
are balance-blocked, and no headline result has been measured.
