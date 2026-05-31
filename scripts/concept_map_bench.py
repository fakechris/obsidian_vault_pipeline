#!/usr/bin/env python3
"""Concept Map Benchmark runner (M13).

Compares OVP's minted evergreen notes for an article against a committed,
article-grounded expected concept map (fixtures/concept_map/<fixture>/expected/
concept_map.yaml). Checks concrete facts, not abstract scores:

  1. must-have coverage     — each expected concept (by id or alias) was minted
  2. must-not-mint          — forbidden umbrella/synonym/metadata slugs absent
  3. definition correctness — a note's definition is not the shared article
                              one-liner reused across many concepts
  4. claim ownership        — a note has at least one claim not shared verbatim
                              with another note (i.e. not pure recycled pool)
  5. redundancy             — must_not_mint synonyms that were nevertheless minted

Offline. Needs PyYAML. Reads OVP output from a vault/evergreen dir; it does NOT
run the pipeline (produce the output with `ovp-next run-cycle` first). Designed
to be red on current main and green after the M13 concept-map fix.

Usage:
  python3 scripts/concept_map_bench.py --ovp-root .run/m12q2
  python3 scripts/concept_map_bench.py --ovp-root .run/m13/out --case rag_wrong
"""
import argparse, glob, os, re, sys

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

# fixture dir -> the per-case subdir name used under --ovp-root
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

def run_case(fixture, ovp_root):
    exp = yaml.safe_load(open(f"{FIX_ROOT}/{fixture}/expected/concept_map.yaml", encoding="utf-8"))
    d = find_evergreen_dir(ovp_root, fixture)
    if not d:
        return None, f"no OVP evergreen dir for {fixture} under {ovp_root}"
    notes = load_notes(d)
    minted = set(notes)

    # alias map: any expected alias/id -> canonical id
    alias_to_id = {}
    for c in exp.get("must_have", []):
        alias_to_id[c["id"]] = c["id"]
        for a in c.get("aliases", []) or []:
            alias_to_id[a] = c["id"]

    r = {"fixture": fixture, "minted": sorted(minted), "checks": {}}

    # 1. must-have coverage
    covered, missing = [], []
    for c in exp.get("must_have", []):
        ids = {c["id"], *[a for a in c.get("aliases", []) or []]}
        merges = set(c.get("may_merge_with", []) or [])
        if (ids & minted) or (merges & minted):
            covered.append(c["id"])
        else:
            missing.append(c["id"])
    r["checks"]["must_have_covered"] = covered
    r["checks"]["must_have_missing"] = missing

    # 2 + 5. must-not-mint / redundancy
    forbidden = {m["slug"]: m.get("reason", "") for m in exp.get("must_not_mint", []) or []}
    minted_forbidden = [(s, forbidden[s]) for s in minted if s in forbidden]
    r["checks"]["forbidden_minted"] = minted_forbidden

    # 3. definition correctness — shared one-liner reused across notes
    defs = {}
    for s, n in notes.items():
        defs.setdefault(n["definition"], []).append(s)
    shared = {d0: ss for d0, ss in defs.items() if d0 and len(ss) > 1}
    r["checks"]["shared_definition_clusters"] = [
        {"definition": d0[:120], "notes": ss} for d0, ss in shared.items()
    ]

    # 4. claim ownership — a note whose every claim is shared verbatim with another note
    claim_owner = {}
    for s, n in notes.items():
        for cl in n["claims"]:
            claim_owner.setdefault(cl, []).append(s)
    no_owned = []
    for s, n in notes.items():
        if not n["claims"]:
            no_owned.append((s, "no claims"))
            continue
        owned = [cl for cl in n["claims"] if len(claim_owner[cl]) == 1]
        if not owned:
            no_owned.append((s, "all claims shared with other notes"))
    r["checks"]["notes_without_owned_claim"] = no_owned

    # verdict
    r["pass"] = (not missing and not minted_forbidden and not shared and not no_owned)
    return r, None

def render(r):
    out = [f"### {r['fixture']}  —  {'PASS' if r['pass'] else 'FAIL'}"]
    out.append(f"- minted ({len(r['minted'])}): {', '.join(r['minted'])}")
    ch = r["checks"]
    out.append(f"- must-have covered: {len(ch['must_have_covered'])}; **missing: {ch['must_have_missing'] or 'none'}**")
    out.append(f"- **forbidden minted: {[s for s,_ in ch['forbidden_minted']] or 'none'}**")
    for s, why in ch["forbidden_minted"]:
        out.append(f"    - `{s}` — {why}")
    sd = ch["shared_definition_clusters"]
    out.append(f"- **shared-definition clusters: {len(sd)}**")
    for c in sd:
        out.append(f"    - {len(c['notes'])} notes share one definition: {', '.join(c['notes'])}")
        out.append(f"      \"{c['definition']}…\"")
    nw = ch["notes_without_owned_claim"]
    out.append(f"- **notes with no concept-owned claim: {len(nw)}**")
    for s, why in nw:
        out.append(f"    - `{s}` — {why}")
    return "\n".join(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ovp-root", default=".run/m12q2", help="root holding <case>/ovp/evergreen/*.md")
    ap.add_argument("--case", help="single fixture (agent_memory_zh|rag_wrong|eval_ai_agents)")
    ap.add_argument("--out", help="write the markdown report to this path too")
    args = ap.parse_args()

    fixtures = [args.case] if args.case else sorted(CASE_DIRS)
    blocks = ["# Concept Map Benchmark report", "", f"OVP output root: `{args.ovp_root}`", ""]
    n_pass = 0
    for fx in fixtures:
        r, err = run_case(fx, args.ovp_root)
        if err:
            blocks.append(f"### {fx} — SKIPPED\n- {err}")
            continue
        n_pass += 1 if r["pass"] else 0
        blocks.append(render(r))
        blocks.append("")
    blocks.append(f"## Result: {n_pass}/{len(fixtures)} cases pass")
    report = "\n".join(blocks)
    print(report)
    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        open(args.out, "w", encoding="utf-8").write(report + "\n")

if __name__ == "__main__":
    main()
