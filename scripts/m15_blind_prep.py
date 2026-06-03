#!/usr/bin/env python3
"""M15 Phase 4 prep — build BLIND, arm-anonymized, stripped views for the
readability judge. Raw files leak the arm (OVP cards carry cited_unit_ids;
KMEM memories carry id/confidence/chunk_index), so we emit neutral
"System A / System B" text lists (title + content only) and keep the A/B↔arm
mapping in a file the readability judge never sees.

Deterministic A/B assignment: arm 'kmem' is System A on odd-index cases, System B
on even — so neither arm is consistently A (removes positional bias too).

Writes .run/m15/blind/<slug>/system_A.md, system_B.md and .run/m15/blind/mapping.json
Usage: python3 scripts/m15_blind_prep.py
"""
import json, os, glob, sys


def entries_kmem(path):
    ms = json.load(open(path, encoding="utf-8"))
    return [(m.get("title", "").strip(), (m.get("content", "") or "").strip()) for m in ms]


def entries_ovp(path):
    cs = json.load(open(path, encoding="utf-8"))
    return [(c.get("title", "").strip(), (c.get("content", "") or "").strip()) for c in cs]


def write_list(path, entries):
    md = []
    for i, (t, c) in enumerate(entries, 1):
        md.append(f"{i}. **{t}** — {c}" if t else f"{i}. {c}")
    open(path, "w", encoding="utf-8").write("\n\n".join(md) + "\n")


def main():
    mapping = {}
    slugs = sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(".run/m15/ovp/s*/cards.json"))
    for slug in slugs:
        idx = int(slug[1:])
        km = f".run/m15/kmem/{slug}/memories.json"
        ov = f".run/m15/ovp/{slug}/cards.json"
        if not (os.path.exists(km) and os.path.exists(ov)):
            print(f"  {slug}: SKIP (missing arm output)"); continue
        kmem_e, ovp_e = entries_kmem(km), entries_ovp(ov)
        kmem_is_A = (idx % 2 == 1)
        a_e, b_e = (kmem_e, ovp_e) if kmem_is_A else (ovp_e, kmem_e)
        od = f".run/m15/blind/{slug}"; os.makedirs(od, exist_ok=True)
        write_list(f"{od}/system_A.md", a_e)
        write_list(f"{od}/system_B.md", b_e)
        mapping[slug] = {"A": "kmem" if kmem_is_A else "ovp", "B": "ovp" if kmem_is_A else "kmem",
                         "kmem_n": len(kmem_e), "ovp_n": len(ovp_e)}
        print(f"  {slug}: A={mapping[slug]['A']} ({len(a_e)}) B={mapping[slug]['B']} ({len(b_e)})")
    os.makedirs(".run/m15/blind", exist_ok=True)
    json.dump(mapping, open(".run/m15/blind/mapping.json", "w"), ensure_ascii=False, indent=2)
    print(f"wrote .run/m15/blind/ for {len(mapping)} cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
