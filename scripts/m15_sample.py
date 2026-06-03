#!/usr/bin/env python3
"""M15 Phase 1 — deterministic sample selection (committed, reproducible).

Per docs/stage-m15-methodology-audit.md "Sampling plan":
  pool = /Users/chris/Documents/ovp-vault/50-Inbox/03-Processed (recursive *.md)
  stable lexicographic sort, FIXED seed, draw N, exclude the tuned 3,
  exclude only unreadable files (logged + replaced in stable order).

Also cross-references the draw against the 6 articles for which a KnowledgeMEM
runtime output (source-detail.json) exists, so the KMEM-arm availability of the
primary sample is explicit (the M15 Phase-3 feasibility question).

Usage: python3 scripts/m15_sample.py            # writes docs/m15/sample-manifest.{json,md}
Deterministic: same pool + same SEED + same N -> identical manifest.
"""
import json, os, random, sys

POOL = "/Users/chris/Documents/ovp-vault/50-Inbox/03-Processed"
SEED = 20260603          # FIXED + recorded (M15 registration). Do not change after the run.
N = 12
OUT_DIR = "docs/m15"

# The tuned-3 M14 cases — EXCLUDED from the primary sample (calibration only).
TUNED = {
    "2026-05-10_akshay_pachaar_-_Youre_doing_RAG_wrong.md",
    "2026-05-28_How_to_Eval_AI_Agents_-_The_2026_Guide.md",
    "2026-05-12_lxfater_-_AI_Agent_是如何记住东西？从原理到实战详细解释.md",
}

# Articles for which a KMEM runtime output already exists (the only articles where
# the KMEM arm can be supplied WITHOUT running the recovered service). Matched by a
# basename substring. (graphrag/fde live in the pool; adapt-claude-code only as a
# captured .run/eval input.md.)
KMEM_AVAILABLE_SUBSTR = {
    "Deep-GraphRAG": "graphrag-paper",
    "当我们谈论_FDE": "fde-zh",
    # adapt-claude-code: captured input only, source not confirmed in pool
}


def list_pool():
    md = []
    for root, _dirs, files in os.walk(POOL):
        for f in files:
            if f.endswith(".md"):
                md.append(os.path.join(root, f))
    md.sort()  # stable lexicographic by full path
    return md


def readable(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return len(fh.read().strip()) > 0
    except Exception:
        return False


def kmem_case_for(path):
    base = os.path.basename(path)
    for sub, case in KMEM_AVAILABLE_SUBSTR.items():
        if sub in base:
            return case
    return None


def main():
    pool = list_pool()
    eligible = [p for p in pool if os.path.basename(p) not in TUNED]
    rng = random.Random(SEED)
    # Draw a shuffled order; walk it, skipping only unreadable files (logged),
    # until N readable files are selected.
    order = eligible[:]
    rng.shuffle(order)
    sample, exclusions = [], []
    for p in order:
        if len(sample) >= N:
            break
        if readable(p):
            sample.append(p)
        else:
            exclusions.append({"path": p, "reason": "unreadable/empty"})
    sample.sort()  # present in stable order

    kmem_hits = [{"path": p, "kmem_case": kmem_case_for(p)} for p in sample if kmem_case_for(p)]

    manifest = {
        "seed": SEED,
        "n_requested": N,
        "n_selected": len(sample),
        "pool_path": POOL,
        "pool_size_md": len(pool),
        "eligible_after_tuned_exclusion": len(eligible),
        "tuned_excluded": sorted(TUNED),
        "unreadable_exclusions": exclusions,
        "sample": sample,
        "kmem_available_in_sample": kmem_hits,
        "kmem_available_count_in_sample": len(kmem_hits),
        "kmem_available_held_out_total": ["graphrag-paper", "fde-zh", "adapt-claude-code"],
        "note": (
            "KMEM arm exists as captured runtime output for 6 articles only "
            "(3 tuned [excluded] + graphrag-paper/fde-zh/adapt-claude-code). A random "
            "draw from the 942-file pool is expected to contain ~0 of them, so the "
            "primary N=12 sample has no KMEM counterpart unless the recovered service "
            "is run on each — the M15 Phase-3 blocker."
        ),
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump(manifest, open(os.path.join(OUT_DIR, "sample-manifest.json"), "w"),
              ensure_ascii=False, indent=2)

    md = [f"# M15 sample manifest (Phase 1)", "",
          f"- seed: `{SEED}` (fixed, registered) · N: {N} · selected: {len(sample)}",
          f"- pool: `{POOL}` ({len(pool)} .md; {len(eligible)} after tuned-3 exclusion)",
          f"- KMEM-arm available in this sample: **{len(kmem_hits)}** / {len(sample)}",
          "", "## Selected (primary sample)", ""]
    for i, p in enumerate(sample, 1):
        kc = kmem_case_for(p)
        md.append(f"{i}. `{os.path.relpath(p, POOL)}`" + (f"  ← KMEM:{kc}" if kc else ""))
    md += ["", "## Tuned-3 (excluded from primary; calibration only)", ""]
    md += [f"- {t}" for t in sorted(TUNED)]
    if exclusions:
        md += ["", "## Unreadable exclusions", ""] + [f"- {e['path']} ({e['reason']})" for e in exclusions]
    md += ["", "## KMEM-arm availability note", "", manifest["note"]]
    open(os.path.join(OUT_DIR, "sample-manifest.md"), "w").write("\n".join(md) + "\n")

    print(f"pool={len(pool)} eligible={len(eligible)} selected={len(sample)} "
          f"kmem_in_sample={len(kmem_hits)} exclusions={len(exclusions)}")
    print("wrote", os.path.join(OUT_DIR, "sample-manifest.json"), "and .md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
