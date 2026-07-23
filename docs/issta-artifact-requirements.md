# ISSTA submission and artifact requirements

This checklist uses the latest posted official guidance available on 2026-07-11.
The ISSTA 2027 call was not yet posted, so the ISSTA 2026 rules are the working
baseline and must be rechecked when the target-year call appears.

Official sources:

- [ISSTA 2026 research-paper call](https://conf.researchr.org/track/issta-2026/issta-2026-research-papers)
- [ISSTA 2026 artifact-evaluation call](https://conf.researchr.org/track/issta-2026/issta-2026-artifact-evaluation)

## Paper gate

- Use `\documentclass[acmsmall,screen,review,anonymous]{acmart}`.
- Keep all text, figures, and appendices within 18 pages; references are unlimited.
- Include a `Data Availability` section immediately before the references. It does not
  count toward the page limit.
- Provide an anonymous artifact link at submission, or explicitly explain why the
  artifact is unavailable.
- Preserve double anonymity in the paper, PDF metadata, repository, and artifact URL.
- Fully disclose uses of generative-AI tools as required by ACM policy.
- Frame the work under AI for Analysis and Testing and/or Software Test Generation;
  the official topic list explicitly includes agentic testing and concolic execution.

## Artifact gate

- Package the artifact as a Docker/Podman container or an OVF/OVA VM.
- The main README must contain:
  1. a Getting Started installation/smoke path that reviewers finish within 30 minutes;
  2. step-by-step reproduction instructions mapping commands to every supported paper
     claim and explicitly listing unsupported claims.
- Provide a reduced experiment that completes within one day, plus instructions for
  the full experiment. Long commands must print progress.
- Avoid downloads during experiments. Bundle or prebuild dependencies wherever
  licensing permits.
- If a commercial/closed model is required, it must stay accessible throughout review
  without compromising reviewer anonymity.
- Include top-level plain-text `REQUIREMENTS`, `STATUS`, and `LICENSE` files.
- `REQUIREMENTS` must state architecture, CPU/RAM/storage, OS, Docker/Podman versions,
  internet/model access, expected runtimes, and any non-commodity requirements.
- `STATUS` must name the requested badges and justify them. The realistic targets are
  Functional and Reusable; Available additionally requires an archival DOI.
- Archive the accepted artifact in a durable repository such as Zenodo and cite its DOI
  in the paper's Data Availability section.

## SkillRACE implementation consequences

- The 30-minute path must use recorded/fake model responses and a small deterministic
  Docker smoke; it cannot depend on a live commercial API.
- The one-day path should use a frozen multi-family subset, while the full 30-skill
  RQ1 and ten-scenario RQ3 commands remain documented separately.
- Raw headline artifacts, model-call journals, protocol/skill/image hashes, confirmation
  ledgers, exclusions, and analysis outputs must ship with the artifact.
- The exact GLM and DeepSeek track configurations and reviewer credential procedure must
  be documented without storing a credential in the artifact.
- Yunwu direct and Pi probes currently succeed for both selected models. These tiny
  development probes are archived separately and do not authorize a headline run before
  the remaining image, schedule, pilot, and freeze gates pass.
- Third-party public skills need a license/provenance inventory. A repository-wide
  license must distinguish original SkillRACE code from vendored skill text and fixtures.
