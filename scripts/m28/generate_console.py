#!/usr/bin/env python3
"""
M28 — OVP Crystal Console generator.

Builds a product-facing, bilingual (EN/ZH) review console over the existing
M27/M26 artifacts. Read-only over inputs; does NOT re-run extraction, tune
prompts, or change any M27 durable/caveated decision.

Inputs (read-only):
  .run/m27/crystal-coverage.json       sources + theme + durable coverage
  .run/m27/store/ledger.jsonl          12 durable claims (append-only store)
  .run/m27/store/review.json           14 caveated claims (NOT durable)
  .run/m27/store/crystal.md            human-readable durable doc
  .run/m27/crystal-candidate.json      claim_id -> source citations (caveated join)
  .run/m26/article-review.json         20 article-level OVP-vs-KMEM reviews
  .run/m26/wf-args.json                source titles/paths + KMEM memory counts
  scripts/m28/content-pack.json        committed bilingual review copy (LLM-authored)
                                       (falls back to .run/m28/content-pack.json)

Outputs (NOT committed — .run is gitignored):
  .run/m28/dashboard/{index,crystal,sources,backlog,compare,coverage,about}.html
  .run/m28/dashboard/assets/console.css
  .run/m28/content-backlog.json

Run:  python3 scripts/m28/generate_console.py
"""
from __future__ import annotations
import json, os, html, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
M27 = ROOT / ".run/m27"
M26 = ROOT / ".run/m26"
OUT = ROOT / ".run/m28"
DASH = OUT / "dashboard"
ASSETS = DASH / "assets"

# ----------------------------------------------------------------------------
# load
# ----------------------------------------------------------------------------
def jload(p): return json.loads(Path(p).read_text())

def load_pack():
    for p in (ROOT / "scripts/m28/content-pack.json", OUT / "content-pack.json"):
        if p.exists():
            return jload(p)
    return {"durable": [], "caveated": [], "reader_only": []}

COV = jload(M27 / "crystal-coverage.json")
LEDGER = [json.loads(l)["record"] for l in (M27 / "store/ledger.jsonl").read_text().splitlines() if l.strip()]
REVIEW = jload(M27 / "store/review.json")["review"]
CAND = jload(M27 / "crystal-candidate.json")["items"]
M26R = jload(M26 / "article-review.json")["reviews"]
WF = jload(M26 / "wf-args.json")
PACK = load_pack()

PACK_D = {x["claim_id"]: x for x in PACK.get("durable", [])}
PACK_C = {x["claim_id"]: x for x in PACK.get("caveated", [])}
PACK_R = {x["case_id"]: x for x in PACK.get("reader_only", [])}

WFMAP = {x["case_id"]: x for x in WF}
SRCMETA = {s["case_id"]: s for s in COV["sources"]}
M26MAP = {r["case_id"]: r for r in M26R}

# caveated claim_id -> source case_ids, via candidate citations
CAND_SRC = {}
for it in CAND:
    cid = it.get("id")
    srcs = sorted({c.get("case_id") for c in (it.get("citations") or []) if c.get("case_id")})
    CAND_SRC[cid] = srcs

# ----------------------------------------------------------------------------
# joined models
# ----------------------------------------------------------------------------
def durable_model():
    out = []
    for r in LEDGER:
        cid = r["claim_id"]
        p = PACK_D.get(cid, {})
        out.append({
            "id": cid,
            "claim": r["claim"],
            "theme": r["theme"],
            "sources": r["source_cases"],
            "citations": r["citations"],
            "provenance_score": r.get("provenance_score"),
            "provenance_class": r.get("provenance_class", "durable"),
            "strength": r.get("strength", "supported"),
            "title_en": p.get("title_en") or r["theme"],
            "title_zh": p.get("title_zh", ""),
            "summary_zh": p.get("summary_zh", ""),
            "why_en": p.get("why_en", ""),
            "why_zh": p.get("why_zh", ""),
        })
    return out

def caveated_model():
    out = []
    for x in REVIEW:
        cid = x["claim_id"]
        p = PACK_C.get(cid, {})
        srcs = CAND_SRC.get(cid, [])
        strength = x.get("strength", "supported")
        # deterministic backlog routing
        if strength != "supported":
            route = "needs_rewrite_or_split" if strength == "over_synthesized" else "gate_blocked"
        elif len(srcs) <= 1:
            route = "needs_cross_source_partner"
        else:
            route = "keep_caveated"
        out.append({
            "id": cid,
            "claim": x["claim"],
            "theme": x["theme"],
            "strength": strength,
            "rationale": x.get("rationale", ""),
            "sources": srcs,
            "route": route,
            "summary_zh": p.get("summary_zh", ""),
            "why_caveated_en": p.get("why_caveated_en", ""),
            "why_caveated_zh": p.get("why_caveated_zh", ""),
            "action_en": p.get("action_en", ""),
            "action_zh": p.get("action_zh", ""),
        })
    return out

DURABLE = durable_model()
CAVEATED = caveated_model()
DURABLE_BY_ID = {d["id"]: d for d in DURABLE}
CAVEATED_BY_ID = {c["id"]: c for c in CAVEATED}

# caveated claims per source
CAV_BY_SRC = {}
for c in CAVEATED:
    for s in c["sources"]:
        CAV_BY_SRC.setdefault(s, []).append(c["id"])

DURABLE_THEMES = {d["theme"] for d in DURABLE}

def source_model():
    out = []
    for cid in sorted(SRCMETA, key=lambda x: int(x.split("-")[1])):
        meta = SRCMETA[cid]
        wf = WFMAP.get(cid, {})
        durable_here = meta.get("durable_claims_citing", [])
        cav_here = CAV_BY_SRC.get(cid, [])
        in_crystal = meta.get("in_crystal", False)
        if in_crystal:
            status = "durable"
        elif cav_here:
            status = "needs-partner"
        else:
            status = "uncovered"
        out.append({
            "id": cid,
            "title": meta.get("title", cid),
            "path": wf.get("source_path", ""),
            "category": meta.get("category", ""),
            "ovp_cards": meta.get("ovp_card_count"),
            "kmem_mem": wf.get("kmem_memory_count"),
            "core_points": meta.get("article_core_points"),
            "durable_claims": durable_here,
            "caveated_claims": cav_here,
            "status": status,
            "verdict": M26MAP.get(cid, {}).get("verdict"),
        })
    return out

SOURCES = source_model()

# ----------------------------------------------------------------------------
# coverage / compare aggregates
# ----------------------------------------------------------------------------
def point_tally(side):
    t = {"covered": 0, "partial": 0, "missing": 0}
    for r in M26R:
        for p in r["core_points"]:
            t[p[side]] = t.get(p[side], 0) + 1
    return t

OVP_T = point_tally("ovp")
KMEM_T = point_tally("kmem")
TOTAL_PTS = sum(OVP_T.values())
VERDICTS = {}
for r in M26R:
    VERDICTS[r["verdict"]] = VERDICTS.get(r["verdict"], 0) + 1

def pct(n): return round(100.0 * n / TOTAL_PTS, 1) if TOTAL_PTS else 0.0

# ----------------------------------------------------------------------------
# content-backlog.json
# ----------------------------------------------------------------------------
def build_backlog():
    items = []
    n = [0]
    def nid():
        n[0] += 1
        return f"bk-{n[0]:02d}"

    # 1. source_uncovered — reader-only with zero durable AND zero caveated material
    for s in SOURCES:
        if s["status"] != "uncovered" or s["durable_claims"]:
            continue
        prio = "high" if (s["ovp_cards"] or 0) >= 15 else "medium"
        items.append({
            "id": nid(), "type": "source_uncovered", "priority": prio,
            "title": f"{s['id']} fully uncovered by Crystal — {s['title']}",
            "summary_en": f"{s['id']} has {s['ovp_cards']} OVP reader cards and {s['core_points']} article core points but contributes 0 durable and 0 caveated claims.",
            "summary_zh": f"{s['id']} 有 {s['ovp_cards']} 张 OVP 阅读卡、{s['core_points']} 个文章核心点，但 0 条 durable、0 条 caveated 主张。",
            "related_sources": [s["id"]], "related_caveated": [],
            "status": "uncovered",
            "recommended_action": "Mine reader cards for at least one claim candidate and pair with a sibling source in the same theme.",
            "rationale": "No claim has survived even to caveated status; the source is invisible to durable Crystal.",
        })

    # 2. reader_only_high_value — reader-only sources that already have caveated material
    for s in SOURCES:
        if s["status"] != "needs-partner":
            continue
        rp = PACK_R.get(s["id"], {})
        ncav = len(s["caveated_claims"])
        prio = "high" if (ncav >= 2 or (s["ovp_cards"] or 0) >= 15) else "medium"
        items.append({
            "id": nid(), "type": "reader_only_high_value", "priority": prio,
            "title": f"Promote {s['id']} — {ncav} caveated candidate(s) ready to harden",
            "summary_en": rp.get("why_high_value_en") or f"{s['id']} already yields {ncav} caveated claim(s); a corroborating partner could lift one to durable.",
            "summary_zh": rp.get("why_high_value_zh") or f"{s['id']} 已产出 {ncav} 条 caveated 主张；补一个佐证来源即可提升为 durable。",
            "related_sources": [s["id"]], "related_caveated": s["caveated_claims"],
            "status": "reader-only",
            "recommended_action": rp.get("partner_action_en") or "Find a cross-source partner so the strongest caveated claim can clear the strength gate.",
            "rationale": "Reader-only at the article level, but caveated candidates prove extractable durable material exists.",
        })

    # 3/4/5. caveated-claim routes
    for c in CAVEATED:
        if c["route"] == "keep_caveated":
            continue
        if c["route"] == "needs_cross_source_partner":
            typ, prio = "needs_cross_source_partner", "medium"
            act = c["action_en"] or "Find a second independent source asserting the same mechanism, then re-gate for durable."
        elif c["route"] == "needs_rewrite_or_split":
            typ, prio = "needs_rewrite_or_split", "medium"
            act = c["action_en"] or "Split the over-synthesized claim into separately-grounded statements, then re-run the strength gate."
        else:  # gate_blocked
            typ, prio = "gate_blocked", "low"
            act = c["action_en"] or "Hold as caveated: opinion-as-fact cannot be promoted without independent corroborating evidence."
        items.append({
            "id": nid(), "type": typ, "priority": prio,
            "title": f"{c['id']} ({c['strength']}) — {c['theme']}",
            "summary_en": c["why_caveated_en"] or c["claim"][:160],
            "summary_zh": c["why_caveated_zh"] or c["summary_zh"],
            "related_sources": c["sources"], "related_caveated": [c["id"]],
            "status": "blocked" if typ == "gate_blocked" else "caveated",
            "recommended_action": act,
            "rationale": f"Strength gate classified this as '{c['strength']}'; it is recorded as caveated and is NOT durable truth.",
        })

    # 6. theme_gap — caveated theme clusters with no durable claim
    THEME_GAPS = [
        ("Evaluation & runtime quality discipline",
         "评估与运行时质量纪律",
         ["evrel-1", "evrel-2", "engrt-3"],
         "Eval/runtime-quality claims are only caveated; no durable claim anchors this theme despite 3+ candidates.",
         "评估/运行时质量主张仅为 caveated；尽管有 3+ 候选，仍无 durable 主张支撑该主题。"),
        ("Agent vs. fleet & skills-as-infrastructure architecture",
         "单体 Agent 与技能即基础设施架构",
         ["agdes-1", "agdes-2", "agdes-3", "agdes-4"],
         "Four architecture claims sit at caveated; each is single-source, so the theme has no durable anchor.",
         "四条架构主张停留在 caveated；均为单一来源，主题缺少 durable 锚点。"),
        ("GPU / hardware abstraction (dense-tech)",
         "GPU 与硬件抽象（dense-tech）",
         ["densetech-1", "densetech-3", "densetech-4", "densetech-5"],
         "Dense-tech/GPU claims cluster in caveated with two strength failures; no durable coverage of the hardware theme.",
         "dense-tech/GPU 主张集中在 caveated，且有两条强度不合格；硬件主题无 durable 覆盖。"),
        ("Anti-patterns: vibe-coding & RL reward gaming",
         "反模式：vibe-coding 与 RL 奖励作弊",
         ["evrel-3", "evrel-4"],
         "Anti-pattern claims are single-source and caveated; useful cautions but not yet durable.",
         "反模式主张为单一来源且 caveated；是有用的警示，但尚未 durable。"),
    ]
    for title_en, title_zh, rel, ren, rzh in THEME_GAPS:
        rel = [r for r in rel if r in CAVEATED_BY_ID]
        items.append({
            "id": nid(), "type": "theme_gap", "priority": "medium",
            "title": f"Theme gap — {title_en}",
            "summary_en": ren, "summary_zh": rzh,
            "related_sources": sorted({s for cid in rel for s in CAVEATED_BY_ID[cid]["sources"]}),
            "related_caveated": rel,
            "status": "caveated",
            "recommended_action": "Pick the strongest candidate in the cluster and pair it with a corroborating source to seed a durable claim for the theme.",
            "rationale": "Theme is represented only by caveated claims; promoting one anchor would close the gap.",
        })

    order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda x: (order[x["priority"]], x["type"]))
    return items

BACKLOG = build_backlog()

# ----------------------------------------------------------------------------
# HTML rendering
# ----------------------------------------------------------------------------
E = html.escape
NAV = [
    ("index.html", "Attention", "默认审阅流"),
    ("crystal.html", "Crystal", "durable 主张"),
    ("sources.html", "Sources", "20 来源"),
    ("backlog.html", "Backlog", "内容待办"),
    ("compare.html", "Compare", "OVP vs KMEM"),
    ("coverage.html", "Coverage", "宏观覆盖"),
    ("about.html", "About", "说明"),
]
STAMP = os.environ.get("M28_STAMP", datetime.date.today().isoformat())

STATUS_LABELS = {
    "durable": "durable", "caveated": "caveated", "reader-only": "reader-only",
    "needs-partner": "needs-partner", "blocked": "blocked",
    "ready-for-review": "ready-for-review", "uncovered": "uncovered",
}

CSS = """
:root{
  --bg:#0f1419; --panel:#161c24; --panel2:#1b232d; --line:#2a3542;
  --ink:#dfe6ee; --mut:#8b9bb0; --dim:#6b7a8d;
  --durable:#3fb27f; --caveated:#d99a2b; --reader:#5b8def; --partner:#a779e8;
  --blocked:#e0617a; --review:#2bb6c0; --uncov:#7a8aa0; --accent:#5b8def;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.app{display:grid;grid-template-columns:208px 1fr;min-height:100vh}
.side{background:var(--panel);border-right:1px solid var(--line);padding:16px 0;position:sticky;top:0;height:100vh}
.brand{padding:0 18px 14px;font-weight:600;font-size:14px;letter-spacing:.2px}
.brand small{display:block;color:var(--dim);font-weight:400;font-size:11px;margin-top:2px}
.nav a{display:block;padding:7px 18px;color:var(--mut);border-left:2px solid transparent}
.nav a .zh{color:var(--dim);font-size:11px;margin-left:6px}
.nav a:hover{background:var(--panel2);text-decoration:none;color:var(--ink)}
.nav a.on{color:var(--ink);border-left-color:var(--accent);background:var(--panel2)}
.main{padding:22px 26px 60px;max-width:1140px}
h1{font-size:18px;margin:0 0 2px}
.sub{color:var(--mut);margin:0 0 18px;font-size:12px}
.strip{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 20px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:8px 12px;min-width:96px}
.stat b{display:block;font-size:18px}
.stat span{color:var(--dim);font-size:11px}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:600;
  border:1px solid;white-space:nowrap}
.s-durable{color:var(--durable);border-color:var(--durable);background:rgba(63,178,127,.10)}
.s-caveated{color:var(--caveated);border-color:var(--caveated);background:rgba(217,154,43,.10)}
.s-reader-only{color:var(--reader);border-color:var(--reader);background:rgba(91,141,239,.10)}
.s-needs-partner{color:var(--partner);border-color:var(--partner);background:rgba(167,121,232,.10)}
.s-blocked{color:var(--blocked);border-color:var(--blocked);background:rgba(224,97,122,.10)}
.s-ready-for-review{color:var(--review);border-color:var(--review);background:rgba(43,182,192,.10)}
.s-uncovered{color:var(--uncov);border-color:var(--uncov);background:rgba(122,138,160,.10)}
.p-high{color:#e0617a;font-weight:700}
.p-medium{color:#d99a2b}
.p-low{color:#7a8aa0}
.tag{display:inline-block;font-size:10.5px;color:var(--dim);border:1px solid var(--line);
  border-radius:4px;padding:0 5px;margin-right:4px}
.feed{display:flex;flex-direction:column;gap:10px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:7px;padding:13px 15px;border-left:3px solid var(--line)}
.card.lv-durable{border-left-color:var(--durable)}
.card.lv-caveated{border-left-color:var(--caveated)}
.card.lv-reader-only{border-left-color:var(--reader)}
.card.lv-needs-partner{border-left-color:var(--partner)}
.card.lv-blocked{border-left-color:var(--blocked)}
.card.lv-ready-for-review{border-left-color:var(--review)}
.card.lv-uncovered{border-left-color:var(--uncov)}
.card .top{display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap}
.card .ctype{font-size:11px;font-weight:600;color:var(--mut);text-transform:uppercase;letter-spacing:.4px}
.card .en{font-weight:500;margin:1px 0}
.card .zh{color:var(--mut);font-size:12.5px;margin:1px 0}
.card .why{color:var(--dim);font-size:12px;margin:6px 0 2px}
.card .why b{color:var(--mut);font-weight:600}
.card .meta{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;align-items:center}
.card .act{margin-top:9px;display:flex;gap:6px;flex-wrap:wrap}
.btn{font-size:11px;color:var(--mut);border:1px solid var(--line);border-radius:5px;padding:3px 9px;background:var(--panel2);cursor:pointer}
.btn:hover{border-color:var(--accent);color:var(--ink);text-decoration:none}
.btn.go{color:var(--accent);border-color:var(--accent)}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.3px;position:sticky;top:0;background:var(--bg)}
tr:hover td{background:var(--panel)}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;color:var(--mut)}
.claim{background:var(--panel);border:1px solid var(--line);border-radius:7px;padding:14px 16px;margin-bottom:12px}
.claim h3{margin:0 0 4px;font-size:14px}
.claim .body{color:var(--mut);margin:4px 0}
details{margin-top:8px}
summary{cursor:pointer;color:var(--accent);font-size:12px}
.prov{margin-top:7px;font-size:12px;color:var(--mut)}
.prov li{margin-bottom:6px}
.q{color:var(--ink)}
.bar{height:9px;border-radius:4px;background:var(--panel2);overflow:hidden;display:flex;min-width:160px}
.bar i{display:block;height:100%}
.bar .c{background:var(--durable)}.bar .p{background:var(--caveated)}.bar .m{background:#3a4452}
.legend{display:flex;gap:14px;flex-wrap:wrap;color:var(--dim);font-size:11px;margin:6px 0 16px}
.legend span:before{content:"";display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:middle}
.lg-c:before{background:var(--durable)}.lg-p:before{background:var(--caveated)}.lg-m:before{background:#3a4452}
.note{color:var(--dim);font-size:11.5px}
.sec{margin:24px 0 10px;font-size:13px;color:var(--mut);font-weight:600;border-bottom:1px solid var(--line);padding-bottom:5px}
.kv{color:var(--dim)}.kv b{color:var(--ink);font-weight:600}
.prose{max-width:760px}
.prose p{color:var(--mut);margin:9px 0}
.prose h2{font-size:14px;margin:22px 0 6px}
.prose li{color:var(--mut);margin:4px 0}
.adopt{color:var(--durable)}.reject{color:var(--blocked)}
"""

def status_pill(s):
    return f'<span class="pill s-{s}">{E(STATUS_LABELS.get(s,s))}</span>'

def src_links(ids):
    return " ".join(f'<a class="mono" href="sources.html#{E(i)}">{E(i)}</a>' for i in ids) or '<span class="note">—</span>'

def page(active, title, sub, body, strip=""):
    nav = "".join(
        f'<a class="{ "on" if href==active else "" }" href="{href}">{E(label)}'
        f'<span class="zh">{E(zh)}</span></a>'
        for href, label, zh in NAV)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(title)} · OVP Crystal Console</title>
<link rel="stylesheet" href="assets/console.css"></head><body>
<div class="app">
<aside class="side"><div class="brand">OVP Crystal Console<small>M28 · review surface · {E(STAMP)}</small></div>
<nav class="nav">{nav}</nav></aside>
<main class="main"><h1>{E(title)}</h1><p class="sub">{sub}</p>{strip}{body}</main>
</div></body></html>"""

def stat(label, value):
    return f'<div class="stat"><b>{value}</b><span>{E(label)}</span></div>'

GLOBAL_STRIP = ('<div class="strip">'
    + stat("durable claims", len(DURABLE))
    + stat("caveated", len(CAVEATED))
    + stat("sources in crystal", f'{COV["n_sources_in_crystal"]}/{COV["n_sources_total"]}')
    + stat("reader-only", len(COV["reader_only_sources"]))
    + stat("backlog items", len(BACKLOG))
    + stat("OVP > KMEM", f'{VERDICTS.get("ovp_better",0)}/{len(M26R)}')
    + '</div>')

# ---- Attention (index) ------------------------------------------------------
def card(ctype, level, status, en, zh, why, sources, evidence, action, buttons):
    btns = "".join(f'<a class="btn{ " go" if g else ""}" href="{E(h)}">{E(t)}</a>' for t, h, g in buttons)
    ev = f'<a class="btn go" href="{E(evidence)}">View evidence</a>' if evidence else ''
    return f"""<div class="card lv-{level}">
<div class="top"><span class="ctype">{E(ctype)}</span>{status_pill(status)}</div>
<div class="en">{en}</div><div class="zh">{zh}</div>
<div class="why"><b>Why it matters:</b> {why}</div>
<div class="meta"><span class="note">Sources:</span> {src_links(sources)}</div>
<div class="act">{ev}{btns}</div></div>"""

def attention_page():
    cards = []
    # 1. Durable Crystal Summary
    themes = ", ".join(sorted(DURABLE_THEMES))[:120]
    cards.append(card(
        "Durable Crystal Summary", "durable", "durable",
        f"{len(DURABLE)} durable claims across {len(DURABLE_THEMES)} themes, citing {COV['n_sources_in_crystal']} of {COV['n_sources_total']} sources — each gate-passed and fully traced to source lines.",
        f"{len(DURABLE)} 条 durable 主张，覆盖 {len(DURABLE_THEMES)} 个主题、引用 {COV['n_sources_in_crystal']}/{COV['n_sources_total']} 个来源；每条均通过质量门并可回溯到源文件行号。",
        "This is the confirmed knowledge surface — the only set safe to treat as durable truth.",
        sorted({s for d in DURABLE for s in d["sources"]}),
        "crystal.html",
        None,
        [("Open Crystal", "crystal.html", True), ("View coverage", "coverage.html", False)]))

    # 2. KMEM Comparison Note
    cards.append(card(
        "KMEM Comparison Note", "ready-for-review", "ready-for-review",
        f"At article level OVP wins {VERDICTS.get('ovp_better',0)}/{len(M26R)} (rest tie, 0 loss). OVP covers {pct(OVP_T['covered'])}% of core points vs KMEM {pct(KMEM_T['covered'])}% (covered-only).",
        f"在文章级别，OVP 胜 {VERDICTS.get('ovp_better',0)}/{len(M26R)}（其余平局，0 负）。核心点覆盖率 OVP {pct(OVP_T['covered'])}% vs KMEM {pct(KMEM_T['covered'])}%（仅 covered）。",
        "KMEM is reference-only; this confirms OVP's grounded surface leads on coverage and provenance, not visual skin.",
        [], "compare.html", None,
        [("Open comparison", "compare.html", True)]))

    # 3. Coverage Gap (uncovered sources)
    uncov = [s for s in SOURCES if s["status"] == "uncovered"]
    if uncov:
        ids = [s["id"] for s in uncov]
        cards.append(card(
            "Coverage Gap", "uncovered", "uncovered",
            f"{len(uncov)} source(s) ({', '.join(ids)}) are fully uncovered — no durable and no caveated claim. They are invisible to Crystal.",
            f"{len(uncov)} 个来源（{', '.join(ids)}）完全未覆盖——无 durable 也无 caveated 主张，对 Crystal 不可见。",
            "Uncovered sources are silent blind spots; each needs at least one claim candidate mined.",
            ids, "coverage.html", None,
            [("Open backlog", "backlog.html", True), ("Coverage view", "coverage.html", False)]))

    # 4. High-Value Expansion Candidates (top reader-only-with-caveated)
    hv = [b for b in BACKLOG if b["type"] == "reader_only_high_value" and b["priority"] == "high"]
    for b in hv:
        sid = b["related_sources"][0]
        cards.append(card(
            "High-Value Expansion Candidate", "reader-only", "reader-only",
            E(b["summary_en"]),
            E(b["summary_zh"]),
            f"Caveated candidates here ({', '.join(b['related_caveated'])}) prove durable material exists — one partner source clears the gate.",
            b["related_sources"], f"sources.html#{sid}", None,
            [("Find partner source", "backlog.html", True), ("Open source", f"sources.html#{sid}", False)]))

    # 5. Reader-Only Source Needs Partner (medium reader-only)
    rp = [b for b in BACKLOG if b["type"] == "reader_only_high_value" and b["priority"] != "high"]
    for b in rp[:3]:
        sid = b["related_sources"][0]
        cards.append(card(
            "Reader-Only Source Needs Partner", "needs-partner", "needs-partner",
            E(b["summary_en"]),
            E(b["summary_zh"]),
            "Has reader cards and caveated candidates but no durable claim yet — needs a corroborating sibling.",
            b["related_sources"], f"sources.html#{sid}", None,
            [("Find partner source", "backlog.html", True)]))

    # 6. Caveated Claim Needs Review (cross-source-partner, medium)
    cav = [b for b in BACKLOG if b["type"] == "needs_cross_source_partner"]
    for b in cav[:4]:
        cid = b["related_caveated"][0]
        cards.append(card(
            "Caveated Claim Needs Review", "caveated", "caveated",
            E(b["summary_en"]),
            E(b["summary_zh"]),
            "Single-source and supported — sound but thin; a second source would let it clear the strength gate.",
            b["related_sources"], f"crystal.html#{cid}", None,
            [("Split / narrow claim", "backlog.html", False), ("Keep caveated", "backlog.html", False)]))

    # 7. Gate-Protected Item (gate_blocked / rewrite)
    gp = [b for b in BACKLOG if b["type"] in ("gate_blocked", "needs_rewrite_or_split")]
    for b in gp:
        cid = b["related_caveated"][0]
        c = CAVEATED_BY_ID[cid]
        cards.append(card(
            "Gate-Protected Item", "blocked", "blocked",
            E(b["summary_en"]),
            E(b["summary_zh"]),
            f"The claim-strength gate classified this as <b>{E(c['strength'])}</b> and refused promotion — the gate working as designed.",
            b["related_sources"], f"crystal.html#{cid}", None,
            [("Keep caveated", "backlog.html", False), ("Dismiss", "backlog.html", False)]))

    intro = ('<p class="note" style="margin:-8px 0 16px">Operational review feed — highest-value actions first. '
             'Actions are non-mutating links into the detail surfaces. '
             '运营审阅流，按价值排序；操作为只读链接，跳转到对应明细页。</p>')
    return page("index.html", "Attention", "Default review feed · 默认审阅流",
                intro + f'<div class="feed">{"".join(cards)}</div>', GLOBAL_STRIP)

# ---- Crystal ----------------------------------------------------------------
def crystal_page():
    by_theme = {}
    for d in DURABLE:
        by_theme.setdefault(d["theme"], []).append(d)
    blocks = [
        '<p class="note" style="margin:-8px 0 14px"><b>Durable</b> = citation linter passed + claim-strength gate passed + written to the append-only durable store. '
        'Caveated claims are listed separately and are NOT durable truth. '
        'durable = 引用校验通过 + 强度门通过 + 写入只追加存储；caveated 不是 durable 真相。</p>']
    for d in DURABLE:
        cites = "".join(
            f'<li>(<span class="mono">{E(c["case_id"])}</span> · line {c.get("resolved_line","?")}) '
            f'<span class="q">“{E(c["quote"])}”</span></li>'
            for c in d["citations"])
        zh = f'<div class="zh">{E(d["summary_zh"])}</div>' if d["summary_zh"] else ""
        why = f'<div class="why"><b>Why it matters:</b> {E(d["why_en"])} {E(d["why_zh"])}</div>' if d["why_en"] else ""
        blocks.append(f"""<div class="claim" id="{E(d['id'])}">
<div class="top" style="display:flex;gap:8px;align-items:center;margin-bottom:5px">
{status_pill('durable')}<span class="tag">{E(d['theme'])}</span>
<span class="mono">{E(d['id'])}</span>
<span class="note">prov {d['provenance_score']:.2f} · strength {E(d['strength'])} · {len(d['sources'])} sources</span></div>
<h3>{E(d['title_en'])}</h3>
<div class="body">{E(d['claim'])}</div>{zh}{why}
<div class="meta"><span class="note">Sources:</span> {src_links(d['sources'])}</div>
<details><summary>Provenance — {len(d['citations'])} citation(s) · claim → unit → quote → line</summary>
<ul class="prov">{cites}</ul></details></div>""")
    return page("crystal.html", "Crystal", f"{len(DURABLE)} durable claims grouped by theme · durable 主张",
                "".join(blocks), GLOBAL_STRIP)

# ---- Sources ----------------------------------------------------------------
def sources_page():
    rows = []
    for s in SOURCES:
        dn = len(s["durable_claims"]); cn = len(s["caveated_claims"])
        cmp_link = f'<a href="compare.html#{s["id"]}">{E(s["verdict"] or "—")}</a>'
        dlink = " ".join(f'<a class="mono" href="crystal.html#{E(i)}">{E(i)}</a>' for i in s["durable_claims"]) or '<span class="note">—</span>'
        rows.append(f"""<tr id="{E(s['id'])}"><td class="mono">{E(s['id'])}</td>
<td>{E(s['title'])}<div class="note">{E(s['category'])}</div></td>
<td>{status_pill(s['status'])}</td>
<td>{s['ovp_cards'] if s['ovp_cards'] is not None else '—'}</td>
<td>{s['kmem_mem'] if s['kmem_mem'] is not None else '—'}</td>
<td>{dn}</td><td>{cn}</td>
<td>{dlink}</td>
<td>{cmp_link}</td></tr>""")
    head = """<tr><th>id</th><th>title / category</th><th>status</th>
<th>OVP cards</th><th>KMEM mem</th><th>durable</th><th>caveated</th><th>durable claims</th><th>article verdict</th></tr>"""
    legend = ('<div class="legend">'
              + status_pill("durable") + status_pill("needs-partner") + status_pill("uncovered")
              + '<span class="note">durable = cited by ≥1 durable claim · needs-partner = caveated material only · uncovered = no claim</span></div>')
    return page("sources.html", "Sources", f"All {len(SOURCES)} sources · 全部来源",
                legend + f"<table>{head}{''.join(rows)}</table>", GLOBAL_STRIP)

# ---- Backlog ----------------------------------------------------------------
def backlog_page():
    type_zh = {
        "source_uncovered": "来源未覆盖", "reader_only_high_value": "高价值待提升",
        "needs_cross_source_partner": "需跨来源佐证", "needs_rewrite_or_split": "需改写/拆分",
        "gate_blocked": "被门拦截", "theme_gap": "主题缺口",
    }
    rows = []
    for b in BACKLOG:
        rel = src_links(b["related_sources"])
        cav = " ".join(f'<a class="mono" href="crystal.html#{E(i)}">{E(i)}</a>' for i in b["related_caveated"]) or "—"
        rows.append(f"""<div class="card lv-{b['status']}">
<div class="top"><span class="ctype">{E(b['type'])}</span>
<span class="tag">{E(type_zh.get(b['type'],''))}</span>
<span class="p-{b['priority']}">priority: {E(b['priority'])}</span>{status_pill(b['status'])}
<span class="mono">{E(b['id'])}</span></div>
<div class="en">{E(b['title'])}</div>
<div class="en" style="font-weight:400;color:var(--mut)">{E(b['summary_en'])}</div>
<div class="zh">{E(b['summary_zh'])}</div>
<div class="why"><b>Recommended action:</b> {E(b['recommended_action'])}</div>
<div class="why" style="color:var(--dim)">{E(b['rationale'])}</div>
<div class="meta"><span class="note">Sources:</span> {rel}
<span class="note" style="margin-left:8px">Caveated:</span> {cav}</div></div>""")
    counts = {}
    for b in BACKLOG:
        counts.setdefault(b["type"], {"high":0,"medium":0,"low":0})
        counts[b["type"]][b["priority"]] += 1
    crows = "".join(
        f'<tr><td class="mono">{E(t)}</td><td>{c["high"]}</td><td>{c["medium"]}</td><td>{c["low"]}</td><td>{c["high"]+c["medium"]+c["low"]}</td></tr>'
        for t, c in sorted(counts.items()))
    summary = (f'<div class="sec">Backlog by type · 按类型</div>'
               f'<table><tr><th>type</th><th>high</th><th>medium</th><th>low</th><th>total</th></tr>{crows}</table>'
               f'<p class="note">Generated to <span class="mono">.run/m28/content-backlog.json</span> · {len(BACKLOG)} items.</p>'
               f'<div class="sec">Items · 待办项</div>')
    return page("backlog.html", "Backlog", f"{len(BACKLOG)} content backlog items · 内容待办",
                summary + f'<div class="feed">{"".join(rows)}</div>', GLOBAL_STRIP)

# ---- Compare ----------------------------------------------------------------
def compare_page():
    rows = []
    for r in M26R:
        cid = r["case_id"]
        meta = SRCMETA.get(cid, {})
        ovp_cov = sum(1 for p in r["core_points"] if p["ovp"] == "covered")
        kmem_cov = sum(1 for p in r["core_points"] if p["kmem"] == "covered")
        tot = len(r["core_points"])
        ofi = len(r.get("ovp_factual_issues") or [])
        kfi = len(r.get("kmem_factual_issues") or [])
        v = r["verdict"]
        vp = "ready-for-review" if v == "ovp_better" else "reader-only"
        notes_en = E((r.get("ovp_granularity_notes_en") or "")[:200])
        rows.append(f"""<div class="claim" id="{E(cid)}">
<div class="top" style="display:flex;gap:8px;align-items:center;margin-bottom:4px">
<span class="mono">{E(cid)}</span>{status_pill(vp)}<span class="tag">{E(v)}</span>
<span class="note">{E(meta.get('title','')[:70])}</span></div>
<div class="meta" style="margin:4px 0">
<span class="kv">OVP core points: <b>{ovp_cov}/{tot}</b></span>
<span class="kv" style="margin-left:14px">KMEM core points: <b>{kmem_cov}/{tot}</b></span>
<span class="kv" style="margin-left:14px">OVP factual issues: <b>{ofi}</b></span>
<span class="kv" style="margin-left:14px">KMEM factual issues: <b>{kfi}</b></span></div>
<div class="zh">{E(r.get('rationale_zh','')[:240])}</div>
<div class="why">{E(r.get('rationale_en','')[:260])}</div>
{f'<div class="note" style="margin-top:5px">Coverage note: {notes_en}</div>' if notes_en else ''}
<div class="meta"><a href="sources.html#{E(cid)}">source row →</a></div></div>""")
    intro = (f'<p class="note" style="margin:-8px 0 14px">Article-level OVP vs KMEM — core-point coverage, not single-memory microscope. '
             f'Verdicts: <b>ovp_better {VERDICTS.get("ovp_better",0)}</b> · tie {VERDICTS.get("tie",0)} · loss {VERDICTS.get("ovp_worse",0)}. '
             f'文章级对比（非单条记忆显微镜）。</p>')
    return page("compare.html", "Compare", "Article-level OVP vs KMEM · 文章级对比",
                intro + "".join(rows), GLOBAL_STRIP)

# ---- Coverage ---------------------------------------------------------------
def coverage_page():
    def bar(t):
        c, p, m = t["covered"], t["partial"], t["missing"]
        return (f'<div class="bar"><i class="c" style="width:{pct(c)}%"></i>'
                f'<i class="p" style="width:{pct(p)}%"></i><i class="m" style="width:{pct(m)}%"></i></div>')
    legend = ('<div class="legend"><span class="lg-c">covered</span>'
              '<span class="lg-p">partial</span><span class="lg-m">missing</span></div>')
    point = f"""<div class="sec">Core-point coverage ({TOTAL_PTS} points across {len(M26R)} articles) · 核心点覆盖</div>{legend}
<table><tr><th>system</th><th>covered</th><th>partial</th><th>missing</th><th>coverage</th></tr>
<tr><td><b>OVP</b></td><td>{OVP_T['covered']} ({pct(OVP_T['covered'])}%)</td><td>{OVP_T['partial']}</td><td>{OVP_T['missing']}</td><td>{bar(OVP_T)}</td></tr>
<tr><td>KMEM (ref)</td><td>{KMEM_T['covered']} ({pct(KMEM_T['covered'])}%)</td><td>{KMEM_T['partial']}</td><td>{KMEM_T['missing']}</td><td>{bar(KMEM_T)}</td></tr></table>"""

    # source coverage
    nd = sum(1 for s in SOURCES if s["status"] == "durable")
    nnp = sum(1 for s in SOURCES if s["status"] == "needs-partner")
    nun = sum(1 for s in SOURCES if s["status"] == "uncovered")
    srcsec = f"""<div class="sec">Source coverage · 来源覆盖</div>
<div class="strip">{stat('durable',nd)}{stat('needs-partner',nnp)}{stat('uncovered',nun)}{stat('total',len(SOURCES))}</div>"""

    # theme coverage
    cav_themes = {}
    for c in CAVEATED:
        cav_themes[c["theme"]] = cav_themes.get(c["theme"], 0) + 1
    trows = "".join(
        f'<tr><td>{E(t)}</td><td>{status_pill("durable")}</td><td>{n}</td></tr>'
        for t, n in sorted(COV["theme_counts"].items()))
    crows = "".join(
        f'<tr><td>{E(t)}</td><td>{status_pill("caveated")}</td><td>{n}</td></tr>'
        for t, n in sorted(cav_themes.items()))
    themesec = f"""<div class="sec">Theme coverage — durable vs caveated · 主题覆盖</div>
<table><tr><th>theme</th><th>status</th><th>claims</th></tr>{trows}{crows}</table>
<p class="note">{len(DURABLE_THEMES)} themes have a durable anchor; the caveated themes above are theme gaps (see Backlog).</p>"""

    # out-of-scope / unpaired
    oos = [s for s in SOURCES if s["status"] != "durable"]
    oosrows = "".join(
        f'<tr><td class="mono">{E(s["id"])}</td><td>{E(s["title"][:60])}</td>'
        f'<td>{status_pill(s["status"])}</td><td>{len(s["caveated_claims"])}</td></tr>'
        for s in oos)
    oossec = f"""<div class="sec">Reader-only / unpaired sources · 仅阅读层 / 未配对</div>
<table><tr><th>id</th><th>title</th><th>status</th><th>caveated</th></tr>{oosrows}</table>"""

    note = '<p class="note">Macro coverage only — no graph visualization by design (see About).</p>'
    return page("coverage.html", "Coverage", "Macro coverage view · 宏观覆盖",
                point + srcsec + themesec + oossec + note, GLOBAL_STRIP)

# ---- About ------------------------------------------------------------------
def about_page():
    body = """<div class="prose">
<h2>What this console is · 这是什么</h2>
<p>The OVP Crystal Console is a review surface over the M27 Crystal artifacts. It exists so a human can open one
dashboard and understand the current durable-knowledge state — what is confirmed, what is held back and why, which
sources are covered, and what the highest-value next actions are — without reading raw JSON.</p>
<p>本控制台是 M27 Crystal 产物的审阅界面：让人无需读 JSON，即可一眼看清当前 durable 知识状态、被保留的原因、来源覆盖与下一步高价值动作。</p>

<h2>Durable vs caveated vs reader-only · 三种状态</h2>
<ul>
<li><span class="pill s-durable">durable</span> — passed the citation/provenance linter <b>and</b> the claim-strength gate, then written to the append-only durable store. Safe to treat as truth. Every durable claim is traced claim → cited unit → verbatim quote → source line.</li>
<li><span class="pill s-caveated">caveated</span> — reviewed but <b>not</b> durable: single-source, opinion-as-fact, or over-synthesized. Recorded for review; never silently promoted.</li>
<li><span class="pill s-reader-only">reader-only</span> — the source has OVP reader cards but contributes no durable claim yet. <span class="pill s-needs-partner">needs-partner</span> = it already has caveated candidates; <span class="pill s-uncovered">uncovered</span> = no claim at all.</li>
</ul>

<h2>Gates are not loosened · 不放松质量门</h2>
<p>M22 (citation + provenance + claim-strength) and M23 (append-only durable store) gates are unchanged. This console
only <i>reads</i> their output. Caveated claims stay caveated; gate-protected items show the gate working as designed.</p>

<h2>KMEM is reference-only · KMEM 仅作旁证</h2>
<p>KnowledgeMem appears only as an article-level comparison baseline (Compare page). We borrow its product
<b>information architecture</b> — a default attention feed, typed status cards, source links, review actions, and
separate surfaces for crystal / sources / backlog / compare / coverage — and reject its memory model and visual skin.</p>
<p><b>Adopted from KMEM:</b> <span class="adopt">default Timeline/Attention feed · typed cards · clear status labels · source links · review actions · confirmable Crystal cards · separate surfaces</span>.</p>
<p><b>Rejected:</b> <span class="reject">graph-first UI · visual skin · single-memory microscope as default · the memory model itself</span>.</p>

<h2>Source article is ground truth · 原文即真值</h2>
<p>The 20 M18 source articles are the ground truth. OVP claims earn durability by tracing back to verbatim source
lines; the Compare page scores OVP and KMEM against each article's own core points.</p>

<h2>Why no Referent / RAG / Graph · 为何不做 Referent/RAG/图谱</h2>
<p>These were intentionally kept out of the main path. The moat is the <b>truth layer</b> — grounded claims with
provenance and gates — not retrieval cleverness or graph visuals. Referent/Resolver, RAG, and graph rendering add
surface without strengthening the durable-knowledge guarantee, so they stay out of this console.</p>
</div>"""
    return page("about.html", "About", "How the console works · 说明", body)

# ----------------------------------------------------------------------------
# write
# ----------------------------------------------------------------------------
def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "console.css").write_text(CSS)
    (OUT / "content-backlog.json").write_text(json.dumps(BACKLOG, ensure_ascii=False, indent=2))
    pages = {
        "index.html": attention_page(),
        "crystal.html": crystal_page(),
        "sources.html": sources_page(),
        "backlog.html": backlog_page(),
        "compare.html": compare_page(),
        "coverage.html": coverage_page(),
        "about.html": about_page(),
    }
    for name, content in pages.items():
        (DASH / name).write_text(content)
    print(f"OVP Crystal Console written to {DASH}")
    print(f"  durable={len(DURABLE)} caveated={len(CAVEATED)} sources={len(SOURCES)} backlog={len(BACKLOG)}")
    bt = {}
    for b in BACKLOG:
        bt[b["type"]] = bt.get(b["type"], 0) + 1
    print(f"  backlog by type: {bt}")
    print(f"  pages: {', '.join(pages)}")

if __name__ == "__main__":
    main()
