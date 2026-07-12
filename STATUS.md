# Artifact status

This file separates implemented infrastructure from measurements that have actually
been run. It is deliberately conservative: passing offline tests is not reported as a
successful model experiment.

## Ready and currently verified

- The D1 draft headline manifest contains **22 redistributable public skills** across
  12 families: 18 high-contingency and 4 medium-contingency. Four original development
  skills are outside the headline set, and three public candidates with absent or unsafe
  redistribution terms are excluded. Source pins, hashes, provenance, and embedded
  upstream licenses are machine-audited.
- D2 contains **10 scenarios**, **100 hidden tests**, and 192 executable checks. Stored
  Docker evidence currently records every reference passing and the assigned negative
  implementations failing, with no pending or failed runtime audit.
- The RQ3 pipeline enforces public/hidden staging, separate confirmation outside the
  30-run search budget, equal byte-bounded feedback envelopes, one revision per feedback
  producer, all-four-condition hidden evaluation, strict all-ten-test denominators,
  resumable receipts, and recursive provenance verification.
- Model calls use a durable, redacted operation journal with exact request identity,
  crash recovery, conservative billing, and production fail-closed pricing.
- Property scripts are compiled before the agent run, fingerprinted, and later executed
  independently in fresh networkless final-state snapshots with host timeouts.

## Not yet a result

- Both the main campaign protocol and D1 suite manifest remain **draft**. They have not
  been frozen for headline execution.
- The D1 set is currently 22 skills. The same pre-registered mining protocol (selection
  criteria and order-by-popularity pass) still needs to be continued to add eight more
  qualifying headline skills before final publication.
- The next reproducibility milestone is to finish D1 to 30 public skills (same protocol/order),
  then regenerate freeze manifests and rerun full D1 protocol/audit checks.
- **No headline RQ1 or RQ3 measurements have been run.** The paper's result fields are
  therefore placeholders and must not be interpreted as evidence that SkillRACE wins.
- A current live `qwen3.6-flash` completion is blocked because the CloseAI account
  reports **insufficient balance**. The ten RQ3 zero-shot base skills consequently remain
  marked for provenance-preserving regeneration rather than being relabelled after the
  fact.
- The exactly-once parallel campaign engine is in final integration and independent
  review. The last integrated suite run had one campaign-owned failure and no RQ3
  failures; rerun `scripts/artifact_smoke.sh` and the complete suite after this status is
  updated.
- No archival DOI or conference artifact package has been produced yet.

## Gate before paid headline runs

The project may freeze and start the expensive study only after the complete offline
suite passes, exact completion-order replay is proven, the three methods pass their
information-boundary and 30-run accounting checks, all runtime evidence is current, a
funded multi-family pilot leaves complete artifacts, and protocol/model/image/dataset/
analysis hashes are recorded before headline results are inspected.
