#!/usr/bin/env python3
"""M14a.5 — span-anchored coverage scorer (deterministic, offline).

Answers: do the M14a.4 accepted Units COVER the article's central source spans?
Scoring is by SOURCE-SPAN OVERLAP / quote containment — never label/slug/text
equality, never fuzzy semantic match.

Inputs:
  fixtures/unit_coverage/<case>/central_units.yml   (gold; committed)
  <run-dir>/<case>/units.accepted.json              (M14a.4 output; .run, not committed)
  fixtures/concept_map/<case>/input_path.txt -> source article

Per case it computes:
  - gold anchor validity (every quote_must_include must occur in the source)
  - central_span_recall  (gold spans covered by >=1 accepted unit's evidence)
  - required recall + uncovered required spans
  - over-coverage (gold spans covered by >1 unit)
  - accepted unit count + units covering no gold span (candidate noise/over-split)

Usage: python3 scripts/m14a5_coverage.py --run-dir .run/m14.4/extract --out .run/m14a.5
"""
import argparse, json, os, re, sys, unicodedata

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

CASES = ["rag_wrong", "eval_ai_agents", "agent_memory_zh"]
GOLD_DIR = "fixtures/unit_coverage"
FIX = "fixtures/concept_map"


def strip_links(s):
    return re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)


def norm_seq(s):
    """Return (chars, orig_byte) keeping a map from each kept normalized char to
    its original UTF-8 byte offset. Drops whitespace + markdown noise; NFKC- and
    case-folds. Same spirit as the Rust validator's render-normalization."""
    s2 = strip_links(s)
    chars, orig = [], []
    b = 0
    for ch in s2:
        blen = len(ch.encode("utf-8"))
        if not (ch.isspace() or ch in "*_`#>~[]()"):
            for fc in unicodedata.normalize("NFKC", ch).lower():
                chars.append(fc)
                orig.append(b)
        b += blen
    return chars, orig


def norm_str(s):
    c, _ = norm_seq(s)
    return "".join(c)


def find_span(source, phrase):
    """Byte range [start,end) in `source` of the normalized `phrase`, or None."""
    hay, orig = norm_seq(source)
    needle = norm_str(phrase)
    if not needle:
        return None
    hs = "".join(hay)
    i = hs.find(needle)
    if i < 0:
        return None
    start = orig[i]
    end_idx = i + len(needle)
    end = orig[end_idx] if end_idx < len(orig) else len(source.encode("utf-8"))
    return (start, end)


def overlaps(a, b):
    return a and b and a[0] < b[1] and b[0] < a[1]


def paragraph_blocks(source):
    """Byte ranges of blank-line-separated paragraphs (maximal runs of non-blank
    lines) — mirrors the Rust source_map::paragraphs granularity. Coverage is
    judged at THIS granularity: a central point is covered if an accepted unit
    anchors anywhere in the same source paragraph (a unit legitimately captures a
    point by quoting any sentence of that paragraph). NOTE: this can over-credit
    when one paragraph carries multiple central points — the review pack lists the
    covering unit so a human can spot-check."""
    blocks, start, end, pos = [], None, 0, 0
    for line in source.split("\n"):
        ll = len(line.encode("utf-8")) + 1  # +1 for the '\n'
        if line.strip() == "":
            if start is not None:
                blocks.append((start, end)); start = None
        else:
            if start is None:
                start = pos
            end = pos + len(line.encode("utf-8"))
        pos += ll
    if start is not None:
        blocks.append((start, end))
    return blocks


def block_of(blocks, span):
    """The paragraph block containing the gold anchor's start byte (expanded to
    the full paragraph), or the anchor span itself if no block matches."""
    if not span:
        return None
    for b in blocks:
        if b[0] <= span[0] < b[1]:
            return b
    return span


def block_window(blocks, span):
    """The gold anchor's block PLUS its immediately-adjacent blocks (±1). The
    articles split one point's topic sentence, bullets, elaboration, and images
    into separate blank-line blocks, so a unit legitimately covering the point
    often anchors in the neighbouring block. Verified by inspection on rag_wrong
    (the 3 strict-misses each had a covering unit 79-100 bytes away in an adjacent
    block). This widens coverage to that neighbourhood; it can over-credit across
    a 2-point boundary, so the LLM review (Tier 2) is the authoritative check."""
    if not span:
        return None
    for i, b in enumerate(blocks):
        if b[0] <= span[0] < b[1]:
            lo = blocks[max(0, i - 1)][0]
            hi = blocks[min(len(blocks) - 1, i + 1)][1]
            return (lo, hi)
    return span


def run_case(case, run_dir, out_dir):
    gold_path = os.path.join(GOLD_DIR, case, "central_units.yml")
    units_path = os.path.join(run_dir, case, "units.accepted.json")
    if not (os.path.exists(gold_path) and os.path.exists(units_path)):
        return None, f"missing gold or units for {case}"
    source = open(open(os.path.join(FIX, case, "input_path.txt")).read().strip(), encoding="utf-8").read()
    gold = yaml.safe_load(open(gold_path, encoding="utf-8"))
    units = json.load(open(units_path, encoding="utf-8"))
    blocks = paragraph_blocks(source)

    # unit source ranges (from the validator's derived evidence location).
    unit_ranges = []
    for u in units:
        loc = u["evidence"].get("location")
        rng = (loc["byte_start"], loc["byte_end"]) if loc else None
        unit_ranges.append({"id": u["id"], "range": rng, "quote": u["evidence"]["quote"]})

    rows, invalid, unit_cover_count = [], [], {u["id"]: 0 for u in units}
    for g in gold.get("central_units", []):
        phrase = g["quote_must_include"]
        gspan = find_span(source, phrase)
        if gspan is None:
            invalid.append(g["label"])  # gold anchor not in source -> bad gold
        gblock = block_of(blocks, gspan)      # the anchor's own paragraph (strict)
        gwin = block_window(blocks, gspan)    # + adjacent blocks (neighbourhood)
        def qcontain(ur):
            return (norm_str(phrase) and norm_str(phrase) in norm_str(ur["quote"])) or (
                norm_str(ur["quote"]) and norm_str(ur["quote"]) in norm_str(phrase))
        covering_strict, covering = [], []
        for ur in unit_ranges:
            if overlaps(gblock, ur["range"]) or qcontain(ur):
                covering_strict.append(ur["id"])
            if overlaps(gwin, ur["range"]) or qcontain(ur):
                covering.append(ur["id"])
                unit_cover_count[ur["id"]] += 1
        rows.append({
            "label": g["label"], "kind": g["kind"], "required": bool(g.get("required")),
            "quote_must_include": phrase, "gold_anchor_found": gspan is not None,
            "covered": len(covering) > 0, "covered_strict": len(covering_strict) > 0,
            "covering_units": covering,
        })

    total = len(rows)
    covered = [r for r in rows if r["covered"]]
    req = [r for r in rows if r["required"]]
    req_covered = [r for r in req if r["covered"]]
    uncovered_required = [r["label"] for r in req if not r["covered"]]
    over = [r["label"] for r in rows if len(r["covering_units"]) > 1]
    noise_units = [uid for uid, n in unit_cover_count.items() if n == 0]

    covered_strict = [r for r in rows if r["covered_strict"]]
    rep = {
        "case": case,
        "accepted_unit_count": len(units),
        "central_spans": total,
        "central_span_recall": round(len(covered) / total, 3) if total else 0.0,
        "central_span_recall_strict": round(len(covered_strict) / total, 3) if total else 0.0,
        "required_spans": len(req),
        "required_recall": round(len(req_covered) / len(req), 3) if req else 1.0,
        "uncovered_required": uncovered_required,
        "over_covered_spans": over,
        "units_covering_no_gold_span": len(noise_units),
        "invalid_gold_anchors": invalid,
        "rows": rows,
    }
    os.makedirs(os.path.join(out_dir, case), exist_ok=True)
    json.dump(rep, open(os.path.join(out_dir, case, "coverage-report.json"), "w"), ensure_ascii=False, indent=2)
    open(os.path.join(out_dir, case, "coverage-report.md"), "w").write(render_md(rep))
    return rep, None


def render_md(r):
    s = [f"# Coverage — {r['case']}", ""]
    s.append(f"- accepted units: {r['accepted_unit_count']}  ·  central gold spans: {r['central_spans']}")
    s.append(f"- **central_span_recall: {r['central_span_recall']*100:.0f}%**  ·  required_recall: {r['required_recall']*100:.0f}%")
    s.append(f"- units covering no gold span: {r['units_covering_no_gold_span']}  ·  over-covered: {len(r['over_covered_spans'])}")
    if r["invalid_gold_anchors"]:
        s.append(f"- ⚠ **invalid gold anchors (not in source)**: {r['invalid_gold_anchors']}")
    if r["uncovered_required"]:
        s.append(f"- ❌ **uncovered REQUIRED**: {r['uncovered_required']}")
    s.append("\n| covered | req | kind | label | anchor |")
    s.append("|--|--|--|--|--|")
    for row in r["rows"]:
        c = "✅" if row["covered"] else "❌"
        rq = "R" if row["required"] else ""
        s.append(f"| {c} | {rq} | {row['kind']} | {row['label']} | {row['quote_must_include'][:48]} |")
    return "\n".join(s) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=".run/m14.4/extract")
    ap.add_argument("--out", default=".run/m14a.5")
    args = ap.parse_args()
    reps = []
    for c in CASES:
        rep, err = run_case(c, args.run_dir, args.out)
        if err:
            print(f"# {c}: SKIP — {err}")
            continue
        reps.append(rep)
        print(f"{c}: recall(adj)={rep['central_span_recall']*100:.0f}% recall(strict)={rep['central_span_recall_strict']*100:.0f}% "
              f"required={rep['required_recall']*100:.0f}% units={rep['accepted_unit_count']} "
              f"uncovered_required={rep['uncovered_required']} invalid_gold={rep['invalid_gold_anchors']}")
    return 0 if reps else 1


if __name__ == "__main__":
    sys.exit(main())
