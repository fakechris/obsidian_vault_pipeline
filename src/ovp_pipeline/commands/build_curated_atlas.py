"""ovp-build-curated-atlas — render the top-N crystals into one
markdown atlas at ``40-Resources/CuratedAtlas.md`` (BL-046, M14).

Reads ``crystal_scores`` (BL-045) joined with the synthesis substrate
to produce a single user-facing entry page over the crystal corpus.
No LLM cost; pure read of existing Projections + filesystem write.

Usage::

    ovp-build-curated-atlas --vault-dir ~/Documents/ovp-vault
    ovp-build-curated-atlas --vault-dir ... --top-n 50
    ovp-build-curated-atlas --vault-dir ... --dry-run

The command takes the ``knowledge_db_write_lock`` because the
markdown is one of two on-disk atlas surfaces and we want it
consistent with whatever ``ovp-knowledge-index`` is doing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..runtime import VaultLayout, knowledge_db_write_lock
from ..synthesis.curated_atlas import (
    DEFAULT_TOP_N,
    write_curated_atlas,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render the top-N curated atlas markdown from "
                    "the crystal_scores Projection.",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--pack", type=str, default="research-tech",
        help="Pack scope (default: research-tech).",
    )
    parser.add_argument(
        "--top-n", type=int, default=DEFAULT_TOP_N,
        help=f"How many crystals to surface (default {DEFAULT_TOP_N}).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compose the atlas but don't write the markdown.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Print summary only, no per-entry preview.",
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

    with knowledge_db_write_lock(vault):
        atlas, target = write_curated_atlas(
            vault,
            db_path=layout.knowledge_db,
            pack=args.pack,
            top_n=args.top_n,
            dry_run=args.dry_run,
        )

    # How many entries to preview in the CLI output.  Matches the
    # rough screen-height limit of a terminal scan; keeps the
    # summary one-pager readable.
    _PREVIEW_LIMIT = 10
    if not args.quiet and atlas.entries:
        print("Top entries:")
        for entry in atlas.entries[:_PREVIEW_LIMIT]:
            print(
                f"  {entry.rank:>2}. {entry.score:.3f}  "
                f"{entry.crystal_kind:>13}  {entry.label}"
            )
        if len(atlas.entries) > _PREVIEW_LIMIT:
            print(f"  ... and {len(atlas.entries) - _PREVIEW_LIMIT} more")
        print()

    verb = "would write" if args.dry_run else "wrote"
    print("=== Summary ===")
    print(f"  pack:           {atlas.pack}")
    print(f"  top_n:          {atlas.top_n}")
    print(f"  selected:       {len(atlas.entries)}")
    print(f"  total chains:   {atlas.total_chains}")
    print(f"  {verb}:        {target}")
    if args.dry_run:
        print()
        print("--dry-run set; no file written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
