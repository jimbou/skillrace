"""Aggregate campaign.json files into the RQ1 comparison table.

Reads `out/campaign/<method>/<skill>/campaign.json` for all methods and skills, and
prints (and writes) the per-method and per-skill numbers the paper's RQ1 table and
discovery-vs-budget figure need: distinct properties violated, runs-with-violation,
one-based observed/right-censored discovery records, unique branches (for
skillrace), reproducible-violation counts if a k-regrade was run, and the LaTeX
macro values to paste into the tex.

Pure post-processing — no Docker, no model. Run after campaigns finish.

Usage:
  python -m skillrace.aggregate --root out/campaign --out out/campaign/summary.json
"""
from __future__ import annotations
import argparse
import json
import pathlib


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


def _counted_records(camp):
    """Return agent executions, excluding pre-agent attempts in new artifacts."""
    if isinstance(camp.get("attempts"), list):
        return [record for record in camp["attempts"]
                if record.get("consume_budget") is True]
    return [record for record in camp.get("iterations", [])
            if record.get("consume_budget", True) is True]


def _first_violation(camp):
    records = _counted_records(camp)
    for ordinal, record in enumerate(records, start=1):
        if record.get("violated"):
            return ordinal, True
    return len(records), False


def _completion(camp, counted_runs):
    """Return experiment completeness without reinterpreting legacy artifacts."""
    if isinstance(camp.get("attempts"), list):
        status = camp.get("status")
        budget = camp.get("budget")
        budget_complete = (
            isinstance(budget, int) and budget >= 0 and counted_runs == budget
        )
        complete = status == "completed" and budget_complete
        if complete:
            return True, "completed exact budget"
        expected = str(budget) if isinstance(budget, int) else "unknown"
        return False, f"status={status or 'missing'}; counted={counted_runs}/{expected}"

    if "complete" in camp:
        complete = camp.get("complete") is True
        return complete, "legacy explicit complete flag"
    if "status" in camp:
        complete = camp.get("status") == "completed"
        return complete, f"legacy status={camp.get('status')}"
    return True, "legacy status absent"


def summarize_campaign(camp):
    its = _counted_records(camp)
    complete, completion_reason = _completion(camp, len(its))
    distinct = sorted({p for r in its for p in r.get("violated", [])})
    with_viol = sum(1 for r in its if r.get("violated"))
    first_runs, first_observed = _first_violation(camp)
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
        "runs_to_first_violation": first_runs,
        "first_violation_observed": first_observed,
        "right_censored": complete and not first_observed,
        "complete": complete,
        "headline_eligible": complete,
        "completion_reason": completion_reason,
        "status": camp.get("status"),
        "budget": camp.get("budget"),
        "reproducible": reproducible, "n_reproducible": len(reproducible),
        "classifications": classes,
        "greybox_level": camp.get("greybox_level"),
    }


def aggregate(root):
    camps = load_campaigns(root)
    per_campaign = [summarize_campaign(c) for c in camps]
    # pool by method
    by_method = {}
    incomplete_campaigns = []
    for s in per_campaign:
        m = by_method.setdefault(s["method"], {"skills": 0,
                                               "campaigns_total": 0,
                                               "campaigns_incomplete": 0,
                                               "distinct_props": set(),
                                               "runs_with_violation": 0, "runs": 0,
                                               "first_violation_records": [],
                                               "n_reproducible": 0})
        m["campaigns_total"] += 1
        if not s["headline_eligible"]:
            m["campaigns_incomplete"] += 1
            incomplete_campaigns.append({
                "method": s["method"],
                "skill": s["skill"],
                "status": s["status"],
                "runs": s["runs"],
                "budget": s["budget"],
                "completion_reason": s["completion_reason"],
            })
            continue
        m["skills"] += 1
        m["distinct_props"].update(f"{s['skill']}:{p}" for p in s["distinct_violated"])
        m["runs_with_violation"] += s["runs_with_violation"]
        m["runs"] += s["runs"]
        m["n_reproducible"] += s["n_reproducible"]
        m["first_violation_records"].append({
            "skill": s["skill"],
            "runs": s["runs_to_first_violation"],
            "observed": s["first_violation_observed"],
        })
    pooled = {}
    for m, d in by_method.items():
        survival = d["first_violation_records"]
        pooled[m] = {
            "skills": d["skills"], "runs": d["runs"],
            "campaigns_total": d["campaigns_total"],
            "campaigns_eligible": d["skills"],
            "campaigns_incomplete": d["campaigns_incomplete"],
            "distinct_violated_pooled": len(d["distinct_props"]),
            "runs_with_violation": d["runs_with_violation"],
            "first_violation_records": survival,
            "skills_with_any_violation": sum(
                1 for record in survival if record["observed"]
            ),
            "reproducible_violations": d["n_reproducible"],
        }
    return {
        "per_campaign": per_campaign,
        "pooled_by_method": pooled,
        "incomplete_campaigns": incomplete_campaigns,
    }


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
    ap.add_argument("--manifest", help="frozen experiment manifest (verified mode)")
    ap.add_argument("--schedule", help="completed schedule.json (verified mode)")
    ap.add_argument("--d1", help="frozen D1 manifest (verified mode)")
    ap.add_argument("--allow-draft", action="store_true")
    args = ap.parse_args()

    verified = (args.manifest, args.schedule, args.d1)
    if any(verified):
        if not all(verified) or not args.out:
            ap.error("verified mode requires --manifest, --schedule, --d1, and --out")
        from .analyze_rq1 import (
            analyze_verified_cells,
            verify_rq1_experiment,
            write_analysis_outputs,
        )

        cells = verify_rq1_experiment(
            experiment_manifest_path=args.manifest,
            schedule_path=args.schedule,
            d1_manifest_path=args.d1,
            require_frozen=not args.allow_draft,
        )
        analysis = analyze_verified_cells(cells)
        paths = write_analysis_outputs(analysis, args.out)
        print(
            "aggregate.py compatibility mode delegated to the verified RQ1 analyzer; "
            f"wrote {paths['json']}"
        )
        return

    summary = aggregate(args.root)
    print(
        "WARNING: legacy raw-property aggregation is diagnostic only and is not "
        "eligible for headline paper claims. Use --manifest/--schedule/--d1."
    )
    print("Pooled by method:")
    for m, d in sorted(summary["pooled_by_method"].items()):
        print(f"  {m:>10}: skills={d['skills']} runs={d['runs']} "
              f"distinct_props={d['distinct_violated_pooled']} "
              f"runs_w_viol={d['runs_with_violation']} "
              f"discovery_records={len(d['first_violation_records'])} "
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
