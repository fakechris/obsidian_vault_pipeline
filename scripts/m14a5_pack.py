#!/usr/bin/env python3
"""M14a.5 — assemble the human-inspectable review packs + summary.

Merges the DETERMINISTIC coverage report (scripts/m14a5_coverage.py output) with
the ADVISORY Tier-2 LLM reviews (.run/m14a.5/reviews.json — semantic coverage +
faithfulness/attribution/modality, NOT ground truth) into operator-facing packs.
Reads only .run + committed gold/source; writes only under .run (not committed).

Usage: python3 scripts/m14a5_pack.py --run-dir .run/m14.4/extract --out .run/m14a.5
"""
import argparse, json, os, sys

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required")

CASES = ["rag_wrong", "eval_ai_agents", "agent_memory_zh"]
FIX = "fixtures/concept_map"
GOLD_DIR = "fixtures/unit_coverage"


def short(uid):
    # "u-018-ab12cd34" -> "u-018"
    p = uid.split("-")
    return "-".join(p[:2]) if len(p) >= 2 else uid


def excerpt(source_bytes, loc, pad=120):
    if not loc:
        return "(no location)"
    s = max(0, loc["byte_start"] - pad)
    e = min(len(source_bytes), loc["byte_end"] + pad)
    while s > 0 and (source_bytes[s] & 0xC0) == 0x80:
        s -= 1
    while e < len(source_bytes) and (source_bytes[e] & 0xC0) == 0x80:
        e += 1
    return source_bytes[s:e].decode("utf-8", "replace").replace("\n", " ")


def run_case(case, run_dir, out_dir, reviews):
    cov = json.load(open(os.path.join(out_dir, case, "coverage-report.json"), encoding="utf-8"))
    units = json.load(open(os.path.join(run_dir, case, "units.accepted.json"), encoding="utf-8"))
    gold = {u["label"]: u for u in yaml.safe_load(open(os.path.join(GOLD_DIR, case, "central_units.yml")))["central_units"]}
    source_bytes = open(open(os.path.join(FIX, case, "input_path.txt")).read().strip(), "rb").read()
    rv = next((r for r in reviews if r["case"] == case), {"coverage": [], "unit_reviews": [], "notes": ""})
    cov_by = {c["label"]: c for c in rv.get("coverage", [])}
    rev_by = {}
    for ur in rv.get("unit_reviews", []):
        rev_by[ur["unit_id"]] = ur  # keyed by short id

    od = os.path.join(out_dir, case)
    os.makedirs(od, exist_ok=True)
    json.dump(units, open(os.path.join(od, "accepted-units.json"), "w"), ensure_ascii=False, indent=2)
    json.dump([gold[k] for k in gold], open(os.path.join(od, "central-gold.json"), "w"), ensure_ascii=False, indent=2)

    # unit-review-sheet.md
    s = [f"# Unit review sheet — {case}", "", "verdicts: advisory (LLM); confirm/override by hand.", ""]
    for u in units:
        sid = short(u["id"])
        r = rev_by.get(sid, {})
        s.append(f"### {u['id']}  ({u['kind']}/{u.get('subtype') or '-'})")
        s.append(f"- **text**: {u['text']}")
        s.append(f"- **quote**: \"{u['evidence']['quote']}\"")
        s.append(f"- **ref**: `{u['evidence']['ref_id']}`  attribution: {u['attribution']}  modality: {u['modality']}")
        s.append(f"- **source**: …{excerpt(source_bytes, u['evidence'].get('location'))}…")
        s.append(f"- faithful: {r.get('faithful','?')}  ·  attribution_correct: {r.get('attribution_correct','?')}  ·  modality_correct: {r.get('modality_correct','?')}")
        if r.get("comment"):
            s.append(f"  - ⚠ {r['comment']}")
        s.append("")
    open(os.path.join(od, "unit-review-sheet.md"), "w").write("\n".join(s))

    # uncovered / overcovered
    unc, over = ["# Uncovered / partial central spans — " + case, ""], ["# Over-covered central spans — " + case, ""]
    for row in cov["rows"]:
        cv = cov_by.get(row["label"], {})
        verdict = cv.get("verdict", "covered" if row["covered"] else "missing")
        req = "REQUIRED" if row["required"] else "optional"
        if verdict in ("missing", "partial"):
            unc.append(f"- [{verdict}] ({req}) **{row['label']}** — {gold[row['label']]['expected_point']}")
            unc.append(f"    anchor: {row['quote_must_include']}")
            if cv.get("why"):
                unc.append(f"    why: {cv['why']}")
        if len(row["covering_units"]) > 1:
            over.append(f"- {row['label']}: covered by {len(row['covering_units'])} units {row['covering_units']}")
    open(os.path.join(od, "uncovered-spans.md"), "w").write("\n".join(unc) + "\n")
    open(os.path.join(od, "overcovered-spans.md"), "w").write("\n".join(over) + "\n")

    # semantic coverage tally
    by = {"covered": 0, "partial": 0, "missing": 0}
    req_missing, req_partial = [], []
    for c in rv.get("coverage", []):
        by[c["verdict"]] = by.get(c["verdict"], 0) + 1
        g = gold.get(c["label"], {})
        if g.get("required") and c["verdict"] == "missing":
            req_missing.append(c["label"])
        if g.get("required") and c["verdict"] == "partial":
            req_partial.append(c["label"])
    tot = len(rv.get("coverage", [])) or 1
    sem = (by["covered"] + 0.5 * by["partial"]) / tot

    # REVIEW.md (per case)
    urs = rv.get("unit_reviews", [])
    p0 = [u["unit_id"] for u in urs if u.get("faithful") == "no" or u.get("attribution_correct") == "no" or u.get("modality_correct") == "no"]
    rm = [
        f"# M14a.5 review — {case}", "",
        f"- accepted units: {cov['accepted_unit_count']}  ·  gold central spans: {cov['central_spans']}",
        f"- coverage (deterministic, lower bound): strict {cov['central_span_recall_strict']*100:.0f}% · adjacent-block {cov['central_span_recall']*100:.0f}%",
        f"- coverage (semantic, advisory LLM): covered {by['covered']} / partial {by['partial']} / missing {by['missing']}  ≈ {sem*100:.0f}%",
        f"- REQUIRED missing: {req_missing or 'none'}  ·  REQUIRED partial: {req_partial or 'none'}",
        f"- faithfulness/attribution/modality P0 (=no): {p0 or 'NONE'}",
        f"- notes: {rv.get('notes','')}",
        "",
        "See coverage-report.md, unit-review-sheet.md, uncovered-spans.md.",
    ]
    open(os.path.join(od, "REVIEW.md"), "w").write("\n".join(rm) + "\n")
    return {"case": case, "det_strict": cov["central_span_recall_strict"], "det_adj": cov["central_span_recall"],
            "sem": sem, "by": by, "req_missing": req_missing, "req_partial": req_partial,
            "units": cov["accepted_unit_count"], "spans": cov["central_spans"], "p0": p0, "notes": rv.get("notes", "")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=".run/m14.4/extract")
    ap.add_argument("--out", default=".run/m14a.5")
    args = ap.parse_args()
    reviews = json.load(open(os.path.join(args.out, "reviews.json"), encoding="utf-8"))
    rows = [run_case(c, args.run_dir, args.out, reviews) for c in CASES]
    S = ["# M14a.5 — faithfulness + coverage baseline (summary)", "",
         "Deterministic coverage = source-span overlap (lower bound; under-counts cross-block, over-counts in-region). Semantic coverage = advisory LLM judge (independent of the MiniMax extractor). Gate rests on semantic + faithfulness, with deterministic as a floor.", "",
         "| case | units | gold | det.strict | det.adj | semantic | req-missing | req-partial | P0 |",
         "|--|--|--|--|--|--|--|--|--|"]
    for r in rows:
        S.append(f"| {r['case']} | {r['units']} | {r['spans']} | {r['det_strict']*100:.0f}% | {r['det_adj']*100:.0f}% | "
                 f"{r['sem']*100:.0f}% (c{r['by']['covered']}/p{r['by']['partial']}/m{r['by']['missing']}) | "
                 f"{','.join(r['req_missing']) or '—'} | {','.join(r['req_partial']) or '—'} | {len(r['p0'])} |")
    open(os.path.join(args.out, "M14A5_SUMMARY.md"), "w").write("\n".join(S) + "\n")
    print("\n".join(S))


if __name__ == "__main__":
    main()
