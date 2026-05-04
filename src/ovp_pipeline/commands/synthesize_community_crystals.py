"""ovp-synthesize-community-crystals — LLM synthesis of one crystal
markdown per Louvain community (BL-042, M13).

Usage::

    ovp-synthesize-community-crystals --vault-dir ~/Documents/ovp-vault
    ovp-synthesize-community-crystals --vault-dir ... --dry-run
    ovp-synthesize-community-crystals --vault-dir ... --limit-communities 5
    ovp-synthesize-community-crystals --vault-dir ... --top-k 12

The default scan reads every Louvain community in the
``research-tech`` pack and writes one crystal per community to
``40-Resources/Crystals/<sha>.md``.  Each run also persists a row
in the ``community_crystals`` table — append-only, so re-runs
produce a new version (BL-044 will surface the version chain).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..llm_client import get_litellm_client
from ..synthesis.community_crystal import (
    DEFAULT_TOP_K_EVERGREENS,
    synthesize_community_crystals,
)
from ..vault_paths import VaultLayout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synthesize one community crystal per Louvain "
                    "community using the configured LLM client.",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--pack", type=str, default="research-tech",
        help="Pack scope to synthesize crystals for (default: research-tech)",
    )
    parser.add_argument(
        "--top-k", type=int, default=DEFAULT_TOP_K_EVERGREENS,
        help=f"Max evergreens to feed the LLM per community "
             f"(default {DEFAULT_TOP_K_EVERGREENS})",
    )
    parser.add_argument(
        "--limit-communities", type=int, default=None,
        help="Process only the first N communities (sorted by cluster_id). "
             "Useful for quick smoke runs before committing to the full batch.",
    )
    parser.add_argument(
        "--cluster-id", action="append", default=None,
        help="Synthesize only the named cluster_id(s).  Can be repeated.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run the LLM and report what would be written, "
             "but skip the markdown + DB writes.",
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

    llm = get_litellm_client(vault_dir=vault)
    if llm is None:
        print(
            "LLM client unavailable — set MINIMAX_API_KEY (or "
            "AUTO_VAULT_API_KEY) and ensure litellm is installed.",
            file=sys.stderr,
        )
        return 2

    only = set(args.cluster_id) if args.cluster_id else None
    crystals = synthesize_community_crystals(
        vault_dir=vault,
        llm_client=llm,
        db_path=layout.knowledge_db,
        pack_name=args.pack,
        top_k=args.top_k,
        limit_communities=args.limit_communities,
        only_cluster_ids=only,
        dry_run=args.dry_run,
    )

    if not args.quiet:
        for c in crystals:
            print(
                f"  {len(c.source_evergreen_slugs):>3} evergreens → "
                f"{c.cluster_id}  ({len(c.body_md)} chars)"
            )

    print()
    verb = "would synthesize" if args.dry_run else "synthesized"
    print(f"=== Summary ({verb}) ===")
    print(f"  pack:                 {args.pack}")
    print(f"  communities {verb}:   {len(crystals)}")
    if args.dry_run:
        print()
        print("--dry-run set; no files or DB rows written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
