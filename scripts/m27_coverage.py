#!/usr/bin/env python3
"""M27 Crystal coverage matrix (offline).

Cross-references the durable Crystal v2 claims (from the M27 store ledger) against
the 20 source articles and the M26 article-level core points, to answer macro
coverage questions (NOT single-quote review):
  - which sources are cited by >=1 durable claim (in-Crystal) vs not
  - per-theme durable claim counts
  - reader-only themes: sources with OVP cards/core points but no durable claim yet

Usage:
    python3 scripts/m27_coverage.py \
        --ledger .run/m27/store/ledger.jsonl \
        --review .run/m26/article-review.json \
        --packs .run/m21/packs.json \
        --out .run/m27/crystal-coverage.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def load_ledger(p: Path):
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True, type=Path)
    ap.add_argument("--review", required=True, type=Path)
    ap.add_argument("--packs", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    events = load_ledger(args.ledger)
    # fold to active durable records
    state = {}
    for ev in events:
        r = ev["record"]
        state[r["claim_key"]] = (ev["op"], r)
    durable = [r for (op, r) in state.values() if op in ("write", "supersede")]

    packs = {c["case_id"]: c for c in load(args.packs)["cases"]}
    reviews = {r["case_id"]: r for r in load(args.review)["reviews"]}
    all_cases = sorted(packs)

    # source coverage: which cases are cited by >=1 durable claim
    case_to_claims: dict[str, list[str]] = {c: [] for c in all_cases}
    theme_counts: dict[str, int] = {}
    for r in durable:
        theme_counts[r["theme"]] = theme_counts.get(r["theme"], 0) + 1
        for c in r["source_cases"]:
            case_to_claims.setdefault(c, []).append(r["claim_id"])

    sources = []
    for cid in all_cases:
        p = packs[cid]
        in_crystal = len(case_to_claims.get(cid, [])) > 0
        rv = reviews.get(cid, {})
        sources.append({
            "case_id": cid,
            "title": p.get("title", ""),
            "category": p.get("category", ""),
            "in_crystal": in_crystal,
            "durable_claims_citing": sorted(set(case_to_claims.get(cid, []))),
            "ovp_card_count": p.get("n_cards", 0),
            "article_core_points": len(rv.get("core_points", [])),
        })

    covered = [s["case_id"] for s in sources if s["in_crystal"]]
    uncovered = [s["case_id"] for s in sources if not s["in_crystal"]]
    # reader-only: not in crystal but has cards (insight exists, not yet synthesized durable)
    reader_only = [s["case_id"] for s in sources if not s["in_crystal"] and s["ovp_card_count"] > 0]

    result = {
        "n_durable_claims": len(durable),
        "n_sources_total": len(all_cases),
        "n_sources_in_crystal": len(covered),
        "sources_in_crystal": covered,
        "sources_not_in_crystal": uncovered,
        "reader_only_sources": reader_only,
        "theme_counts": theme_counts,
        "sources": sources,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"coverage → {args.out}: {len(durable)} durable claims, "
          f"{len(covered)}/{len(all_cases)} sources in Crystal, "
          f"themes={len(theme_counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
