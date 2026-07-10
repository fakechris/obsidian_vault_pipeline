#!/usr/bin/env python3
"""M14a.RCA — classify why M14a.1 evidence quotes failed to locate.

OFFLINE root-cause analysis. Reads the recorded unit review packs
(`<run-dir>/<case>/units.all.json`) + the source articles and classifies every
quote the validator did NOT locate, to decide whether the failure is OUR
pipeline (source representation / segmentation / validator) or the model
(splice / paraphrase / transcription limit).

No live calls, no network. Does not read or write cassettes.

Categories per failed quote (location == null):
  E_validator   matches the VALIDATOR's own normalization inside the ref
                paragraph but was still marked not-found  → validator bug
  A_render      matches after a proper markdown render (link TEXT extraction +
                smart-quote/dash normalization) that the validator does NOT do
                → SOURCE REPRESENTATION mismatch (model copied rendered text)
  B_boundary    matches the whole article (rendered) but no single paragraph
                → PARAGRAPH/LIST SEGMENTATION mismatch (quote spans paragraphs)
  C_splice      two halves each appear in the article but not contiguously
                → model spliced non-adjacent fragments
  D_paraphrase  none of the above → model paraphrase / not in source

Usage:
  python3 scripts/m14a_rca.py --run-dir .run/m14.1b
"""
import argparse, difflib, json, os, re, sys, unicodedata

CASES = ["rag_wrong", "eval_ai_agents", "agent_memory_zh"]
FIX = "fixtures/concept_map"

SMART = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", " ": " ",
    "「": '"', "」": '"', "＂": '"', "＇": "'",
}


def smart(s):
    for k, v in SMART.items():
        s = s.replace(k, v)
    return s


def ws(s):
    return re.sub(r"\s+", "", s)


def render_md(s):
    # [text](url) -> text ;  ![alt](url) -> alt
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"[*_`#>~]", "", s)  # emphasis / code / heading / blockquote / strike
    return s


def norm_full(s):
    "Proper plain-text render the validator does NOT do (NFKC + link text + smart quotes)."
    return ws(unicodedata.normalize("NFKC", smart(render_md(s)))).lower()


def best_ratio(qn, art_n):
    "Max SequenceMatcher ratio of qn against any same-length window of art_n."
    L = len(qn)
    if L == 0 or L > len(art_n):
        return 0.0
    best = 0.0
    for i in range(0, len(art_n) - L + 1, max(1, L // 4)):
        r = difflib.SequenceMatcher(None, qn, art_n[i : i + L]).ratio()
        if r > best:
            best = r
    return best


def vstrip_relaxed(s):
    "Mirror the Rust validator's Relaxed tier: strip md punctuation chars + ws + lower."
    return ws(re.sub(r"[*_`#>~\[\]()]", "", s)).lower()


def validator_match(q, text):
    "What the current Rust validator would accept (exact / ws-insensitive / relaxed)."
    return (q in text) or (ws(q) in ws(text)) or (vstrip_relaxed(q) in vstrip_relaxed(text))


def paragraphs(body):
    "Mirror source_map::paragraphs — maximal runs of non-blank lines."
    paras, cur = [], []
    for line in body.split("\n"):
        if line.strip() == "":
            if cur:
                paras.append("\n".join(cur)); cur = []
        else:
            cur.append(line)
    if cur:
        paras.append("\n".join(cur))
    return paras


def classify(q, article, paras, ref_text):
    q = q.strip()
    if not q:
        return "empty"
    if ref_text is not None and validator_match(q, ref_text):
        return "E_validator"
    qn = norm_full(q)
    if not qn:
        return "empty"
    an = norm_full(article)
    # A — recoverable by a proper plain-text view (NFKC + link text + smart quotes),
    #     which the validator does NOT do. The quote IS the source, rendered.
    if ref_text is not None and qn in norm_full(ref_text):
        return "A_render"
    if any(qn in norm_full(p) for p in paras):
        return "A_render_wrongpara"
    # B — segmentation: matches the whole article rendered but no single paragraph.
    if qn in an:
        return "B_boundary"
    # A_near — near-verbatim (smart apostrophe, a dropped trailing word, one
    #          punctuation diff): a slightly more tolerant validator recovers it.
    if best_ratio(qn, an) >= 0.90:
        return "A_near"
    # C — splice of two non-adjacent fragments.
    mid = len(q) // 2
    h1, h2 = norm_full(q[:mid]), norm_full(q[mid:])
    if len(h1) > 10 and len(h2) > 10 and h1 in an and h2 in an:
        return "C_splice"
    # D — genuine model rewrite: compression of a list / paraphrase. Split the
    #     band so the report distinguishes "condensed but grounded" from "loose".
    r = best_ratio(qn, an)
    return "D_compress" if r >= 0.70 else "D_paraphrase"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=".run/m14.1b")
    ap.add_argument("--examples", type=int, default=2)
    args = ap.parse_args()

    grand = {}
    for case in CASES:
        upath = os.path.join(args.run_dir, case, "units.all.json")
        ipath = os.path.join(FIX, case, "input_path.txt")
        if not os.path.exists(upath):
            print(f"# {case}: no units.all.json under {args.run_dir} — skip")
            continue
        article = open(open(ipath).read().strip(), encoding="utf-8").read()
        paras = paragraphs(article)
        units = json.load(open(upath, encoding="utf-8"))

        failed = [u for u in units if u["evidence"]["location"] is None and u["evidence"]["quote"].strip()]
        cats, examples = {}, {}
        for u in failed:
            q = u["evidence"]["quote"]
            ref = u["evidence"].get("ref_id") or u["evidence"].get("paragraph_ref", "")
            m = re.match(r"p0*(\d+)", ref or "")
            ref_text = paras[int(m.group(1)) - 1] if m and 0 < int(m.group(1)) <= len(paras) else None
            c = classify(q, article, paras, ref_text)
            cats[c] = cats.get(c, 0) + 1
            examples.setdefault(c, []).append((ref, q[:80]))
            grand[c] = grand.get(c, 0) + 1

        print(f"\n### {case}  ({len(failed)} unlocated quotes of {len(units)} units, {len(paras)} paragraphs)")
        for c in sorted(cats, key=lambda k: -cats[k]):
            print(f"  {cats[c]:>3}  {c}")
            for ref, ex in examples[c][: args.examples]:
                print(f"        [{ref}] {ex!r}")

    print("\n## TOTAL by category")
    pipeline = sum(grand.get(k, 0) for k in ("A_render", "A_render_wrongpara", "A_near", "B_boundary", "E_validator"))
    model = sum(grand.get(k, 0) for k in ("C_splice", "D_compress", "D_paraphrase"))
    for c in sorted(grand, key=lambda k: -grand[k]):
        print(f"  {grand[c]:>3}  {c}")
    total = pipeline + model
    if total:
        print(f"\n  pipeline-side (A/B/E): {pipeline}/{total} = {100*pipeline/total:.0f}%")
        print(f"  model-side   (C/D):   {model}/{total} = {100*model/total:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
