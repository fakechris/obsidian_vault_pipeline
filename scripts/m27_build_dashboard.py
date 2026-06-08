#!/usr/bin/env python3
"""M27 Crystal v2 product dashboard (bilingual, offline).

A PRODUCT reading surface for the durable Crystal — not a debug UI, not raw JSON.
Reads the M27 store (ledger + review) + coverage matrix and renders:
  - index.html   : overview + theme nav + verdict/coverage summary (bilingual)
  - crystal.html : durable claims grouped by theme; each shows the readable
                   synthesis first, provenance COLLAPSED; caveated claims in a
                   clearly separated section with "why not durable yet + next step"
  - coverage.html: source/theme coverage matrix vs the 20 articles

All human-facing titles/sections/summaries are bilingual (EN + 中文).

Usage:
    python3 scripts/m27_build_dashboard.py \
        --ledger .run/m27/store/ledger.jsonl \
        --review .run/m27/store/review.json \
        --coverage .run/m27/crystal-coverage.json \
        --out .run/m27/dashboard
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


def load_ledger(p: Path):
    if not p or not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


CSS = """
body{font:15px/1.6 -apple-system,Segoe UI,Roboto,'PingFang SC','Microsoft YaHei',sans-serif;max-width:1000px;margin:1.4rem auto;padding:0 1rem;color:#1a1a1a}
h1{font-size:1.5rem}h2{font-size:1.15rem;margin:1.6rem 0 .4rem}
h3{font-size:1.02rem;margin:.8rem 0 .2rem}
a{color:#06c;text-decoration:none}a:hover{text-decoration:underline}
.zh{color:#447;font-size:.93em}
.banner{background:#f0f6ff;border:1px solid #bcd;border-radius:8px;padding:.7rem .9rem;margin:.8rem 0;font-size:.92rem}
.theme{border-left:4px solid #0a6;padding-left:.6rem;margin-top:1.5rem}
.claim{border:1px solid #e3e3e3;border-radius:10px;padding:.7rem .9rem;margin:.7rem 0;background:#fff}
.claim.durable{border-top:3px solid #0a6}
.claim .t{font-weight:600}
.claim .m{color:#666;font-size:.82rem;margin:.2rem 0}
.caveat-sec{border:1px solid #e8c;border-radius:10px;padding:.5rem .9rem;margin:1.5rem 0;background:#fdf7fc}
.claim.caveated{border-top:3px solid #c60}
.q{color:#0a5;font-style:italic}.uid{color:#999;font-family:monospace;font-size:.78rem}
details>summary{cursor:pointer;color:#667;font-size:.88rem}
table{border-collapse:collapse;width:100%;font-size:.85rem;margin:.5rem 0}
th,td{border:1px solid #e3e3e3;padding:.35rem .5rem;text-align:left;vertical-align:top}
th{background:#f7f7f7}
.yes{color:#0a7;font-weight:700}.no{color:#c00}
.pill{display:inline-block;border:1px solid #ccc;border-radius:6px;padding:0 .4rem;font-size:.72rem;color:#555}
footer{margin-top:2.5rem;color:#888;font-size:.8rem;border-top:1px solid #eee;padding-top:.8rem}
"""


def active_records(events):
    state = {}
    for ev in events:
        r = ev["record"]
        state[r["claim_key"]] = (ev["op"], r)
    return [r for (op, r) in state.values() if op in ("write", "supersede")]


def render_claim(r, cls):
    p = [f'<div class="claim {cls}">']
    p.append(f'<div class=t>{esc(r["claim"])}</div>')
    p.append(f'<div class=m>{esc(r["theme"])} · sources: {esc(", ".join(r["source_cases"]))} '
             f'· provenance {r.get("provenance_score",0):.2f} · strength {esc(r.get("strength"))} '
             f'· <span class=pill>{esc(r.get("final_class"))}</span></div>')
    cits = r.get("citations", [])
    p.append(f'<details><summary>Evidence / 证据 — {len(cits)} citation(s) (collapsed)</summary>')
    for c in cits:
        line = f"line {c['resolved_line']}" if c.get("resolved_line") else "—"
        p.append(f'<div class=m><span class=q>“{esc(c.get("quote"))}”</span> '
                 f'<span class=uid>[{esc(c.get("case_id"))} · {esc(c.get("unit_id"))} · {esc(line)}]</span></div>')
    p.append('</details></div>')
    return "\n".join(p)


def crystal_page(durable, review, out_dir):
    p = [f'<!doctype html><html><head><meta charset=utf-8><title>Crystal v2</title><style>{CSS}</style></head><body>']
    p.append('<p><a href="index.html">&larr; index</a> · <a href="coverage.html">coverage matrix</a></p>')
    p.append('<h1>OVP Crystal v2 — durable knowledge / 可信知识</h1>')
    p.append('<div class=banner>EN: Durable claims passed BOTH gates (citation/provenance + claim-strength). '
             'Read the synthesis; evidence is collapsed. Caveated candidates are separated below and are NOT durable truth.'
             '<br><span class=zh>ZH: Durable claim 通过了两道闸（引用/出处 + claim 强度）。先读结论，证据折叠。下方 caveated 候选与 durable 分开，不是可信真相。</span></div>')
    # group durable by theme
    by_theme = {}
    for r in durable:
        by_theme.setdefault(r["theme"], []).append(r)
    p.append(f'<h2>Durable claims / 可信结论 ({len(durable)})</h2>')
    for theme in sorted(by_theme):
        p.append(f'<div class=theme><h3>{esc(theme)} ({len(by_theme[theme])})</h3></div>')
        for r in by_theme[theme]:
            p.append(render_claim(r, "durable"))
    # caveated
    p.append('<div class=caveat-sec>')
    p.append(f'<h2>Review — caveated candidates / 待定候选 ({len(review)})</h2>')
    p.append('<div class=m>EN: real citations, but the claim-strength gate found the synthesis overreaches its evidence '
             '(usually single-source or over-generalized). Next step: narrow the claim or add a second source, then re-gate. '
             '<br><span class=zh>ZH: 引用真实，但 claim 强度闸判定综合超出证据（多为单源或过度概括）。下一步：收窄表述或补第二来源，再过闸。</span></div>')
    for e in review:
        p.append('<div class="claim caveated">')
        p.append(f'<div class=t>{esc(e.get("claim"))}</div>')
        p.append(f'<div class=m>{esc(e.get("theme"))} · <span class=pill>{esc(e.get("final_class"))}</span> '
                 f'· strength {esc(e.get("strength"))}</div>')
        p.append(f'<div class=m><b>Why not durable / 为何未 durable:</b> {esc(e.get("rationale"))}</div>')
        p.append('</div>')
    p.append('</div>')
    p.append('<footer>Durable truth is traceable: claim → cited unit → verbatim quote → source line. '
             'KMEM is not involved here (reference baseline only). Provenance collapsed by design.</footer>')
    p.append('</body></html>')
    (out_dir).mkdir(parents=True, exist_ok=True)
    (out_dir / "crystal.html").write_text("\n".join(p), encoding="utf-8")


def coverage_page(cov, out_dir):
    p = [f'<!doctype html><html><head><meta charset=utf-8><title>Crystal coverage</title><style>{CSS}</style></head><body>']
    p.append('<p><a href="index.html">&larr; index</a> · <a href="crystal.html">crystal</a></p>')
    p.append('<h1>Crystal v2 coverage matrix / 覆盖矩阵</h1>')
    if not cov:
        p.append('<p>no coverage data</p></body></html>')
        (out_dir / "coverage.html").write_text("\n".join(p), encoding="utf-8")
        return
    p.append(f'<div class=banner>EN: {cov["n_sources_in_crystal"]}/{cov["n_sources_total"]} sources are cited by at least '
             f'one durable claim. Macro view (not single-quote review): which articles entered Crystal v2 and which did not.'
             f'<br><span class=zh>ZH: {cov["n_sources_in_crystal"]}/{cov["n_sources_total"]} 篇文章被至少一条 durable claim 引用。宏观视角：哪些文章进入了 Crystal v2，哪些没有。</span></div>')
    p.append('<h2>Themes / 主题</h2><table><tr><th>theme</th><th>durable claims</th></tr>')
    for t, n in sorted(cov["theme_counts"].items(), key=lambda x: -x[1]):
        p.append(f'<tr><td>{esc(t)}</td><td>{n}</td></tr>')
    p.append('</table>')
    p.append('<h2>Sources / 文章 (20)</h2>')
    p.append('<table><tr><th>case</th><th>title</th><th>in Crystal?</th><th>durable claims citing</th>'
             '<th>OVP cards</th><th>core pts</th></tr>')
    for s in cov["sources"]:
        flag = '<span class=yes>yes / 是</span>' if s["in_crystal"] else '<span class=no>no / 否</span>'
        p.append(f'<tr><td>{esc(s["case_id"])}</td><td>{esc(s["title"][:48])}</td><td>{flag}</td>'
                 f'<td>{esc(", ".join(s["durable_claims_citing"]))}</td>'
                 f'<td>{s["ovp_card_count"]}</td><td>{s["article_core_points"]}</td></tr>')
    p.append('</table>')
    if cov.get("reader_only_sources"):
        p.append(f'<div class=m><b>Reader-only / 仅在 reader cards（尚未 synthesize 成 durable Crystal）:</b> '
                 + esc(", ".join(cov["reader_only_sources"])) + '</div>')
    p.append('</body></html>')
    (out_dir / "coverage.html").write_text("\n".join(p), encoding="utf-8")


def index_page(durable, review, cov, out_dir):
    p = [f'<!doctype html><html><head><meta charset=utf-8><title>OVP Crystal v2</title><style>{CSS}</style></head><body>']
    p.append('<h1>OVP Crystal v2 — product surface / 产品界面</h1>')
    p.append('<div class=banner>EN: A readable, auditable Crystal of cross-article durable knowledge from 20 held-out '
             'sources. This is the reading/acceptance surface — NOT the M25 single-claim debug workbench. '
             '<br><span class=zh>ZH: 从 20 篇文章综合出的、可读可审计的跨文章可信知识库。这是阅读/验收界面——不是 M25 单条 claim 调试台。</span></div>')
    p.append('<ul>'
             f'<li><a href="crystal.html">Crystal — durable claims ({len(durable)}) + caveated ({len(review)})</a> / 可信结论 + 待定候选</li>'
             '<li><a href="coverage.html">Coverage matrix</a> / 覆盖矩阵</li>'
             '</ul>')
    if cov:
        p.append(f'<p><b>Summary / 概要:</b> {len(durable)} durable · {len(review)} caveated · '
                 f'{cov["n_sources_in_crystal"]}/{cov["n_sources_total"]} sources in Crystal · '
                 f'{len(cov["theme_counts"])} themes.</p>')
    p.append('<footer>Durable write path unchanged: M22 gate (citation linter + provenance + claim-strength) → M23 '
             'append-only store. Agent-generated candidate; durability decided only by the gate. KMEM = reference baseline.</footer>')
    p.append('</body></html>')
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text("\n".join(p), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True, type=Path)
    ap.add_argument("--review", required=True, type=Path)
    ap.add_argument("--coverage", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    durable = active_records(load_ledger(args.ledger))
    durable.sort(key=lambda r: (r["theme"], r["claim_id"]))
    review = (load(args.review) or {}).get("review", [])
    cov = load(args.coverage)
    crystal_page(durable, review, args.out)
    coverage_page(cov, args.out)
    index_page(durable, review, cov, args.out)
    print(f"dashboard → {args.out}/index.html ({len(durable)} durable, {len(review)} caveated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
