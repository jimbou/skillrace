"""Calibration scoring for the model-driven components (RQ2).

The human labels do not exist yet, but the scoring does — so once ~100 traces are
hand-segmented and a set of episode pairs is hand-labeled for the merge decision, these
functions produce the F1 / agreement numbers the paper reports. Pure functions; no
Docker, no model.

Label file formats (JSON):
  segmentation: {"<run_id>": {"gold": [b1,b2,...], "pred": [b1,b2,...]}, ...}
      where each list is the sorted set of episode START calls (boundaries), e.g.
      [1, 5, 12] means episodes begin at calls 1, 5, 12. (Call 1 is always a boundary.)
  merge:        [{"gold": true|false, "pred": true|false}, ...]
      one entry per labeled episode pair (same-purpose or not).

Usage:
  python -m skillrace.calibrate --segmentation seg_labels.json --merge merge_labels.json
"""
from __future__ import annotations
import argparse
import json
import pathlib


def boundary_f1(gold, pred, tol=0):
    """F1 over boundary positions. A predicted boundary matches a gold one if within
    `tol` calls (tol=0 = exact). Returns (precision, recall, f1)."""
    gold, pred = set(gold), set(pred)
    if not gold and not pred:
        return 1.0, 1.0, 1.0
    matched_gold, matched_pred = set(), set()
    for p in pred:
        for g in gold:
            if g in matched_gold:
                continue
            if abs(p - g) <= tol:
                matched_pred.add(p)
                matched_gold.add(g)
                break
    tp = len(matched_pred)
    prec = tp / len(pred) if pred else 0.0
    rec = tp / len(gold) if gold else 0.0
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
    return prec, rec, f1


def segmentation_scores(labels, tol=0):
    """Mean per-trace boundary F1 across a label set."""
    rows = []
    for rid, d in labels.items():
        p, r, f = boundary_f1(d["gold"], d["pred"], tol=tol)
        rows.append((rid, p, r, f))
    n = len(rows) or 1
    return {
        "n_traces": len(rows),
        "mean_precision": round(sum(r[1] for r in rows) / n, 4),
        "mean_recall": round(sum(r[2] for r in rows) / n, 4),
        "mean_f1": round(sum(r[3] for r in rows) / n, 4),
        "per_trace": [{"run": r[0], "precision": round(r[1], 3),
                       "recall": round(r[2], 3), "f1": round(r[3], 3)} for r in rows],
    }


def binary_agreement(pairs):
    """Agreement + Cohen's kappa for a binary judgment (e.g. same-purpose merge)."""
    n = len(pairs) or 1
    agree = sum(1 for x in pairs if bool(x["gold"]) == bool(x["pred"]))
    po = agree / n
    # kappa
    g1 = sum(1 for x in pairs if x["gold"]) / n
    p1 = sum(1 for x in pairs if x["pred"]) / n
    pe = g1 * p1 + (1 - g1) * (1 - p1)
    kappa = (po - pe) / (1 - pe) if (1 - pe) else 1.0
    # confusion
    tp = sum(1 for x in pairs if x["gold"] and x["pred"])
    tn = sum(1 for x in pairs if not x["gold"] and not x["pred"])
    fp = sum(1 for x in pairs if not x["gold"] and x["pred"])
    fn = sum(1 for x in pairs if x["gold"] and not x["pred"])
    return {"n": len(pairs), "agreement": round(po, 4), "cohen_kappa": round(kappa, 4),
            "confusion": {"tp": tp, "tn": tn, "fp": fp, "fn": fn}}


def main():
    ap = argparse.ArgumentParser(description="Score calibration of model-driven components")
    ap.add_argument("--segmentation", help="segmentation labels JSON")
    ap.add_argument("--merge", help="merge-decision labels JSON")
    ap.add_argument("--tol", type=int, default=0, help="boundary match tolerance (calls)")
    args = ap.parse_args()
    if not (args.segmentation or args.merge):
        raise SystemExit("provide --segmentation and/or --merge")
    if args.segmentation:
        s = segmentation_scores(json.loads(pathlib.Path(args.segmentation).read_text()),
                                tol=args.tol)
        print(f"Segmentation: {s['n_traces']} traces, mean F1 = {s['mean_f1']} "
              f"(P={s['mean_precision']} R={s['mean_recall']}, tol={args.tol})")
    if args.merge:
        m = binary_agreement(json.loads(pathlib.Path(args.merge).read_text()))
        print(f"Merge decision: n={m['n']}, agreement = {m['agreement']}, "
              f"kappa = {m['cohen_kappa']}, confusion = {m['confusion']}")


if __name__ == "__main__":
    main()
