"""ovp-rescore-crystals — recompute the ``crystal_scores`` Projection
without touching any other state.

Use case: re-score after the underlying signals change (new
``source_authority`` row, new contradiction, new evergreen) without
running the full ``ovp-knowledge-index`` rebuild.  Cheap (no LLM,
no projection-wide rebuild), deterministic, idempotent.

The standard path is for ``ovp-knowledge-index`` to call
``rebuild_crystal_scores`` itself after the upstream Projections
are populated.  This CLI exists for ad-hoc re-scoring + testing.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from ..runtime import VaultLayout
from ..synthesis.crystal_scoring import rebuild_crystal_scores


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recompute the crystal_scores Projection.  "
                    "No LLM, no upstream rebuild.",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--pack", type=str, default="research-tech",
        help="Pack scope (default: research-tech).",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Print summary only, no per-crystal lines.",
    )
    args = parser.parse_args(argv)

    vault = args.vault_dir.resolve()
    if not vault.is_dir():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2
    layout = VaultLayout.from_vault(vault)
    if not layout.knowledge_db.exists():
        print(
            f"knowledge.db not found at {layout.knowledge_db}.  "
            "Run ovp-knowledge-index first.",
            file=sys.stderr,
        )
        return 2

    conn = sqlite3.connect(layout.knowledge_db)
    try:
        scores = rebuild_crystal_scores(
            conn, vault_dir=vault, pack=args.pack,
        )
    finally:
        conn.close()

    by_kind: dict[str, int] = {}
    for s in scores:
        by_kind[s.crystal_kind] = by_kind.get(s.crystal_kind, 0) + 1

    if not args.quiet and scores:
        # Top 10 by score for an at-a-glance sanity check.
        top = sorted(scores, key=lambda s: -s.score)[:10]
        print("Top 10 by score:")
        for s in top:
            print(
                f"  {s.score:.3f}  {s.crystal_kind:>13}  {s.crystal_id}  "
                f"(size={s.signals.size_norm:.2f} "
                f"cred={s.signals.credibility_norm:.2f} "
                f"contra={s.signals.contradiction_norm:.2f} "
                f"reuse={s.signals.reuse_recency_norm:.2f} "
                f"recency={s.signals.evergreen_recency_norm:.2f})"
            )
        print()
    print("=== Summary ===")
    print(f"  pack:                 {args.pack}")
    print(f"  total scored:         {len(scores)}")
    for kind, n in sorted(by_kind.items()):
        print(f"  {kind} crystals:    {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
