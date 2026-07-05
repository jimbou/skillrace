"""Crawl skillsmp.com for D1 candidate skills, per docs/dataset-protocol.md.

skillsmp.com is a community marketplace of *coding-agent* skills with a free REST
search API (`GET /api/v1/skills/search?q=<term>`), ranked by stars. This crawler
queries a fixed, pre-committed list of search terms that map to the protocol's
checkable-artifact families, dedups, EXCLUDES OpenClaw/ClawHub skills (kept out of the
suite deliberately), ranks by popularity, and writes a candidate log in the protocol's
format for a human to apply I1-I4 / X1-X4 to.

It does NOT auto-admit skills — selection stays a logged human judgment (§4). It only
enumerates the candidate pool reproducibly, so the crawl step is no longer gated on a
GitHub API token.

Rate limits: anonymous 50 req/day (10/min); with a free key, 500/day (30/min). Pass
--api-key to raise the cap. One request per search term.

Usage:
  python -m skillrace.crawl_skillsmp --out candidates/skillsmp-pool.md
  python -m skillrace.crawl_skillsmp --api-key $SKILLSMP_KEY --per-term 40 \
      --out candidates/skillsmp-pool.md
"""
from __future__ import annotations
import argparse
import json
import pathlib
import time
import urllib.parse
import urllib.request

API = "https://skillsmp.com/api/v1/skills/search"

# Pre-committed search terms -> the checkable-artifact families in the protocol.
# Fixed BEFORE crawling so the pool is reproducible and not outcome-driven.
TERMS = [
    "cli", "command line tool", "argparse",
    "parser", "csv", "json", "yaml config", "config validation",
    "sql", "sqlite", "database query",
    "regex", "validator", "data transform",
    "unit test", "pytest", "test driven development",
    "fix failing test", "debugging", "refactor",
    "rest api", "fastapi", "flask endpoint",
    "build tool", "compiler", "linter",
    "docx", "pdf", "spreadsheet xlsx",
    "mcp server", "web scraper", "log parser",
]

# Skills to keep OUT of the suite (personal-assistant / OpenClaw ecosystem).
EXCLUDE_SUBSTR = ["clawhub", "openclaw", "claw-hub"]


def _get(url, api_key=None, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "skillrace-crawler"})
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _results(payload):
    """Extract the skills list from skillsmp's envelope.
    Observed shape: {"success":..,"data":{"skills":[...],"pagination":..},"meta":..}.
    Falls back to other common shapes (top-level list, results/items/skills)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        d = payload.get("data")
        if isinstance(d, dict) and isinstance(d.get("skills"), list):
            return d["skills"]
        if isinstance(d, list):
            return d
        for k in ("results", "skills", "items"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def _excluded(rec):
    blob = " ".join(str(rec.get(k, "")) for k in ("name", "author", "githubUrl",
                                                  "skillUrl", "description")).lower()
    return any(s in blob for s in EXCLUDE_SUBSTR)


def crawl(terms, per_term, api_key, sleep_s):
    pool = {}   # key -> record
    for term in terms:
        url = f"{API}?{urllib.parse.urlencode({'q': term, 'sortBy': 'stars'})}"
        try:
            payload = _get(url, api_key=api_key)
        except Exception as e:  # noqa: BLE001 — one bad term shouldn't abort the crawl
            print(f"  [term skip] {term!r}: {type(e).__name__}: {e}")
            time.sleep(sleep_s)
            continue
        n_new = 0
        for rec in _results(payload)[:per_term]:
            if _excluded(rec):
                continue
            key = (rec.get("githubUrl") or "") + "::" + (rec.get("name") or "")
            if key in pool:
                pool[key].setdefault("terms", []).append(term)
                continue
            rec["terms"] = [term]
            pool[key] = rec
            n_new += 1
        print(f"  {term:>28}: +{n_new} new (pool={len(pool)})")
        time.sleep(sleep_s)
    return list(pool.values())


def write_candidate_log(records, out_path):
    records.sort(key=lambda r: -int(r.get("stars", 0) or 0))
    lines = [
        "# D1 candidate pool — skillsmp.com crawl",
        "",
        "> Reproducible pool from `skillrace.crawl_skillsmp` (fixed term list, "
        "OpenClaw/ClawHub excluded), ranked by stars. **Not yet admitted** — apply "
        "`docs/dataset-protocol.md` §3 (I1-I4 / X1-X4) to each row and record the "
        "verdict + reason in the `decision` / `reason` columns. Selection stays a "
        "logged human judgment.",
        "",
        f"Pool size: {len(records)}",
        "",
        "| stars | skill | author | github | description | decision | reason |",
        "|------:|-------|--------|--------|-------------|----------|--------|",
    ]
    for r in records:
        desc = (r.get("description") or "").replace("|", "/").replace("\n", " ")[:90]
        lines.append(
            f"| {r.get('stars','?')} | {r.get('name','?')} | {r.get('author','?')} "
            f"| {r.get('githubUrl','')} | {desc} |  |  |")
    pathlib.Path(out_path).write_text("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description="Crawl skillsmp.com for D1 candidates")
    ap.add_argument("--out", default="candidates/skillsmp-pool.md")
    ap.add_argument("--json-out", help="also write the raw records as JSON")
    ap.add_argument("--api-key", help="skillsmp API key (raises rate limit to 500/day)")
    ap.add_argument("--per-term", type=int, default=25, help="max results kept per term")
    ap.add_argument("--sleep", type=float, default=6.5,
                    help="seconds between requests (>=6 keeps under the 10/min anon limit)")
    args = ap.parse_args()

    print(f"crawling {len(TERMS)} terms (per_term={args.per_term}, "
          f"{'keyed' if args.api_key else 'anonymous'})...")
    records = crawl(TERMS, args.per_term, args.api_key, args.sleep)
    write_candidate_log(records, args.out)
    if args.json_out:
        pathlib.Path(args.json_out).write_text(json.dumps(records, indent=2))
    print(f"\nwrote {len(records)} candidates -> {args.out}")
    print("Next: apply protocol §3 criteria per row (decision/reason), keep ~30, "
          "then author each admitted skill's harness and build it.")


if __name__ == "__main__":
    main()
