#!/usr/bin/env python3
"""M25 Crystal Review Workbench — static HTML generator (offline review surface).

Combines the review-pack (m25_review_pack.py) with the AI evidence review into a
single self-contained HTML workbench: one card per caveated claim showing the OVP
claim, why it was caveated, OVP evidence quotes + source line + excerpt, KMEM 旁证
(reference-only, visually fenced off), the AI recommendation + suggested rewrite,
and a human decision field. Also emits a `decisions.template.json` the reviewer
edits and feeds to `ovp-next crystal-review apply`.

No backend; embeds no run data (reads inputs at runtime). KMEM is rendered as
reference-only and is never an input to any gate.

Usage:
    python3 scripts/m25_build_workbench.py \
        --pack .run/m25/review-pack.json \
        --ai-review .run/m25/ai-reviews.json \
        --out .run/m25/workbench
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p and p.exists() else None


CSS = """
body{font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;max-width:1000px;margin:1.5rem auto;padding:0 1rem;color:#1a1a1a}
h1{font-size:1.5rem}h2{font-size:1.05rem;margin:.2rem 0}
.card{border:1px solid #ddd;border-radius:10px;padding:1rem 1.1rem;margin:1.2rem 0;background:#fff}
.claim{font-weight:600;font-size:1.05rem;margin:.2rem 0 .4rem}
.tag{display:inline-block;border:1px solid #ccc;border-radius:6px;padding:0 .45rem;font-size:.72rem;color:#555;margin-left:.3rem}
.why{background:#fffaf0;border-left:3px solid #c60;padding:.5rem .7rem;margin:.5rem 0;font-size:.9rem}
.ev{margin:.3rem 0 .3rem .3rem}.q{color:#0a5;font-style:italic}.uid{color:#999;font-family:monospace;font-size:.78rem}
.exc{color:#555;font-size:.82rem;background:#f7f7f7;border-radius:6px;padding:.3rem .5rem;margin:.2rem 0 .5rem .8rem;white-space:pre-wrap}
.kmem{border:1px dashed #bbb;border-radius:8px;padding:.5rem .7rem;margin:.5rem 0;background:#fafafe}
.kmem .lbl{color:#967;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em}
.ai{border-left:3px solid #06c;background:#f3f8ff;padding:.5rem .7rem;margin:.6rem 0;font-size:.92rem}
.rec{font-weight:700}
.rec.promote,.rec.rewrite,.rec.split{color:#0a7}.rec.keep_caveated{color:#c60}.rec.reject{color:#c00}
.suggest{background:#f0fbf6;border:1px solid #bde;border-radius:6px;padding:.4rem .6rem;margin:.3rem 0}
.decision{background:#fffef5;border:1px dashed #cc8;border-radius:6px;padding:.4rem .6rem;margin:.5rem 0;font-size:.85rem;color:#660}
details>summary{cursor:pointer;color:#556}
.meta{color:#666;font-size:.85rem}
code{background:#f0f0f0;padding:0 .25rem;border-radius:3px}
"""

REC_CLS = {"promote": "promote", "rewrite": "rewrite", "split": "split",
           "keep_caveated": "keep_caveated", "reject": "reject"}


def render(pack: dict, ai: dict, out_dir: Path):
    ai_by = {r["claim_id"]: r for r in (ai or {}).get("reviews", [])}
    cards = pack["cards"]
    p = [f'<!doctype html><html><head><meta charset=utf-8><title>Crystal Review Workbench</title>'
         f'<style>{CSS}</style></head><body>']
    p.append('<h1>Crystal Review Workbench — caveated claims</h1>')
    p.append(f'<p class=meta>{len(cards)} caveated claim(s). Your job: judge whether each insight is '
             'worth durable Crystal and whether the rewrite is faithful. AI moved the evidence and '
             'proposed rewrites; the <b>gate</b> (citation linter + claim-strength), not this page, '
             'makes anything durable. Edit <code>decisions.template.json</code> then run '
             '<code>ovp-next crystal-review apply</code> → strength gate → <code>crystal-write</code>.</p>')
    p.append('<p class=meta><b>KMEM boxes are 旁证 / reference-only</b> — Knowledge Mem has no '
             'sentence-level provenance and never decides durability; ground truth is the OVP source quote.</p>')

    for c in cards:
        cid = c["claim_id"]
        r = ai_by.get(cid, {})
        p.append('<div class=card>')
        p.append(f'<div class=claim>{esc(cid)}. {esc(c["claim"])}'
                 f'<span class=tag>{esc(c["theme"])}</span>'
                 f'<span class=tag>{esc(c.get("final_class"))}</span>'
                 f'<span class=tag>{esc(c.get("strength"))}</span></div>')
        p.append(f'<div class=why><b>Why not durable:</b> {esc(c["why_not_durable"])}</div>')

        p.append('<h2>OVP evidence (ground truth)</h2>')
        for e in c["ovp_evidence"]:
            line = f"line {e['line']}" if e.get("line") else "—"
            p.append(f'<div class=ev><span class=q>“{esc(e["quote"])}”</span> '
                     f'<span class=uid>[{esc(e["case_id"])} · {esc(e["unit_id"])} · {esc(line)}]</span></div>')
            if e.get("source_excerpt"):
                p.append(f'<div class=exc>{esc(e["source_excerpt"])}</div>')

        kref = c.get("kmem_reference", [])
        p.append(f'<details class=kmem><summary><span class=lbl>KMEM 旁证 — reference only, NOT ground truth</span> '
                 f'({len(kref)} memories)</summary>')
        for m in kref[:8]:
            p.append(f'<div class=ev>({esc(m["case_id"])}) <b>{esc(m["title"])}</b><br>'
                     f'<span class=meta>{esc((m.get("content") or "")[:240])}</span></div>')
        p.append('</details>')

        if r:
            rec = r.get("recommendation", "")
            p.append('<div class=ai>')
            p.append(f'<div><span class="rec {REC_CLS.get(rec,"")}">AI recommendation: {esc(rec)}</span> '
                     f'· risk: {esc(r.get("risk"))} · KMEM: {esc(r.get("kmem_relation"))}</div>')
            p.append(f'<div class=meta><b>Supported:</b> {esc(r.get("supported_parts"))}</div>')
            p.append(f'<div class=meta><b>Over-strong:</b> {esc(r.get("overstrong_terms"))}</div>')
            p.append(f'<div class=meta><b>KMEM note:</b> {esc(r.get("kmem_note"))}</div>')
            if r.get("suggested_claim"):
                p.append(f'<div class=suggest><b>Suggested rewrite:</b> {esc(r["suggested_claim"])}'
                         + (f'<br><span class=meta>drop citations: {esc(", ".join(r.get("suggested_citations_drop", [])))}</span>'
                            if r.get("suggested_citations_drop") else "") + '</div>')
            p.append(f'<div class=meta><b>Rationale:</b> {esc(r.get("rationale"))}</div>')
            p.append('</div>')

        p.append(f'<div class=decision><b>Human decision (edit decisions.template.json):</b> '
                 f'set <code>action</code> = accept rewrite / split / keep_caveated / reject for <code>{esc(cid)}</code>. '
                 'A rewrite must stay faithful to the quotes above; the gate will re-check it.</div>')
        p.append('</div>')

    p.append('</body></html>')
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text("\n".join(p), encoding="utf-8")

    # Decisions template, prefilled with the AI recommendation as a starting point
    # (the human edits action + revisions before applying).
    template = []
    for c in cards:
        cid = c["claim_id"]
        r = ai_by.get(cid, {})
        rec = r.get("recommendation", "keep_caveated")
        action = {"promote": "rewrite", "rewrite": "rewrite", "split": "split",
                  "keep_caveated": "keep_caveated", "reject": "reject"}.get(rec, "keep_caveated")
        entry = {"claim_id": cid, "action": action, "revisions": [], "note": "AI-suggested; human to confirm"}
        if action in ("rewrite", "split") and r.get("suggested_claim"):
            drop = set(r.get("suggested_citations_drop", []))
            orig_citations = []
            for e in c["ovp_evidence"]:
                if e["unit_id"] not in drop:
                    orig_citations.append({"case_id": e["case_id"], "unit_id": e["unit_id"], "quote": e["quote"]})
            entry["revisions"] = [{
                "id": f"{cid}r", "claim": r["suggested_claim"], "theme": c["theme"],
                "citations": orig_citations, "caveat": "none",
            }]
        template.append(entry)
    (out_dir / "decisions.template.json").write_text(
        json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"workbench → {out_dir}/index.html ({len(cards)} cards); "
          f"decisions template → {out_dir}/decisions.template.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True, type=Path)
    ap.add_argument("--ai-review", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    pack = load(args.pack)
    ai = load(args.ai_review)
    render(pack, ai, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
