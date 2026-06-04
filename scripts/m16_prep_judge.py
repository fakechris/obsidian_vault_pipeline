#!/usr/bin/env python3
"""M16 re-judge prep: build blind System A/B views (v2 OVP cards vs the SAME M15
KMEM memories) + the Phase-4 args.json. Reuses M15 KMEM arm + M15 units (truth
layer unchanged); only the OVP card view is the M16 v2 output.

Writes .run/m16/blind/<slug>/system_{A,B}.md, .run/m16/blind/mapping.json,
.run/m16/phase4_args.json (absolute paths). A/B: kmem is System A on odd slugs.
"""
import json, os, sys

R = "/Users/chris/Documents/ovp-next"


def entries(items, kind):
    out = []
    for x in items:
        t = x.get("title", "").strip(); c = (x.get("content", "") or "").strip()
        out.append((t, c))
    return out


def write_list(path, es):
    md = [f"{i}. **{t}** — {c}" if t else f"{i}. {c}" for i, (t, c) in enumerate(es, 1)]
    open(path, "w", encoding="utf-8").write("\n\n".join(md) + "\n")


def main():
    RUN = sys.argv[1] if len(sys.argv) > 1 else "m16"  # ovp-cards run dir + output run dir
    sample = json.load(open(f"{R}/docs/m15/sample-manifest.json"))["sample"]
    mapping, args = {}, []
    for idx, src in enumerate(sample, 1):
        slug = f"s{idx:02d}"
        km = f"{R}/.run/m15/kmem/{slug}/memories.json"
        ov = f"{R}/.run/{RUN}/ovp/{slug}/cards.json"
        units = f"{R}/.run/m15/ovp/{slug}/units.accepted.json"
        if not (os.path.exists(km) and os.path.exists(ov)):
            print(f"  {slug}: SKIP (missing arm)"); continue
        kmem_e = entries(json.load(open(km)), "kmem")
        ovp_e = entries(json.load(open(ov)), "ovp")
        kmem_is_A = (idx % 2 == 1)
        a_e, b_e = (kmem_e, ovp_e) if kmem_is_A else (ovp_e, kmem_e)
        bd = f"{R}/.run/{RUN}/blind/{slug}"; os.makedirs(bd, exist_ok=True)
        write_list(f"{bd}/system_A.md", a_e); write_list(f"{bd}/system_B.md", b_e)
        mapping[slug] = {"A": "kmem" if kmem_is_A else "ovp", "B": "ovp" if kmem_is_A else "kmem"}
        args.append({"slug": slug, "source": src, "kmem_path": km,
                     "ovp_cards_path": ov, "ovp_units_path": units, "blind_dir": bd,
                     "a_arm": mapping[slug]["A"]})
        print(f"  {slug}: A={mapping[slug]['A']} kmem={len(kmem_e)} ovp_v2={len(ovp_e)}")
    os.makedirs(f"{R}/.run/{RUN}/blind", exist_ok=True)
    json.dump(mapping, open(f"{R}/.run/{RUN}/blind/mapping.json", "w"), ensure_ascii=False, indent=2)
    json.dump(args, open(f"{R}/.run/{RUN}/phase4_args.json", "w"), ensure_ascii=False, indent=1)
    print(f"built {len(args)} cases -> .run/{RUN}/phase4_args.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
