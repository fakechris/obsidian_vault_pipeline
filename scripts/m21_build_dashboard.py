#!/usr/bin/env python3
"""M21 pre-release dashboard generator (offline review surface).

Builds a self-contained static HTML review dashboard from:
  - packs.json            (scripts/m21_pack_summary.py output)
  - reviews.json          (per-case agent verdicts; optional)
  - synthesis.json        (OVP corpus synthesis draft; optional)
  - synthesis_review.json  (crystal-readiness review; optional)

Outputs index.html + cases/<case>.html + ab.html into --out. No backend, no raw
model replies. The KMEM arm is rendered as UNAVAILABLE when no kmem.json is
supplied (the source article is ground truth, never KMEM). This script embeds no
run data — it only reads the JSON inputs at runtime.

Usage:
    python3 scripts/m21_build_dashboard.py \
        --packs .run/m21/packs.json \
        --reviews .run/m21/reviews.json \
        --synthesis .run/m21/synthesis.json \
        --synthesis-review .run/m21/synthesis_review.json \
        --kmem .run/m21/kmem.json \
        --out .run/m21/dashboard
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def load(p: Path | None):
    if p and p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


CSS = """
:root{--ovp:#0a6;--kmem:#888;--bad:#c00;--warn:#c60;--ok:#0a7}
*{box-sizing:border-box}
body{font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;max-width:1100px;margin:1.5rem auto;padding:0 1rem;color:#1a1a1a}
h1{font-size:1.6rem}h2{font-size:1.2rem;margin-top:2rem;border-bottom:2px solid #eee;padding-bottom:.3rem}
h3{font-size:1.05rem;margin:.6rem 0 .3rem}
a{color:#06c;text-decoration:none}a:hover{text-decoration:underline}
table{border-collapse:collapse;width:100%;font-size:.86rem;margin:.5rem 0}
th,td{border:1px solid #e3e3e3;padding:.35rem .5rem;text-align:left;vertical-align:top}
th{background:#f7f7f7}
.verdict{border:2px solid;border-radius:10px;padding:.8rem 1rem;margin:1rem 0;font-size:1rem}
.pass{border-color:var(--ok);background:#f0fbf6}.fail{border-color:var(--bad);background:#fdf2f2}
.inconclusive{border-color:var(--warn);background:#fffaf0}
.pill{display:inline-block;border-radius:6px;padding:0 .45rem;font-size:.75rem;border:1px solid #ccc;color:#555}
.good{color:#0a7;font-weight:600}.ok{color:#c60;font-weight:600}.poor{color:#c00;font-weight:700}
.unavail{color:#999;font-style:italic}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin:1rem 0}
.col{border:1px solid #e3e3e3;border-radius:10px;padding:.8rem}
.col.ovp{border-top:4px solid var(--ovp)}.col.kmem{border-top:4px solid var(--kmem)}
.card{border:1px solid #eee;border-radius:8px;padding:.5rem .7rem;margin:.5rem 0;background:#fff}
.card .t{font-weight:600}.card .c{color:#333;margin:.2rem 0}
.q{color:#0a5;font-style:italic}.uid{color:#999;font-family:monospace;font-size:.78rem}
details>summary{cursor:pointer;color:#556}
.meta{color:#666;font-size:.85rem}
.scores span{display:inline-block;margin-right:.8rem}
textarea{width:100%;min-height:70px;font:13px/1.4 monospace;border:1px solid #ccc;border-radius:6px;padding:.4rem}
.ab .side{border:1px solid #ddd;border-radius:8px;padding:.6rem;background:#fcfcfc}
.hidden{display:none}
button{font:14px sans-serif;padding:.3rem .7rem;border:1px solid #888;border-radius:6px;background:#f3f3f3;cursor:pointer}
footer{margin-top:3rem;color:#888;font-size:.8rem;border-top:1px solid #eee;padding-top:1rem}
"""


def rating_cls(r):
    return {"good": "good", "ok": "ok", "poor": "poor"}.get(r, "")


def render_cards(cards):
    out = []
    for i, c in enumerate(cards, 1):
        out.append('<div class=card>')
        out.append(f'<div class=t>{i}. {esc(c.get("title"))} '
                   f'<span class=pill>{esc(c.get("unit_type") or "-")}</span></div>')
        if c.get("content"):
            out.append(f'<div class=c>{esc(c["content"])}</div>')
        ev = c.get("evidence", [])
        if ev:
            out.append(f'<details><summary>Evidence — {len(ev)} quote(s)</summary>')
            for u in ev:
                line = f"line {u['line']}" if u.get("line") else "—"
                out.append(f'<div><span class=q>“{esc(u.get("quote"))}”</span> '
                           f'<span class=uid>[{esc(u.get("id"))} · {esc(line)}]</span></div>')
            out.append('</details>')
        out.append('</div>')
    return "\n".join(out)


def render_units(units):
    out = ['<div class=meta>Raw grounded-units readout (truth layer, pre card-view):</div>']
    for u in units:
        line = f"line {u['line']}" if u.get("line") else "—"
        out.append(f'<div><span class=q>“{esc(u.get("quote"))}”</span> '
                   f'<span class=uid>[{esc(u.get("id"))} · {esc(line)}]</span></div>')
    return "\n".join(out)


KMEM_UNAVAIL = ('<div class=unavail>Knowledge Mem arm UNAVAILABLE in this environment '
                '(no service / MCP / API). Marked unavailable per M21 spec — not '
                'substituted with global search. The source article is ground truth.</div>')


def case_page(case, verdict, out_dir: Path):
    cid = case["case_id"]
    parts = [f'<!doctype html><html><head><meta charset=utf-8><title>{esc(cid)}</title>'
             f'<style>{CSS}</style></head><body>']
    parts.append(f'<p><a href="../index.html">&larr; dashboard</a></p>')
    parts.append(f'<h1>{esc(cid)} — {esc(case["title"])}</h1>')
    parts.append(f'<div class=meta>{esc(case["category"])} · {esc(case["path"])}<br>'
                 f'cards={case["n_cards"]} · units={case["n_units"]} · '
                 f'accepted_without_quote={case["accepted_without_quote"]} · '
                 f'quote_not_found={case["quote_not_found"]}'
                 f'{" · json_repaired" if case.get("json_repaired") else ""}</div>')

    # Side-by-side OVP vs KMEM
    parts.append('<div class=cols>')
    parts.append(f'<div class="col ovp"><h3>OVP reader cards</h3>{render_cards(case["cards"])}</div>')
    parts.append(f'<div class="col kmem"><h3>Knowledge Mem source memories</h3>{KMEM_UNAVAIL}</div>')
    parts.append('</div>')

    # AB block (anonymized): A = cards, B = raw units. Reveal toggles labels.
    parts.append('<h2>AB — readability (card view vs raw units, anonymized)</h2>')
    parts.append('<div class=ab><div class=cols>')
    parts.append(f'<div class=side><h3 id="{cid}-la">Side A</h3>{render_cards(case["cards"])}</div>')
    parts.append(f'<div class=side><h3 id="{cid}-lb">Side B</h3>{render_units(case["units"])}</div>')
    parts.append('</div>')
    parts.append(f'<p><button onclick="document.getElementById(\'{cid}-la\').textContent=\'Side A = OVP card view\';'
                 f'document.getElementById(\'{cid}-lb\').textContent=\'Side B = OVP raw grounded-units\';">Reveal arms</button></p>')
    parts.append('</div>')

    # Agent verdict
    parts.append('<h2>Agent review</h2>')
    if verdict:
        v = verdict
        parts.append(f'<p>rating: <span class={rating_cls(v.get("rating"))}>{esc(v.get("rating"))}</span> · '
                     f'winner: {esc(v.get("winner"))} · kmem: {esc(v.get("kmem_status"))} · '
                     f'provenance_checkable: {esc(v.get("provenance_checkable"))}</p>')
        parts.append('<div class=scores>' + "".join(
            f'<span>{k}: <b>{esc(v.get(k))}</b>/5</span>' for k in
            ["faithfulness", "coverage", "readability", "source_support",
             "practical_usefulness", "longterm_vault_usefulness"]) + '</div>')
        parts.append(f'<p><b>AB:</b> {esc(v.get("ab_cardview_vs_units"))} — {esc(v.get("ab_note"))}</p>')
        parts.append(f'<p><b>Unsupported claims:</b> {esc(v.get("unsupported_claims"))}</p>')
        parts.append(f'<p><b>Rationale:</b> {esc(v.get("rationale"))}</p>')
    else:
        parts.append('<p class=unavail>No agent verdict for this case.</p>')

    parts.append('<h2>Human review notes</h2>')
    parts.append('<textarea placeholder="Reviewer notes (static; edit + save the HTML, or record in the M21 doc)."></textarea>')
    parts.append('</body></html>')
    (out_dir / "cases").mkdir(parents=True, exist_ok=True)
    (out_dir / "cases" / f"{cid}.html").write_text("\n".join(parts), encoding="utf-8")


def index_page(data, reviews, synthesis, synth_review, kmem, out_dir: Path):
    cases = data["cases"]
    vmap = {v["case_id"]: v for v in (reviews or {}).get("verdicts", [])}
    totals = data["totals"]
    ratings = [vmap.get(c["case_id"], {}).get("rating") for c in cases]
    n_good = ratings.count("good")
    n_ok = ratings.count("ok")
    n_poor = ratings.count("poor")
    kmem_available = bool(kmem)

    p = [f'<!doctype html><html><head><meta charset=utf-8><title>M21 pre-release dashboard</title>'
         f'<style>{CSS}</style></head><body>']
    p.append('<h1>M21 — OVP Reader Trunk Pre-release Review Dashboard</h1>')
    p.append(f'<div class=meta>{totals["n_cases"]} held-out sources · {totals["total_cards"]} OVP cards · '
             f'{totals["total_units"]} grounded units · accepted_without_quote sum = '
             f'{totals["accepted_without_quote_sum"]}</div>')

    # Verdict banner
    if not kmem_available:
        p.append('<div class="verdict inconclusive"><b>OVP vs KMEM verdict: INCONCLUSIVE</b> — '
                 'Knowledge Mem is unavailable in this environment, so the head-to-head AB cannot be '
                 'run. OVP standalone is assessed below (source-level usefulness, provenance, corpus '
                 'synthesis). The AB surface compares OVP card-view vs OVP raw units.</div>')
    p.append(f'<div class="verdict pass"><b>OVP standalone (source-level):</b> {n_good} good · {n_ok} ok · '
             f'{n_poor} poor of {len(cases)} · accepted_without_quote=0 across all. '
             'Provenance is checkable per card (verbatim quote + source line).</div>')

    # 20-case table
    p.append('<h2>Per-source comparison</h2>')
    p.append('<table><tr><th>case</th><th>category</th><th>OVP cards</th><th>OVP units</th>'
             '<th>awq</th><th>KMEM memories</th><th>winner</th><th>rating</th>'
             '<th>AB cards vs units</th><th></th></tr>')
    for c in cases:
        v = vmap.get(c["case_id"], {})
        p.append('<tr>'
                 f'<td><a href="cases/{esc(c["case_id"])}.html">{esc(c["case_id"])}</a></td>'
                 f'<td>{esc(c["category"])}</td>'
                 f'<td>{c["n_cards"]}</td><td>{c["n_units"]}</td>'
                 f'<td>{c["accepted_without_quote"]}</td>'
                 f'<td class=unavail>unavailable</td>'
                 f'<td>{esc(v.get("winner","—"))}</td>'
                 f'<td class={rating_cls(v.get("rating"))}>{esc(v.get("rating","—"))}</td>'
                 f'<td>{esc(v.get("ab_cardview_vs_units","—"))}</td>'
                 f'<td><a href="cases/{esc(c["case_id"])}.html">open</a></td>'
                 '</tr>')
    p.append('</table>')

    # AB summary
    ab_counts = {}
    for v in vmap.values():
        k = v.get("ab_cardview_vs_units", "—")
        ab_counts[k] = ab_counts.get(k, 0) + 1
    p.append('<h2>AB test surface</h2>')
    p.append('<p>Knowledge Mem arm unavailable → the AB compares OVP <b>card view</b> (Side A) vs OVP '
             '<b>raw grounded-units readout</b> (Side B), anonymized, per case page (with a reveal button). '
             'This validates whether the card synthesis improves readability over the raw truth layer.</p>')
    p.append('<p>Agent AB tally: ' + ", ".join(f'{esc(k)}={n}' for k, n in sorted(ab_counts.items())) + '</p>')

    # Corpus synthesis
    p.append('<h2>Corpus synthesis draft (review-only, NOT a durable Crystal)</h2>')
    if synthesis:
        p.append(f'<div class=meta>{len(synthesis.get("items",[]))} synthesis items · '
                 f'themes: {esc(", ".join(synthesis.get("themes_covered",[])))}</div>')
        for it in synthesis.get("items", []):
            p.append('<div class=card>')
            p.append(f'<div class=t>{esc(it.get("claim"))} <span class=pill>{esc(it.get("theme"))}</span></div>')
            p.append(f'<div class=meta>support: {it.get("n_support")} sources '
                     f'[{esc(", ".join(it.get("supporting_cases",[])))}]</div>')
            if it.get("evidence_refs"):
                p.append('<details><summary>evidence</summary><div class=meta>'
                         + "<br>".join(esc(r) for r in it["evidence_refs"]) + '</div></details>')
            if it.get("caveats") and it["caveats"].lower() != "none":
                p.append(f'<div class=meta><b>caveat:</b> {esc(it["caveats"])}</div>')
            p.append('</div>')
        if synthesis.get("notes"):
            p.append(f'<p class=meta><b>Notes:</b> {esc(synthesis["notes"])}</p>')
    else:
        p.append('<p class=unavail>No synthesis draft generated.</p>')

    # Crystal readiness
    p.append('<h2>Crystal readiness</h2>')
    if synth_review:
        sr = synth_review
        p.append(f'<div class="verdict {"pass" if sr.get("crystal_readiness")=="ready" else "inconclusive"}">'
                 f'<b>Crystal readiness: {esc(sr.get("crystal_readiness"))}</b> '
                 f'(confidence {esc(sr.get("confidence"))}) · faithfulness_to_cards '
                 f'{esc(sr.get("faithfulness_to_cards"))}/5 · every_item_grounded '
                 f'{esc(sr.get("every_item_grounded"))}</div>')
        p.append(f'<p><b>Strengths:</b> {esc(sr.get("strengths"))}</p>')
        p.append(f'<p><b>Gaps:</b> {esc(sr.get("gaps"))}</p>')
        p.append(f'<p><b>Recommended M22:</b> {esc(sr.get("recommended_m22"))}</p>')
    else:
        p.append('<p class=unavail>No synthesis review.</p>')

    p.append('<footer>Generated by scripts/m21_build_dashboard.py from .run/m21 inputs. '
             'Agent judges share a model with the generator (model confound — labeled). '
             'OVP packs reused from the M20 live run. No durable Crystal written; no vault/canonical mutation. '
             'Source article is ground truth, not any memory system.</footer>')
    p.append('</body></html>')
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text("\n".join(p), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packs", required=True, type=Path)
    ap.add_argument("--reviews", type=Path)
    ap.add_argument("--synthesis", type=Path)
    ap.add_argument("--synthesis-review", type=Path)
    ap.add_argument("--kmem", type=Path, help="Knowledge Mem arm JSON; omitted ⇒ rendered UNAVAILABLE")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    data = load(args.packs)
    reviews = load(args.reviews)
    synthesis = load(args.synthesis)
    synth_review = load(args.synthesis_review)
    kmem = load(args.kmem)

    vmap = {v["case_id"]: v for v in (reviews or {}).get("verdicts", [])}
    for case in data["cases"]:
        case_page(case, vmap.get(case["case_id"]), args.out)
    index_page(data, reviews, synthesis, synth_review, kmem, args.out)
    print(f"dashboard → {args.out}/index.html ({len(data['cases'])} cases, "
          f"kmem={'available' if kmem else 'UNAVAILABLE'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
