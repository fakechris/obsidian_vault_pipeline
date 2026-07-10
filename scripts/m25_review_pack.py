#!/usr/bin/env python3
"""M25 Crystal Review Workbench — review-pack assembler (offline review helper).

For each caveated claim in a crystal-write `review.json`, assembles everything a
reviewer (and the AI evidence-review step) needs, so nobody reads raw JSON or
hunts for quotes:

  - the OVP claim + theme + why-not-durable rationale
  - OVP cited units: quote + resolved source line (from the candidate + packs)
  - a short source excerpt around each cited line (ground truth)
  - KMEM source-scoped comparable memories for the claim's cited cases
    (旁证 / reference-only — KMEM has NO sentence-level provenance, so it can
    inform but never judge; ground truth is the source quote)

Pure assembly from existing artifacts; no gate logic, no durability decision.
Embeds no run data — reads inputs at runtime.

Usage:
    python3 scripts/m25_review_pack.py \
        --review .run/m24/store/review.json \
        --candidate .run/m22/candidate.json \
        --packs .run/m21/packs.json \
        --kmem .run/m21/kmem/kmem.json \
        --sample .run/m18/sample.tsv \
        --out .run/m25/review-pack.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def source_excerpt(path: str, line: int | None, quote: str = "", radius: int = 2) -> str:
    """Source context for a cited quote. Prefer locating the verbatim `quote`
    directly (the unit's resolved `line` is a paragraph-granular anchor and can
    sit a few lines before the sentence); fall back to a window around `line`."""
    if not path:
        return ""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    anchor = None
    needle = (quote or "").strip()[:40]
    if needle:
        for i, l in enumerate(lines):
            if needle in l:
                anchor = i
                break
    if anchor is None and line:
        anchor = line - 1
    if anchor is None:
        return ""
    lo = max(0, anchor - radius)
    hi = min(len(lines), anchor + radius + 1)
    return "\n".join(lines[lo:hi]).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", required=True, type=Path)
    ap.add_argument("--candidate", required=True, type=Path)
    ap.add_argument("--packs", required=True, type=Path)
    ap.add_argument("--kmem", required=True, type=Path)
    ap.add_argument("--sample", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    review = load(args.review)["review"]
    candidate = {c["id"]: c for c in load(args.candidate)["items"]}
    packs = {c["case_id"]: c for c in load(args.packs)["cases"]}
    kmem = load(args.kmem).get("cases", {})

    # case_id -> source path (for excerpts)
    src_path: dict[str, str] = {}
    for line in args.sample.read_text(encoding="utf-8").splitlines():
        parts = line.rstrip("\n").split("\t")
        if len(parts) >= 2 and parts[0] != "case_id":
            src_path[parts[0]] = parts[1]

    def unit_line(case_id: str, unit_id: str) -> int | None:
        c = packs.get(case_id) or {}
        for u in c.get("units", []):
            if u["id"] == unit_id:
                return u.get("line")
        return None

    cards = []
    for r in review:
        cid = r["claim_id"]
        cand = candidate.get(cid, {})
        cited = []
        cases = []
        for cit in cand.get("citations", []):
            case_id = cit["case_id"]
            cases.append(case_id)
            line = unit_line(case_id, cit["unit_id"])
            cited.append({
                "case_id": case_id,
                "unit_id": cit["unit_id"],
                "quote": cit["quote"],
                "line": line,
                "source_excerpt": source_excerpt(src_path.get(case_id, ""), line, cit["quote"]),
            })
        cases = sorted(set(cases))
        # KMEM 旁证: source-scoped memories for the claim's cited cases.
        kmem_ref = []
        for case_id in cases:
            entry = kmem.get(case_id) or {}
            for m in (entry.get("memories") or []):
                kmem_ref.append({
                    "case_id": case_id,
                    "title": m.get("title", ""),
                    "content": m.get("content", ""),
                    "lifecycle": entry.get("lifecycle_state"),
                })
        cards.append({
            "claim_id": cid,
            "claim": r.get("claim", ""),
            "theme": r.get("theme", ""),
            "final_class": r.get("final_class"),
            "strength": r.get("strength"),
            "evidence_sufficient": r.get("evidence_sufficient"),
            "why_not_durable": r.get("rationale", ""),
            "ovp_evidence": cited,
            "source_cases": cases,
            "kmem_reference": kmem_ref,  # 旁证 only — not ground truth, no sentence-level provenance
        })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "kmem_policy": "reference-only: KMEM source memories are 旁证. KMEM has no "
                       "sentence-level provenance and never decides durability; ground "
                       "truth is the OVP source quote.",
        "n_claims": len(cards),
        "cards": cards,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    total_kmem = sum(len(c["kmem_reference"]) for c in cards)
    print(f"wrote {args.out}: {len(cards)} caveated claim(s), "
          f"{sum(len(c['ovp_evidence']) for c in cards)} OVP citations, "
          f"{total_kmem} KMEM reference memories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
