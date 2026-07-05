"""Triage a crawled candidate pool into a pre-annotated decision log.

Reads the crawler's JSON (`candidates/skillsmp-pool.json`) and, for each candidate,
proposes a verdict against the protocol's checkable-artifact criterion using a keyword
heuristic over the name+description:
  - LIKELY-INCLUDE : signals a mechanically checkable artifact (cli, parser, api, test,
    build, sql, csv/json/yaml, validator, formatter, file-format, ...).
  - LIKELY-EXCLUDE : signals subjective success (design, art, brand, copywriting,
    prose, docs-writing, ...), or a meta/agent-config skill.
  - REVIEW         : neither signal dominates.

This is ONLY a first pass to order the human's work; the actual include/exclude
decision stays a logged human judgment (docs/dataset-protocol.md §4). It also proposes
a shortlist of the top-N LIKELY-INCLUDE by stars plus backups, and de-dups obvious
near-duplicates (same author+similar name).

Usage:
  python -m skillrace.triage_candidates --in candidates/skillsmp-pool.json \
      --out candidates/skillsmp-triaged.md --keep 30 --backups 15
"""
from __future__ import annotations
import argparse
import json
import pathlib
import re

INCLUDE_KW = [
    "cli", "command-line", "command line", "argparse", "parser", "parse", "csv",
    "json", "yaml", "toml", "config", "sql", "sqlite", "database", "query", "regex",
    "validat", "transform", "convert", "test", "pytest", "unittest", "tdd", "debug",
    "refactor", "build", "compile", "lint", "api", "endpoint", "rest", "fastapi",
    "flask", "server", "docx", "pdf", "xlsx", "spreadsheet", "scraper", "log",
    "format", "encode", "decode", "serialize", "migration", "algorithm",
]
EXCLUDE_KW = [
    "design", "art", "brand", "logo", "aesthetic", "copywrit", "marketing", "prose",
    "blog", "tone", "voice", "persona", "creative", "story", "poem", "comms",
    "communication", "presentation deck", "slide design", "theme", "color palette",
    "writing style", "seo content", "social media", "email draft",
    # meta / agent-config, not a task with a checkable artifact:
    "skill creator", "skill-creator", "create skills", "writing skills", "agent config",
    "prompt template", "system prompt", "persona",
]


def score(rec):
    blob = f"{rec.get('name','')} {rec.get('description','')}".lower()
    inc = sum(1 for k in INCLUDE_KW if k in blob)
    exc = sum(1 for k in EXCLUDE_KW if k in blob)
    if inc and inc > exc:
        return "LIKELY-INCLUDE", inc, exc
    if exc and exc >= inc:
        return "LIKELY-EXCLUDE", inc, exc
    return "REVIEW", inc, exc


def _norm(name):
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


# A candidate is "project-coupled" when its guidance targets ONE specific
# product/codebase rather than a portable task — such skills fail I2 (can't Dockerize
# as a self-contained checkable artifact) and are demoted out of the shortlist (still
# logged for the human). Signalled by a "for/in <the> <X> repo/project/sdk/codebase"
# phrasing, or by naming a specific well-known product as the target.
_COUPLED_RE = re.compile(
    r"\b(for|in|of|within)\b.{0,30}\b(repo|repository|project|codebase|monorepo|sdk|"
    r"package|library)\b", re.I)
_PRODUCT_TOKENS = [
    "lobehub", "clickhouse", "supabase", "n8n", "langflow", "cline", "julia",
    "cypress", "vs code", "vscode", "hermes", "clip", "next.js repo", "django repo",
]


def project_coupled(rec):
    blob = f"{rec.get('name','')} {rec.get('description','')}".lower()
    if any(t in blob for t in _PRODUCT_TOKENS):
        return True
    return bool(_COUPLED_RE.search(blob))


def triage(records, keep, backups, author_cap=2):
    seen_norm = {}
    for r in records:
        r["_stars"] = int(r.get("stars", 0) or 0)
        r["_verdict"], r["_inc"], r["_exc"] = score(r)
        r["_coupled"] = project_coupled(r)
        # dedup: same author + near-identical normalized name
        k = (r.get("author", ""), _norm(r.get("name", "")))
        if k in seen_norm:
            r["_verdict"] = "DUP"
        else:
            seen_norm[k] = True
    records.sort(key=lambda r: (-r["_stars"],))
    # shortlist: likely-include, NOT project-coupled, at most `author_cap` per author,
    # so a single monorepo's skill dump can't dominate the suite.
    per_author = {}
    shortlist, backup = set(), set()
    for r in records:
        if r["_verdict"] != "LIKELY-INCLUDE" or r["_coupled"]:
            continue
        a = r.get("author", "")
        if per_author.get(a, 0) >= author_cap:
            continue
        if len(shortlist) < keep:
            shortlist.add(id(r)); per_author[a] = per_author.get(a, 0) + 1
        elif len(backup) < backups:
            backup.add(id(r)); per_author[a] = per_author.get(a, 0) + 1
        if len(shortlist) >= keep and len(backup) >= backups:
            break
    for r in records:
        r["_shortlist"] = "SHORTLIST" if id(r) in shortlist else (
            "backup" if id(r) in backup else "")
    n_portable_inc = sum(1 for r in records
                         if r["_verdict"] == "LIKELY-INCLUDE" and not r["_coupled"])
    return records, n_portable_inc


def write_log(records, n_include, out_path, keep, backups):
    lines = [
        "# D1 triaged candidate log (skillsmp.com)",
        "",
        "> First-pass triage by `skillrace.triage_candidates` — a keyword heuristic over "
        "name+description. **The `decision`/`reason` columns are for the human** to fill "
        "per `docs/dataset-protocol.md` §3; the `proposed` column is only advisory. "
        "`SHORTLIST` = top likely-includes by stars (the ~30 to confirm first); "
        "`backup` = next best; `DUP` = near-duplicate of a higher-ranked row.",
        "",
        f"Pool={len(records)}  likely-include={n_include}  "
        f"(shortlist target={keep}, backups={backups})",
        "",
        "| pick | proposed | coupled | stars | skill | author | github | description | decision | reason |",
        "|------|----------|---------|------:|-------|--------|--------|-------------|----------|--------|",
    ]
    for r in records:
        desc = (r.get("description") or "").replace("|", "/").replace("\n", " ")[:80]
        lines.append(
            f"| {r.get('_shortlist','')} | {r['_verdict']} "
            f"| {'yes' if r.get('_coupled') else ''} | {r['_stars']} "
            f"| {r.get('name','?')} | {r.get('author','?')} | {r.get('githubUrl','')} "
            f"| {desc} |  |  |")
    pathlib.Path(out_path).write_text("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description="Triage crawled D1 candidates against the criteria")
    ap.add_argument("--in", dest="inp", default="candidates/skillsmp-pool.json")
    ap.add_argument("--out", default="candidates/skillsmp-triaged.md")
    ap.add_argument("--keep", type=int, default=30)
    ap.add_argument("--backups", type=int, default=15)
    args = ap.parse_args()
    records = json.loads(pathlib.Path(args.inp).read_text())
    records, n_inc = triage(records, args.keep, args.backups)
    write_log(records, n_inc, args.out, args.keep, args.backups)
    verdicts = {}
    for r in records:
        verdicts[r["_verdict"]] = verdicts.get(r["_verdict"], 0) + 1
    print(f"triaged {len(records)} candidates: {verdicts}")
    print(f"shortlist proposes {min(args.keep, n_inc)} + {min(args.backups, max(0,n_inc-args.keep))} backups")
    print(f"wrote {args.out} — confirm the decision/reason columns by hand")


if __name__ == "__main__":
    main()
