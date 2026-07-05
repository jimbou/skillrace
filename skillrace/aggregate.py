"""Aggregate campaign.json files into the RQ1 comparison table.

Reads `out/campaign/<method>/<skill>/campaign.json` for all methods and skills, and
prints (and writes) the per-method and per-skill numbers the paper's RQ1 table and
discovery-vs-budget figure need: distinct properties violated, runs-with-violation,
median runs-to-first-violation, unique branches (for skillrace), reproducible-violation
counts if a k-regrade was run, and the LaTeX macro values to paste into the tex.

Pure post-processing — no Docker, no model. Run after campaigns finish.

Usage:
  python -m skillrace.aggregate --root out/campaign --out out/campaign/summary.json
"""
from __future__ import annotations
import argparse
import json
import pathlib
import statistics


def load_campaigns(root):
    """root/<method>/<skill>/campaign.json -> list of campaign dicts."""
    out = []
    for cj in sorted(pathlib.Path(root).glob("*/*/campaign.json")):
        try:
            c = json.loads(cj.read_text())
            c["_path"] = str(cj)
            out.append(c)
        except Exception as e:
            print(f"  [skip] {cj}: {e}")
    return out


def _first_violation_run(camp):
    for r in camp.get("iterations", []):
        if r.get("violated"):
            return r["i"]
    return None


def summarize_campaign(camp):
    its = camp.get("iterations", [])
    distinct = sorted({p for r in its for p in r.get("violated", [])})
    with_viol = sum(1 for r in its if r.get("violated"))
    fv = _first_violation_run(camp)
    reproducible = sorted({p for r in its for p in r.get("reproducible", [])})
    # unique tree branches only meaningful for skillrace; count classifications
    classes = {}
    for r in its:
        c = r.get("classification")
        if c:
            classes[c] = classes.get(c, 0) + 1
    return {
        "method": camp.get("method"), "skill": camp.get("skill"),
        "runs": len(its),
        "distinct_violated": distinct, "n_distinct_violated": len(distinct),
        "runs_with_violation": with_viol,
        "first_violation_run": fv,
        "reproducible": reproducible, "n_reproducible": len(reproducible),
        "classifications": classes,
        "greybox_level": camp.get("greybox_level"),
    }


def aggregate(root):
    camps = load_campaigns(root)
    per_campaign = [summarize_campaign(c) for c in camps]
    # pool by method
    by_method = {}
    for s in per_campaign:
        m = by_method.setdefault(s["method"], {"skills": 0, "distinct_props": set(),
                                               "runs_with_violation": 0, "runs": 0,
                                               "first_violation_runs": [],
                                               "n_reproducible": 0})
        m["skills"] += 1
        m["distinct_props"].update(f"{s['skill']}:{p}" for p in s["distinct_violated"])
        m["runs_with_violation"] += s["runs_with_violation"]
        m["runs"] += s["runs"]
        m["n_reproducible"] += s["n_reproducible"]
        if s["first_violation_run"] is not None:
            m["first_violation_runs"].append(s["first_violation_run"])
    pooled = {}
    for m, d in by_method.items():
        fvr = d["first_violation_runs"]
        pooled[m] = {
            "skills": d["skills"], "runs": d["runs"],
            "distinct_violated_pooled": len(d["distinct_props"]),
            "runs_with_violation": d["runs_with_violation"],
            "median_runs_to_first_violation": (statistics.median(fvr) if fvr else None),
            "skills_with_any_violation": len(fvr),
            "reproducible_violations": d["n_reproducible"],
        }
    return {"per_campaign": per_campaign, "pooled_by_method": pooled}


def latex_macros(pooled):
    """Emit \\renewcommand lines for the paper's placeholder macros."""
    def g(m, k): return pooled.get(m, {}).get(k, "TBA")
    lines = ["% paste into the PLACEHOLDERS block (values from aggregate.py)"]
    lines.append(f"\\renewcommand{{\\BugsRandom}}{{{g('random','distinct_violated_pooled')}}}")
    lines.append(f"\\renewcommand{{\\BugsGreybox}}{{{g('greybox','distinct_violated_pooled')}}}")
    lines.append(f"\\renewcommand{{\\BugsSkillrace}}{{{g('skillrace','distinct_violated_pooled')}}}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Aggregate campaign results into RQ1 numbers")
    ap.add_argument("--root", default="out/campaign", help="dir with <method>/<skill>/campaign.json")
    ap.add_argument("--out", help="write full summary JSON here")
    args = ap.parse_args()

    summary = aggregate(args.root)
    print("Pooled by method:")
    for m, d in sorted(summary["pooled_by_method"].items()):
        print(f"  {m:>10}: skills={d['skills']} runs={d['runs']} "
              f"distinct_props={d['distinct_violated_pooled']} "
              f"runs_w_viol={d['runs_with_violation']} "
              f"median_runs_to_first={d['median_runs_to_first_violation']} "
              f"reproducible={d['reproducible_violations']}")
    print("\nPer skill (distinct properties violated):")
    for s in summary["per_campaign"]:
        print(f"  {s['skill']:>22} [{s['method']:>9}]: {s['n_distinct_violated']} "
              f"{s['distinct_violated']}")
    print("\n" + latex_macros(summary["pooled_by_method"]))
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(summary, indent=2))
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
