#!/usr/bin/env python3
"""M26 Article-level Memory Map AB — review-pack assembler (offline).

For each of the 20 held-out sources, assemble an ARTICLE-LEVEL comparison input:
  - source title + path (+ a short outline derived from the article's headings)
  - Knowledge Mem source-scoped memories (full content; the coarser-but-stable baseline)
  - OVP reader/memory cards (title + content; provenance/citations COLLAPSED — kept as
    counts + an optional details list, never the main surface)

This is the input both to the AI article-level judge and to the dashboard. The unit
of comparison is the whole article, NOT a single claim. Ground truth = source article;
KMEM is a reference arm, never ground truth. Reuses M20/M21 artifacts; no re-extraction.

Usage:
    python3 scripts/m26_review_pack.py \
        --packs .run/m21/packs.json \
        --kmem .run/m21/kmem/kmem.json \
        --sample .run/m18/sample.tsv \
        --out .run/m26/review-pack.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def outline(path: str, limit: int = 25) -> list[str]:
    """Markdown headings as a quick outline of the article."""
    if not path:
        return []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    heads = []
    in_fm = False
    for i, l in enumerate(lines):
        s = l.strip()
        if i == 0 and s == "---":
            in_fm = True
            continue
        if in_fm:
            if s == "---":
                in_fm = False
            continue
        if s.startswith("#"):
            heads.append(s.lstrip("#").strip())
        if len(heads) >= limit:
            break
    return heads


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packs", required=True, type=Path)
    ap.add_argument("--kmem", required=True, type=Path)
    ap.add_argument("--sample", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    packs = {c["case_id"]: c for c in load(args.packs)["cases"]}
    kmem = load(args.kmem).get("cases", {})
    meta = {}
    for line in args.sample.read_text(encoding="utf-8").splitlines():
        p = line.rstrip("\n").split("\t")
        if len(p) >= 3 and p[0] != "case_id":
            meta[p[0]] = {"path": p[1], "category": p[2]}

    cases = []
    for case_id in sorted(packs):
        pack = packs[case_id]
        m = meta.get(case_id, {})
        path = m.get("path", pack.get("path", ""))
        ovp_cards = [{
            "title": c.get("title", ""),
            "content": c.get("content", ""),
            "unit_type": c.get("unit_type"),
            "n_citations": len(c.get("evidence", []) or c.get("cited_unit_ids", [])),
        } for c in pack.get("cards", [])]
        kentry = kmem.get(case_id, {})
        kmem_mems = [{
            "title": x.get("title", ""),
            "content": x.get("content", ""),
        } for x in (kentry.get("memories") or [])]
        cases.append({
            "case_id": case_id,
            "source_title": pack.get("title", case_id),
            "source_path": path,
            "category": m.get("category", "?"),
            "outline": outline(path),
            "ovp_card_count": len(ovp_cards),
            "ovp_cards": ovp_cards,
            "kmem_memory_count": len(kmem_mems),
            "kmem_lifecycle": kentry.get("lifecycle_state"),
            "kmem_memories": kmem_mems,
        })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "policy": "Article-level AB: ground truth = source article; KMEM is a reference arm "
                  "(coarser/stable baseline), NOT ground truth; OVP is compared via its "
                  "reader/memory CARDS, not raw units. Provenance is collapsed, not the main UI.",
        "n_cases": len(cases),
        "cases": cases,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.out}: {len(cases)} cases, "
          f"{sum(c['ovp_card_count'] for c in cases)} OVP cards, "
          f"{sum(c['kmem_memory_count'] for c in cases)} KMEM memories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
