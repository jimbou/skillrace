# Artifact requirements

SkillRACE has a no-cost offline validation path and a separate, explicitly paid live
experiment path. Start with the offline path; it does not need an API credential.

## Host software

- Linux on x86-64. The current artifact is tested on Ubuntu with Linux 6.17.
- Python 3.12 or newer. The current artifact is tested with Python 3.12.3.
- Docker Engine with a working local daemon. The current artifact is tested with Docker
  Engine 29.6.1. Docker is needed for runtime oracle checks and experiments, but not for
  the smallest static/unit-test subset.
- Bash, Git, and standard Unix utilities.
- Internet access only when pulling/building uncached container dependencies or making
  an explicitly requested live model call.

The Python package itself uses the standard library. The development extra pins pytest
for verification:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

Do not use `sudo` for the artifact commands. Configure Docker so the current user can
access its daemon.

## Offline Getting Started

From the repository root:

```bash
PYTHON=.venv/bin/python scripts/artifact_smoke.sh
```

This gate compiles the Python sources, runs the focused experimental-contract tests,
audits the 30-skill D1 manifest and third-party licensing boundary, and verifies the
checked-in runtime evidence for all 10 D2 scenarios and 100 hidden tests. It does not
contact the Yunwu API, start an agent-under-test run, or spend model credit.

If the 30 prebuilt D1 images are already present, add the local image-identity check:

```bash
SKILLRACE_SMOKE_REQUIRE_IMAGES=1 PYTHON=.venv/bin/python \
  scripts/artifact_smoke.sh
```

## Live experiments

Live RQ1/RQ3 execution additionally requires:

- a funded Yunwu account and `yunwu_key` exported in the shell;
- network access to the configured Yunwu endpoint;
- two independent full tracks: one uses `glm-4.5-flash` for every model-driven role,
  and the other uses `deepseek-v4-flash` for every model-driven role;
- a dated Yunwu rate-card snapshot for both model IDs. Provider-native costs are
  reported in Yunwu credits (`⚡`), without inventing a USD conversion;
- all referenced Docker base images built or available locally;
- substantially more time, disk, and model credit than the Getting Started smoke.

Never put the credential in a manifest or command-line argument. Live calls are
journaled durably, including exact request identity, provider usage when available, and
known or explicitly unknown billing. A paid headline run must not be started until both
draft track protocols and the dataset manifests have been reviewed and frozen.
Development connectivity probes are kept outside pilot and headline result directories.

## Reproduction levels

- `scripts/artifact_smoke.sh`: no-cost contract/evidence check, intended to finish well
  within the artifact-review Getting Started window.
- Full offline suite: `python -m pytest -m 'not live'`.
- D2 runtime re-execution: rebuild the scenario images and regenerate the stored audit
  evidence before validating it. See `scenarios/README.md`.
- Headline RQ1/RQ3: paid, long-running campaigns; commands and freeze requirements are
  documented in `README.md`, `docs/rq3-artifact-guide.md`, and `STATUS.md`.
