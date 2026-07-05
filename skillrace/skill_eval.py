"""Hidden-test skill evaluation — does a (revised) skill make the agent pass?

A SCENARIO packages hidden tests for one skill:
  scenarios/<name>/tests/<t>/          a normal case dir: Dockerfile + candidate.json
  scenarios/<name>/tests/<t>/checks/   pass-criteria bash scripts (exit 0 = pass),
                                       authored INDEPENDENTLY of any improvement loop
                                       (different model or hand-written) — leakage
                                       control for the skill-generation claim.

Given a CANDIDATE SKILL DIR (a SKILL.md + optional scripts — e.g. LLM-generated
zero-shot, or revised with random / greybox / skillrace feedback), this harness:
  1. derives, per hidden test, a case whose image OVERLAYS the candidate skill at
     /skills/<skill> (so the agent under test consults THIS version);
  2. runs the shared runner + shared property checker on each;
  3. reports per-test and aggregate pass rates -> <out>/skill_eval.json.

The harness is condition-blind: every skill version goes through byte-identical
tests, runner, and checks; only the SKILL.md differs.

Usage:
  python -m skillrace.skill_eval --scenario scenarios/csv-tools \
      --skill-name fix-failing-test --skill-dir candidates/skillrace-v3 \
      --agent-model qwen3.6-flash --out out/skill-eval/skillrace-v3
"""
from __future__ import annotations
import argparse
import json
import pathlib
import shutil
import subprocess
import sys


def derive_case(test_dir, skill_name, skill_dir, work_dir):
    """Copy the hidden test's case dir and overlay the candidate skill into the
    image (COPY skill/ /skills/<name>/ appended to the Dockerfile)."""
    test_dir, work_dir = pathlib.Path(test_dir), pathlib.Path(work_dir)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    shutil.copytree(test_dir, work_dir)
    shutil.copytree(skill_dir, work_dir / "skill")
    df = work_dir / "Dockerfile"
    df.write_text(df.read_text().rstrip() +
                  f"\n# skill under evaluation overlays the baked-in one:\n"
                  f"COPY skill/ /skills/{skill_name}/\n")
    cand = json.loads((work_dir / "candidate.json").read_text())
    cand["skill"] = skill_name
    (work_dir / "candidate.json").write_text(json.dumps(cand, indent=2))
    return work_dir


def run_test(case_dir, run_dir, agent_model, wall_clock, checks_dir):
    subprocess.run([sys.executable, "-m", "skillrace.run_case",
                    "--case", str(case_dir), "--model", agent_model,
                    "--out", str(run_dir), "--wall-clock", str(wall_clock)],
                   capture_output=True, text=True)
    subprocess.run([sys.executable, "-m", "skillrace.check_properties",
                    "--run", str(run_dir), "--checks", str(checks_dir)],
                   capture_output=True, text=True)
    vp = pathlib.Path(run_dir) / "verdicts.json"
    return json.loads(vp.read_text()) if vp.exists() else []


def main():
    ap = argparse.ArgumentParser(description="Evaluate one skill version on a scenario's hidden tests")
    ap.add_argument("--scenario", required=True, help="scenario dir (contains tests/)")
    ap.add_argument("--skill-name", required=True, help="skill mount name (/skills/<name>)")
    ap.add_argument("--skill-dir", required=True, help="the candidate skill version to evaluate")
    ap.add_argument("--agent-model", default="qwen3.6-flash")
    ap.add_argument("--wall-clock", type=int, default=1200)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tests = sorted(p for p in (pathlib.Path(args.scenario) / "tests").iterdir()
                   if (p / "Dockerfile").exists())
    if not tests:
        raise SystemExit(f"no tests under {args.scenario}/tests")

    results = []
    for t in tests:
        case = derive_case(t, args.skill_name, args.skill_dir, out / "cases" / t.name)
        run_dir = out / "runs" / t.name
        verdicts = run_test(case, run_dir, args.agent_model, args.wall_clock,
                            t / "checks")
        # a hidden test PASSES iff every non-fixed pass-criterion holds
        crit = [v for v in verdicts if v.get("provenance") != "fixed"]
        passed = bool(crit) and all(v.get("holds") is True for v in crit)
        results.append({"test": t.name, "passed": passed,
                        "criteria": [{ "id": v["property_id"], "holds": v["holds"]}
                                     for v in crit],
                        "fixed_violations": [v["property_id"] for v in verdicts
                                             if v.get("provenance") == "fixed"
                                             and v.get("violated")]})
        print(f"  {t.name}: {'PASS' if passed else 'fail'}")

    summary = {"scenario": str(args.scenario), "skill_dir": str(args.skill_dir),
               "agent_model": args.agent_model,
               "passed": sum(r["passed"] for r in results), "total": len(results),
               "results": results}
    (out / "skill_eval.json").write_text(json.dumps(summary, indent=2))
    print(f"\n{summary['passed']}/{summary['total']} hidden tests passed "
          f"-> {out}/skill_eval.json")


if __name__ == "__main__":
    main()
