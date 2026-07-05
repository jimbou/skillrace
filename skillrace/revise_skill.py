"""Skill reviser — one model call turns testing feedback into a revised SKILL.md.

Condition-blindness is the whole design: the reviser PROMPT IS IDENTICAL across
conditions; only the FEEDBACK PAYLOAD differs (that payload is what each testing
method produced — random: raw verdicts; greybox: verdicts + novelty stats;
skillrace: verdicts + guard/branch coverage + mutated-assumption bug reports).
So a difference in hidden-test pass rate is attributable to the FEEDBACK QUALITY,
i.e. to the testing method — not to a better reviser.

Usage:
  python -m skillrace.revise_skill --skill-dir skills/fix-failing-test \
      --feedback out/campaign/skillrace/fix-failing-test/campaign.json \
      --out candidates/skillrace-v2
"""
from __future__ import annotations
import argparse
import json
import pathlib
import shutil

from .closeai import chat

REVISE_SYS = (
    "You revise a coding-agent SKILL (a SKILL.md of procedural guidance) using "
    "feedback from testing it. The feedback shows where agents following the skill "
    "violated correctness properties, behaved inconsistently, or hit situations the "
    "skill's guidance does not cover. Rewrite the SKILL.md so an agent following it "
    "avoids the observed failures: make ambiguous steps precise, add the missing "
    "contingencies the tests exposed (what to do when X), and add explicit guardrails "
    "for any violated property. Keep the skill's purpose, format, and overall length "
    "discipline — this is guidance, not a manual; do NOT enumerate test cases.\n"
    "Output ONLY the complete revised SKILL.md content."
)


def build_feedback_payload(campaign_path, max_chars=12000):
    """Method-agnostic projection of a campaign.json into reviser feedback: the
    violations (with each candidate's provenance — for skillrace that includes the
    mutated assumption that produced the input) + the generator's own state."""
    c = json.loads(pathlib.Path(campaign_path).read_text())
    lines = [f"testing method: {c.get('method')} — {len(c.get('iterations', []))} runs"]
    for r in c.get("iterations", []):
        if not r.get("violated") and not r.get("inconclusive"):
            continue
        prov = r.get("provenance") or {}
        lines.append(
            f"- run {r.get('i')}: violated {r.get('violated') or '[]'}"
            + (f", inconclusive {r['inconclusive']}" if r.get("inconclusive") else "")
            + f"\n    input idea: {prov.get('task_nl', '')[:200]} | env: {prov.get('env_nl', '')[:200]}"
            + (f"\n    mutated assumption: {prov.get('mutation')}"
               f" (guard: {prov.get('guard', '')[:150]})" if prov.get("mutation") else "")
            + (f"\n    targeted property: {prov.get('targeted_property')}"
               if prov.get("targeted_property") else ""))
    gs = c.get("generator_state")
    if gs:
        lines.append(f"generator state: {json.dumps(gs)[:800]}")
    return "\n".join(lines)[:max_chars]


def main():
    ap = argparse.ArgumentParser(description="Revise a SKILL.md from testing feedback")
    ap.add_argument("--skill-dir", required=True)
    ap.add_argument("--feedback", required=True,
                    help="campaign.json (any method) OR a plain-text feedback file")
    ap.add_argument("--model", default="qwen3.6-flash")
    ap.add_argument("--out", required=True, help="revised skill dir (copied + new SKILL.md)")
    args = ap.parse_args()

    skill_dir = pathlib.Path(args.skill_dir)
    fb_path = pathlib.Path(args.feedback)
    if fb_path.suffix == ".json":
        feedback = build_feedback_payload(fb_path)
    else:
        feedback = fb_path.read_text()[:12000]

    current = (skill_dir / "SKILL.md").read_text()
    user = (f"CURRENT SKILL.md:\n---\n{current[:8000]}\n---\n\n"
            f"TESTING FEEDBACK:\n{feedback}\n\n"
            "Output ONLY the complete revised SKILL.md.")
    resp = chat([{"role": "system", "content": REVISE_SYS},
                 {"role": "user", "content": user}],
                model=args.model, temperature=0.0, reasoning=True, max_tokens=4000,
                tag="revise.skill", skill=skill_dir.name)

    out = pathlib.Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(skill_dir, out, ignore=shutil.ignore_patterns(
        "repo", "seeds", "*.log", "Containerfile.base", "properties.json"))
    revised = resp["content"].strip()
    if revised.startswith("```"):
        revised = revised.split("\n", 1)[1].rsplit("```", 1)[0]
    (out / "SKILL.md").write_text(revised.strip() + "\n")
    (out / "revision.json").write_text(json.dumps(
        {"from": str(skill_dir), "feedback": str(fb_path), "model": args.model,
         "cost_usd": resp["cost_usd"]}, indent=2))
    print(f"revised SKILL.md -> {out}/SKILL.md  (${resp['cost_usd']:.4f})")


if __name__ == "__main__":
    main()
