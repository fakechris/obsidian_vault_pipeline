"""ovp-synthesize-contradiction-crystals — LLM synthesis of one
"open question" crystal per open contradiction (BL-043, M13).

Usage::

    ovp-synthesize-contradiction-crystals --vault-dir ~/Documents/ovp-vault
    ovp-synthesize-contradiction-crystals --vault-dir ... --dry-run
    ovp-synthesize-contradiction-crystals --vault-dir ... --limit 5
    ovp-synthesize-contradiction-crystals --vault-dir ... \\
        --contradiction-id contradiction::abc123def456

Reads ``status='open'`` rows from the ``contradictions`` table,
sends each one's positive/negative claim pair to the LLM, and
writes ``40-Resources/Crystals/contradiction-<sha>.md`` plus a
lineage row in ``contradiction_crystals``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..llm_client import get_litellm_client
from ..synthesis.contradiction_crystal import (
    synthesize_contradiction_crystals,
)
from ..vault_paths import VaultLayout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synthesize one open-question crystal per open "
                    "contradiction using the configured LLM client.",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--pack", type=str, default="research-tech",
        help="Pack scope to synthesize crystals for (default: research-tech)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process only the first N contradictions (sorted by "
             "contradiction_id).  Useful for smoke runs before the full batch.",
    )
    parser.add_argument(
        "--contradiction-id", action="append", default=None,
        help="Synthesize only the named contradiction_id(s).  Can be repeated.",
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

    only = set(args.contradiction_id) if args.contradiction_id else None
    crystals = synthesize_contradiction_crystals(
        vault_dir=vault,
        llm_client=llm,
        db_path=layout.knowledge_db,
        pack_name=args.pack,
        only_contradiction_ids=only,
        limit=args.limit,
        dry_run=args.dry_run,
    )

    if not args.quiet:
        for c in crystals:
            print(
                f"  pos={len(c.positive_claim_ids):>2} "
                f"neg={len(c.negative_claim_ids):>2} → "
                f"{c.contradiction_id} "
                f"({c.subject_key!r}, {len(c.body_md)} chars)"
            )

    print()
    verb = "would synthesize" if args.dry_run else "synthesized"
    print(f"=== Summary ({verb}) ===")
    print(f"  pack:                    {args.pack}")
    print(f"  contradictions {verb}:   {len(crystals)}")
    if args.dry_run:
        print()
        print("--dry-run set; no files or DB rows written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
