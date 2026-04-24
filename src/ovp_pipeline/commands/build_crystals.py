"""``ovp-build-crystals`` — materialize the operator briefing as a Crystal note.

A Crystal is the persistent counterpart to ``observation_surface``: where
``/briefing`` recomputes a JSON snapshot on every request, this command
freezes that snapshot to ``40-Resources/Crystals/<crystal_id>.md`` so the
review trail and the daily working-memory distill have something durable to
point at.

Idempotent: re-running with no underlying state change produces the same
crystal_id and is a no-op (the file is identical, no rewrite). When the
briefing snapshot's salient content shifts, a new crystal_id is minted and a
new file lands alongside the prior one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from ..materializers.crystal import materialize_crystal
    from ..runtime import resolve_vault_dir
    from ..truth_api import get_briefing_snapshot
except ImportError:
    from ovp_pipeline.materializers.crystal import materialize_crystal  # type: ignore
    from ovp_pipeline.runtime import resolve_vault_dir  # type: ignore
    from ovp_pipeline.truth_api import get_briefing_snapshot  # type: ignore


DEFAULT_PACK_NAME = "research-tech"
DEFAULT_LIMIT = 8


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ovp-build-crystals",
        description="Materialize the operator briefing as a persisted Crystal note.",
    )
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault root (default: cwd)")
    parser.add_argument(
        "--pack",
        default=DEFAULT_PACK_NAME,
        help=f"Pack to query (default: {DEFAULT_PACK_NAME})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Briefing item limit per category (default: {DEFAULT_LIMIT})",
    )
    parser.add_argument("--json", action="store_true", help="Print structured summary to stdout.")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    snapshot = get_briefing_snapshot(vault_dir, pack_name=args.pack, limit=args.limit)
    record = materialize_crystal(snapshot, vault_dir, pack_name=args.pack)

    summary = {
        "crystal_id": record.crystal_id,
        "path": str(record.path),
        "source_object_count": len(record.source_object_ids),
        "evolves_relation_count": len(record.evolves_relations),
        "created": record.created,
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    print("=" * 60)
    print("BUILD CRYSTALS SUMMARY")
    print("=" * 60)
    print(f"Crystal ID:           {summary['crystal_id']}")
    print(f"Path:                 {summary['path']}")
    print(f"Source objects:       {summary['source_object_count']}")
    print(f"EVOLVES relations:    {summary['evolves_relation_count']}")
    print(f"Created (vs cached):  {summary['created']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
