#!/usr/bin/env python3
"""M21 pre-release dashboard — pack summarizer (offline review helper).

Reads OVP `read-source` reader packs from a dogfood run directory and emits a
single compact JSON summary the dashboard generator + review agents consume.
Pure diagnostic/eval tooling: never writes the vault/canonical store, never the
runtime architecture (see repo AGENTS/README). No raw run data is embedded in
this script — it only reads packs at runtime.

Usage:
    python3 scripts/m21_pack_summary.py \
        --packs .run/m20/dogfood \
        --sample .run/m18/sample.tsv \
        --out .run/m21/packs.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_sample(sample_tsv: Path) -> dict[str, dict]:
    meta: dict[str, dict] = {}
    if not sample_tsv.exists():
        return meta
    for line in sample_tsv.read_text(encoding="utf-8").splitlines():
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 3 or parts[0] == "case_id":
            continue
        meta[parts[0]] = {"path": parts[1], "category": parts[2]}
    return meta


def unit_index(units: list[dict]) -> dict[str, dict]:
    idx = {}
    for u in units:
        loc = u.get("evidence", {}).get("location") or {}
        idx[u["id"]] = {
            "id": u["id"],
            "quote": u.get("evidence", {}).get("quote", ""),
            "line": loc.get("line"),
            "kind": u.get("kind"),
        }
    return idx


def summarize_case(case_dir: Path, meta: dict) -> dict | None:
    rs = case_dir / "run-status.json"
    if not rs.exists():
        return None
    status = json.loads(rs.read_text(encoding="utf-8"))
    cards = json.loads((case_dir / "cards.json").read_text(encoding="utf-8")) \
        if (case_dir / "cards.json").exists() else []
    units = json.loads((case_dir / "units.accepted.json").read_text(encoding="utf-8")) \
        if (case_dir / "units.accepted.json").exists() else []
    uidx = unit_index(units)

    card_rows = []
    for c in cards:
        cites = [uidx[cid] for cid in c.get("cited_unit_ids", []) if cid in uidx]
        card_rows.append({
            "title": c.get("title", ""),
            "content": c.get("content", ""),
            "unit_type": c.get("unit_type"),
            "cited_unit_ids": c.get("cited_unit_ids", []),
            "evidence": cites,
        })

    return {
        "case_id": case_dir.name,
        "title": status.get("source", case_dir.name),
        "path": meta.get("path", ""),
        "category": meta.get("category", "?"),
        "n_cards": status.get("cards", len(cards)),
        "n_units": status.get("accepted_units", len(units)),
        "accepted_without_quote": status.get("accepted_without_quote", 0),
        "quote_not_found": status.get("quote_not_found", 0),
        "needs_review": status.get("needs_review", 0),
        "json_repaired": status.get("json_repaired", False),
        "cards": card_rows,
        # compact raw-units readout (the truth layer pre-card-view) for the AB arm.
        "units": [{"id": u["id"], "line": u["line"], "quote": u["quote"]} for u in uidx.values()],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packs", required=True, type=Path, help="dogfood dir with m18-NN/ packs")
    ap.add_argument("--sample", required=True, type=Path, help="sample.tsv (case_id, path, category)")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    meta = load_sample(args.sample)
    cases = []
    for case_dir in sorted(p for p in args.packs.iterdir() if p.is_dir()):
        s = summarize_case(case_dir, meta.get(case_dir.name, {}))
        if s:
            cases.append(s)

    totals = {
        "n_cases": len(cases),
        "total_cards": sum(c["n_cards"] for c in cases),
        "total_units": sum(c["n_units"] for c in cases),
        "accepted_without_quote_sum": sum(c["accepted_without_quote"] for c in cases),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"totals": totals, "cases": cases}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"wrote {args.out}: {totals}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
