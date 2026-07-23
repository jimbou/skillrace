# GLM three-method development smoke: interrupted diagnostic

**Date:** 2026-07-13  
**Classification:** development-only, incomplete, prohibited from headline reuse  
**Schedule:** `experiments/schedules/development-smoke.glm.json`  
**Raw output:** `out/development-pilots/2026-07-13/glm-three-method-v1/`

## What happened

The GLM-only schedule was created from the three GLM cells in the existing five-cell
smoke: Random, VeriGrey-inspired L1, and SkillRACE, each with two possible counted runs.
It keeps one API, Docker, and agent slot and uses `glm-4.5-flash` for every role.

The first Random proposal request succeeded (HTTP 200). Its first realization was rejected
before Docker/Pi because the candidate wrote a workspace Python script with the ordinary
`#!/usr/bin/env python3` shebang. The textual candidate policy incorrectly treated that
reference as a protected-runtime modification. No agent started and no counted execution
was spent.

The broad textual executable-path ban was then removed: direct configuration/hook surfaces
remain rejected, and the shared host-side runtime fingerprint remains the authoritative
pre-agent protection against a real executable/runtime modification. The new regression
in `tests/test_candidate_runtime_trust.py` uses precisely that workspace shebang and now
passes with the focused realization/runner tests.

After the rejection, two subsequent GLM realizer calls remained open beyond their requested
180-second timeout despite their durable journal intents. The development driver was
terminated to prevent an unbounded wait. It produced zero agent starts and zero results;
the output remains intentionally incomplete and must not be resumed or promoted. The two
unresolved provider operations must be treated as unknown development actions, never
silently retried under the same operation identity.

## Development fallback probe

At the user's direction, a direct `deepseek-v3.2` development-only fallback probe was
also run. It returned HTTP 200 and generated a valid structured realization under the
repaired policy. `deepseek-v3.2` is not in the frozen two-track model catalog or dated
rate card, so this probe's provider-credit cost is intentionally `unknown`; it is not an
experiment observation and does not alter the two headline models.
