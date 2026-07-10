#!/usr/bin/env python3
"""M26 Article-level Memory Map AB dashboard (bilingual, offline).

Builds the MAIN review surface (replacing M25 micro-review as the acceptance entry):
index + per-case pages comparing, for each source article, KMEM memories vs OVP
cards against the article's core points — at the ARTICLE level. All human-facing
text is bilingual (EN + 中文). Provenance is collapsed (details), never the main UI.

Inputs:
  - review-pack.json   (scripts/m26_review_pack.py)
  - article-review.json (the AI article-level judge output: {reviews:[...]})

Usage:
    python3 scripts/m26_build_dashboard.py \
        --pack .run/m26/review-pack.json \
        --review .run/m26/article-review.json \
        --out .run/m26/dashboard
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
body{font:15px/1.55 -apple-system,Segoe UI,Roboto,'PingFang SC','Microsoft YaHei',sans-serif;max-width:1080px;margin:1.4rem auto;padding:0 1rem;color:#1a1a1a}
h1{font-size:1.5rem}h2{font-size:1.1rem;margin:1.4rem 0 .4rem;border-bottom:1px solid #eee;padding-bottom:.2rem}
h3{font-size:1rem;margin:.5rem 0 .2rem}
a{color:#06c;text-decoration:none}a:hover{text-decoration:underline}
table{border-collapse:collapse;width:100%;font-size:.86rem;margin:.5rem 0}
th,td{border:1px solid #e3e3e3;padding:.35rem .5rem;text-align:left;vertical-align:top}
th{background:#f7f7f7}
.zh{color:#447;font-size:.92em}
.verdict{display:inline-block;border-radius:6px;padding:.05rem .5rem;font-weight:700;font-size:.85rem}
.ovp_better{background:#e6f7ee;color:#0a6;border:1px solid #0a6}
.kmem_better{background:#fdeee6;color:#c60;border:1px solid #c60}
.tie{background:#eef;color:#55a;border:1px solid #88c}
.inconclusive{background:#f3f3f3;color:#888;border:1px solid #bbb}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
.col{border:1px solid #e3e3e3;border-radius:10px;padding:.7rem .9rem}
.col.ovp{border-top:4px solid #0a6}.col.kmem{border-top:4px solid #c60}
.card{border:1px solid #eee;border-radius:6px;padding:.4rem .6rem;margin:.4rem 0;background:#fff}
.card .t{font-weight:600;font-size:.92rem}.card .c{color:#444;font-size:.86rem}
.cov-covered{color:#0a7;font-weight:700}.cov-partial{color:#c60}.cov-missing{color:#c00}
.issue{background:#fdf2f2;border-left:3px solid #c00;padding:.3rem .5rem;margin:.3rem 0;font-size:.85rem}
.note{color:#555;font-size:.88rem;margin:.2rem 0}
.meta{color:#666;font-size:.85rem}
details>summary{cursor:pointer;color:#667}
footer{margin-top:2.5rem;color:#888;font-size:.8rem;border-top:1px solid #eee;padding-top:.8rem}
.banner{background:#f0f6ff;border:1px solid #bcd;border-radius:8px;padding:.6rem .9rem;margin:.8rem 0;font-size:.9rem}
"""

VCLS = {"ovp_better": "ovp_better", "kmem_better": "kmem_better", "tie": "tie", "inconclusive": "inconclusive"}
VLABEL = {"ovp_better": "OVP better", "kmem_better": "KMEM better", "tie": "tie", "inconclusive": "inconclusive"}


def case_page(pack_case: dict, rev: dict, out_dir: Path):
    cid = pack_case["case_id"]
    p = [f'<!doctype html><html><head><meta charset=utf-8><title>{esc(cid)}</title><style>{CSS}</style></head><body>']
    p.append('<p><a href="../index.html">&larr; dashboard</a></p>')
    p.append(f'<h1>{esc(cid)} — {esc(pack_case["source_title"])}</h1>')
    p.append(f'<div class=meta>{esc(pack_case.get("category"))} · {esc(pack_case["source_path"])}<br>'
             f'KMEM memories: {pack_case["kmem_memory_count"]} · OVP cards: {pack_case["ovp_card_count"]}</div>')

    if rev:
        v = rev.get("verdict", "inconclusive")
        p.append(f'<p>Verdict / 结论: <span class="verdict {VCLS.get(v,"")}">{esc(VLABEL.get(v,v))}</span> '
                 f'· core coverage — OVP {rev.get("ovp_covered_points","?")} / KMEM {rev.get("kmem_covered_points","?")} '
                 f'of {len(rev.get("core_points",[]))}</p>')
        p.append(f'<div class=banner><b>Why / 理由</b><br>EN: {esc(rev.get("rationale_en"))}'
                 f'<br><span class=zh>ZH: {esc(rev.get("rationale_zh"))}</span></div>')

        # Core points checklist
        p.append('<h2>Article core points / 文章核心点 — coverage</h2>')
        p.append('<table><tr><th>#</th><th>Core point / 核心点</th><th>KMEM</th><th>OVP</th></tr>')
        for i, cp in enumerate(rev.get("core_points", []), 1):
            def cell(s):
                return f'<span class=cov-{esc(s)}>{esc(s)}</span>'
            p.append(f'<tr><td>{i}</td><td>{esc(cp.get("point_en"))}<br><span class=zh>{esc(cp.get("point_zh"))}</span></td>'
                     f'<td>{cell(cp.get("kmem","?"))}</td><td>{cell(cp.get("ovp","?"))}</td></tr>')
        p.append('</table>')
        if rev.get("missed_points"):
            p.append('<div class=note><b>Missed by both / 两边都漏:</b> ' + esc("; ".join(rev["missed_points"])) + '</div>')

    # Side-by-side memories vs cards
    p.append('<h2>KMEM memories vs OVP cards</h2>')
    p.append('<div class=cols>')
    p.append('<div class="col kmem"><h3>KMEM source memories (reference baseline / 参照基线)</h3>')
    for m in load_case_inputs(pack_case).get("kmem_memories", []):
        p.append(f'<div class=card><div class=t>{esc(m.get("title"))}</div><div class=c>{esc(m.get("content"))}</div></div>')
    p.append('</div>')
    p.append('<div class="col ovp"><h3>OVP cards (provenance collapsed / 证据折叠)</h3>')
    for m in load_case_inputs(pack_case).get("ovp_cards", []):
        p.append(f'<div class=card><div class=t>{esc(m.get("title"))}</div><div class=c>{esc(m.get("content"))}</div></div>')
    p.append('</div></div>')

    if rev:
        p.append('<h2>Factual issues / 事实问题</h2>')
        p.append('<h3>KMEM</h3>')
        ki = rev.get("kmem_factual_issues", [])
        p.append("".join(f'<div class=issue>{esc(x)}</div>' for x in ki) or '<div class=note>none / 无</div>')
        p.append('<h3>OVP</h3>')
        oi = rev.get("ovp_factual_issues", [])
        p.append("".join(f'<div class=issue>{esc(x)}</div>' for x in oi) or '<div class=note>none / 无</div>')

        p.append('<h2>Granularity & usability / 颗粒度与可用性</h2>')
        p.append(f'<div class=note><b>KMEM:</b> {esc(rev.get("kmem_granularity_notes_en"))}'
                 f'<br><span class=zh>{esc(rev.get("kmem_granularity_notes_zh"))}</span></div>')
        p.append(f'<div class=note><b>OVP:</b> {esc(rev.get("ovp_granularity_notes_en"))}'
                 f'<br><span class=zh>{esc(rev.get("ovp_granularity_notes_zh"))}</span></div>')

    # provenance / outline collapsed (debug only)
    p.append('<details><summary>Article outline / 文章大纲 (debug)</summary><div class=meta>'
             + "<br>".join(esc(h) for h in pack_case.get("outline", [])) + '</div></details>')
    p.append('</body></html>')
    (out_dir / "cases").mkdir(parents=True, exist_ok=True)
    (out_dir / "cases" / f"{cid}.html").write_text("\n".join(p), encoding="utf-8")


_INPUT_CACHE: dict = {}


def load_case_inputs(pack_case: dict) -> dict:
    """The full memories/cards live in the review-pack case itself."""
    return {"kmem_memories": pack_case.get("kmem_memories", []),
            "ovp_cards": pack_case.get("ovp_cards", [])}


def index_page(pack: dict, reviews: dict, out_dir: Path):
    cases = {c["case_id"]: c for c in pack["cases"]}
    rmap = {r["case_id"]: r for r in (reviews or {}).get("reviews", [])}
    p = [f'<!doctype html><html><head><meta charset=utf-8><title>M26 Article-level Memory AB</title><style>{CSS}</style></head><body>']
    p.append('<h1>M26 — Article-level Memory Map AB / 整篇文章级别记忆对比</h1>')
    p.append('<div class=banner>'
             'EN: The MAIN review surface. For each source article we compare <b>KMEM source memories</b> vs '
             '<b>OVP reader/memory cards</b> against the article\'s core points — at the article level, not single claims. '
             'Ground truth is the source article; KMEM is a coarser-but-stable reference baseline, not ground truth.'
             '<br><span class=zh>ZH: 这是主验收入口。每篇文章把 <b>KMEM 记忆</b> 和 <b>OVP 卡片</b> 放在文章核心点上比较——'
             '看整篇，不看单句。Ground truth 是原文；KMEM 是更粗但稳定的参照基线，不是真相。</span>'
             '<br><span class=meta>M25 micro-review (single-claim quote/citation) is now a DEBUG-only workflow for gate-blocked claims, '
             'not the acceptance surface. / M25 单条 claim 评审已降级为仅用于排查被 gate 拦下的 claim 的调试工具，不再是验收入口。</span></div>')

    # verdict tally
    tally = {}
    for r in rmap.values():
        tally[r.get("verdict")] = tally.get(r.get("verdict"), 0) + 1
    p.append('<p><b>Verdict distribution / 结论分布:</b> ' +
             " · ".join(f'{esc(VLABEL.get(k,k))}={v}' for k, v in sorted(tally.items())) + '</p>')

    p.append('<table><tr><th>case</th><th>title</th><th>KMEM mem</th><th>OVP cards</th>'
             '<th>core pts</th><th>KMEM cov</th><th>OVP cov</th><th>verdict / 结论</th></tr>')
    for cid in sorted(cases):
        c = cases[cid]
        r = rmap.get(cid, {})
        v = r.get("verdict", "—")
        ncp = len(r.get("core_points", [])) if r else "—"
        p.append('<tr>'
                 f'<td><a href="cases/{esc(cid)}.html">{esc(cid)}</a></td>'
                 f'<td>{esc(c["source_title"][:60])}</td>'
                 f'<td>{c["kmem_memory_count"]}</td><td>{c["ovp_card_count"]}</td>'
                 f'<td>{ncp}</td>'
                 f'<td>{esc(r.get("kmem_covered_points","—"))}</td>'
                 f'<td>{esc(r.get("ovp_covered_points","—"))}</td>'
                 f'<td><span class="verdict {VCLS.get(v,"")}">{esc(VLABEL.get(v,v))}</span></td>'
                 '</tr>')
    p.append('</table>')
    p.append('<footer>Generated by scripts/m26_build_dashboard.py. Ground truth = source article. '
             'KMEM = reference baseline (not ground truth). OVP compared via cards, not raw units. '
             'Agent-judged (model confound labeled). Provenance collapsed by design.</footer>')
    p.append('</body></html>')
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text("\n".join(p), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True, type=Path)
    ap.add_argument("--review", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    pack = load(args.pack)
    reviews = load(args.review)
    rmap = {r["case_id"]: r for r in (reviews or {}).get("reviews", [])}
    for c in pack["cases"]:
        case_page(c, rmap.get(c["case_id"]), args.out)
    index_page(pack, reviews, args.out)
    print(f"dashboard → {args.out}/index.html ({len(pack['cases'])} cases, {len(rmap)} reviewed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
