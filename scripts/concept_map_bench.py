#!/usr/bin/env python3
"""Concept Map Benchmark runner (M13).

Scores OVP's minted evergreen notes for an article against a committed,
article-grounded expected concept map. Checks CONCRETE FACTS, not abstract
scores, and is built to resist "fake green" (a correct-slug note that still
defines the wrong thing, or carries a mis-filed/foreign claim).

Per case it checks:
  1. must-have coverage      — expected concept minted by id or acceptable alias
                               (a match via a must_not_mint slug is reported
                               separately as covered_by_forbidden_alias, NOT clean)
  2. must-not-mint           — forbidden umbrella/synonym/metadata slugs absent
  3. shared-definition       — one article one-liner reused across notes
  4. claim ownership         — a note owns >=1 claim not shared verbatim elsewhere
  5. definition correctness  — definition_must_include_any / definition_must_not_include_any
  6. claim correctness       — claims_must_include_any / claims_must_not_include_any
  7. evidence grounding      — evidence_must_include_any (in definition+claims)
  8. confusion guard         — a concept's definition must NOT carry a
                               must_not_confuse_with concept's signature
  9. forbidden phrases       — case-level forbidden_phrases_anywhere (author/client
                               metadata, body-unsupported marketing numbers) in no note

Offline; needs PyYAML. Does not run the pipeline — produce OVP output first
(`ovp-next run-cycle ...`), then score it.

Usage:
  python3 scripts/concept_map_bench.py --ovp-root .run/m12q2
  python3 scripts/concept_map_bench.py --ovp-root .run/m13/out --case rag_wrong
"""
import argparse, glob, os, re, sys

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

CASE_DIRS = {
    "agent_memory_zh": ["agent_memory_zh", "agent-memory-zh"],
    "rag_wrong": ["rag_wrong", "rag-wrong"],
    "eval_ai_agents": ["eval_ai_agents", "eval-ai-agents"],
}
FIX_ROOT = "fixtures/concept_map"

def parse_note(md):
    slug = (re.search(r"^slug:\s*(.+)$", md, re.M) or [None, ""])[1].strip()
    title = (re.search(r"^title:\s*(.+)$", md, re.M) or [None, ""])[1].strip().strip('"')
    definition = (re.search(r"^>\s*(.+)$", md, re.M) or [None, ""])[1].strip()
    msec = re.search(r"## Source-backed claims\n\n(.*?)(?:\n##|\Z)", md, re.S)
    claims = re.findall(r"^- (.+)$", msec.group(1), re.M) if msec else []
    return slug, title, definition, [c.strip() for c in claims]

def find_evergreen_dir(ovp_root, fixture):
    for cand in CASE_DIRS[fixture]:
        for sub in (f"{cand}/ovp/evergreen", f"{cand}/ovp/vault/10-Knowledge/Evergreen", f"{cand}/10-Knowledge/Evergreen"):
            d = os.path.join(ovp_root, sub)
            if os.path.isdir(d):
                return d
    return None

def load_notes(d):
    notes = {}
    for fn in sorted(glob.glob(os.path.join(d, "*.md"))):
        slug, title, definition, claims = parse_note(open(fn, encoding="utf-8").read())
        slug = slug or os.path.basename(fn)[:-3]
        notes[slug] = {"title": title, "definition": definition, "claims": claims}
    return notes

def lc(s):
    return (s or "").lower()

def any_in(haystack, phrases):
    h = lc(haystack)
    return [p for p in (phrases or []) if lc(p) in h]

def run_case(fixture, ovp_root):
    exp = yaml.safe_load(open(f"{FIX_ROOT}/{fixture}/expected/concept_map.yaml", encoding="utf-8"))
    d = find_evergreen_dir(ovp_root, fixture)
    if not d:
        return None, f"no OVP evergreen dir for {fixture} under {ovp_root}"
    notes = load_notes(d)
    minted = set(notes)
    by_id = {c["id"]: c for c in exp.get("must_have", [])}
    forbidden = {m["slug"]: m.get("reason", "") for m in exp.get("must_not_mint", []) or []}
    case_forbidden = exp.get("forbidden_phrases_anywhere", []) or []

    r = {"fixture": fixture, "minted": sorted(minted), "fail": []}

    # 1. coverage (clean vs covered-by-forbidden-alias vs missing)
    covered, covered_forbidden, missing = [], [], []
    note_for = {}  # concept id -> the clean note dict used for content checks
    for c in exp.get("must_have", []):
        # Deterministic order (canonical id first, then fixture alias order), so
        # the note chosen for the content guards (clean[0]) is stable even if a
        # concept is minted under both its id and an alias.
        ids = [c["id"], *(c.get("aliases", []) or [])]
        clean = [s for s in ids if s in minted and s not in forbidden]
        forb = [s for s in ids if s in minted and s in forbidden]
        merge_forb = [s for s in (c.get("may_merge_with", []) or []) if s in minted and s in forbidden]
        if clean:
            covered.append(c["id"]); note_for[c["id"]] = notes[clean[0]]
        elif forb or merge_forb:
            covered_forbidden.append((c["id"], forb + merge_forb))
        else:
            missing.append(c["id"])
    r["covered"] = covered
    r["covered_by_forbidden_alias"] = covered_forbidden
    r["missing"] = missing
    if missing:
        r["fail"].append(f"missing must-have: {missing}")
    if covered_forbidden:
        r["fail"].append(f"covered only via forbidden alias: {[i for i,_ in covered_forbidden]}")

    # 2. must-not-mint  (sorted: `minted` is a set — keep report order stable)
    r["forbidden_minted"] = [(s, forbidden[s]) for s in sorted(minted) if s in forbidden]
    if r["forbidden_minted"]:
        r["fail"].append(f"forbidden minted: {[s for s,_ in r['forbidden_minted']]}")

    # 3. shared definition
    defs = {}
    for s, n in notes.items():
        defs.setdefault(n["definition"], []).append(s)
    r["shared_definition"] = [{"notes": ss} for d0, ss in defs.items() if d0 and len(ss) > 1]
    if r["shared_definition"]:
        r["fail"].append(f"shared definition across {sum(len(c['notes']) for c in r['shared_definition'])} notes")

    # 4. claim ownership
    owner = {}
    for s, n in notes.items():
        for cl in n["claims"]:
            owner.setdefault(cl, []).append(s)
    no_owned = []
    for s, n in notes.items():
        if not n["claims"]:
            no_owned.append((s, "no claims"))
        elif not [cl for cl in n["claims"] if len(owner[cl]) == 1]:
            no_owned.append((s, "all claims shared with other notes"))
    r["no_owned_claim"] = no_owned
    if no_owned:
        r["fail"].append(f"notes without an owned claim: {[s for s,_ in no_owned]}")

    # 5-8. per-concept content guards (only for cleanly-covered concepts)
    content_fail = []
    for cid, n in note_for.items():
        c = by_id[cid]
        defn = n["definition"]
        claims_join = " || ".join(n["claims"])
        di = c.get("definition_must_include_any")
        if di and not any_in(defn, di):
            content_fail.append((cid, f"definition lacks any of {di}"))
        dni = c.get("definition_must_not_include_any")
        if dni:
            hit = any_in(defn, dni)
            if hit:
                content_fail.append((cid, f"definition contains forbidden/confused phrase {hit}"))
        ci = c.get("claims_must_include_any")
        if ci and not any_in(claims_join, ci):
            content_fail.append((cid, f"no claim includes any of {ci}"))
        cni = c.get("claims_must_not_include_any")
        if cni:
            hit = any_in(claims_join, cni)
            if hit:
                content_fail.append((cid, f"a claim includes foreign phrase {hit}"))
        ev = c.get("evidence_must_include_any")
        if ev and not any_in(defn + " || " + claims_join, ev):
            content_fail.append((cid, f"no article-evidence anchor from {ev}"))
        # confusion guard: definition must not carry a must_not_confuse_with concept's signature
        for other in c.get("must_not_confuse_with", []) or []:
            sig = (by_id.get(other) or {}).get("definition_must_include_any") or []
            hit = any_in(defn, sig)
            if hit:
                content_fail.append((cid, f"definition matches '{other}' signature {hit} (confusion)"))
    r["content_fail"] = content_fail
    if content_fail:
        r["fail"].append(f"content-guard failures on {sorted({i for i,_ in content_fail})}")

    # 9. case-level forbidden phrases anywhere
    phrase_hits = []
    for s, n in notes.items():
        hit = any_in(n["definition"] + " || " + " || ".join(n["claims"]), case_forbidden)
        if hit:
            phrase_hits.append((s, hit))
    r["forbidden_phrase_hits"] = phrase_hits
    if phrase_hits:
        r["fail"].append(f"forbidden phrases in {[s for s,_ in phrase_hits]}")

    r["pass"] = not r["fail"]
    return r, None

def render(r):
    out = [f"### {r['fixture']}  —  {'PASS' if r['pass'] else 'FAIL'}"]
    out.append(f"- minted ({len(r['minted'])}): {', '.join(r['minted'])}")
    out.append(f"- coverage: clean={len(r['covered'])}  forbidden-alias={len(r['covered_by_forbidden_alias'])}  missing={len(r['missing'])}")
    if r["missing"]:
        out.append(f"    - **missing**: {r['missing']}")
    for cid, via in r["covered_by_forbidden_alias"]:
        out.append(f"    - **covered_by_forbidden_alias**: `{cid}` only via {via}")
    if r["forbidden_minted"]:
        out.append(f"- **forbidden minted**: {[s for s,_ in r['forbidden_minted']]}")
    if r["shared_definition"]:
        out.append(f"- **shared-definition clusters**: {len(r['shared_definition'])} ({sum(len(c['notes']) for c in r['shared_definition'])} notes)")
    if r["no_owned_claim"]:
        out.append(f"- **notes without an owned claim**: {[s for s,_ in r['no_owned_claim']]}")
    if r["content_fail"]:
        out.append(f"- **content-guard failures** ({len(r['content_fail'])}):")
        for cid, why in r["content_fail"]:
            out.append(f"    - `{cid}`: {why}")
    if r["forbidden_phrase_hits"]:
        out.append(f"- **forbidden-phrase hits**:")
        for s, hit in r["forbidden_phrase_hits"]:
            out.append(f"    - `{s}`: {hit}")
    return "\n".join(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ovp-root", default=".run/m12q2")
    ap.add_argument("--case")
    ap.add_argument("--out")
    args = ap.parse_args()
    fixtures = [args.case] if args.case else sorted(CASE_DIRS)
    blocks = ["# Concept Map Benchmark report", "", f"OVP output root: `{args.ovp_root}`", ""]
    n_pass = n_run = 0
    for fx in fixtures:
        r, err = run_case(fx, args.ovp_root)
        if err:
            blocks.append(f"### {fx} — SKIPPED\n- {err}"); blocks.append("")
            continue
        n_run += 1
        n_pass += 1 if r["pass"] else 0
        blocks.append(render(r)); blocks.append("")
    blocks.append(f"## Result: {n_pass}/{n_run} cases pass")
    report = "\n".join(blocks)
    print(report)
    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        open(args.out, "w", encoding="utf-8").write(report + "\n")
    sys.exit(0 if n_pass == n_run and n_run > 0 else 1)

if __name__ == "__main__":
    main()
