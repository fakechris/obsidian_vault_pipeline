"""ovp-entity-aliases — dump the unioned alias view.

Inspection tool for PR-G1 (BL-038).  Two output modes:

  default  — human-readable summary: count by canonical_entity_type,
             top-N entities by number of aliases
  --json   — full flat list of EntityAlias rows for piping into
             other tools (e.g., the prompt-prime job in PR-G2)
  --grouped-json — index form: ``{canonical_handle: [aliases]}``

The CLI exists primarily for sanity-checking before shipping
PR-G2 (extraction prime) and PR-G3 (auto-wikilink) so a human can
audit whether the merged view actually covers the entities we
want to prime.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from ..entities.aliases import (
    EntityAlias,
    build_alias_index,
    collect_entity_aliases,
)
from ..entities.store import EntityStore


def _human_summary(aliases: list[EntityAlias]) -> None:
    by_type: dict[str, int] = defaultdict(int)
    by_canonical: dict[str, list[EntityAlias]] = defaultdict(list)
    for a in aliases:
        by_type[a.canonical_entity_type] += 1
        by_canonical[a.canonical_handle].append(a)

    print(f"alias rows:           {len(aliases):>5}")
    print(f"unique canonicals:    {len(by_canonical):>5}")
    print()
    print("by canonical_entity_type:")
    for t, n in sorted(by_type.items(), key=lambda kv: -kv[1]):
        print(f"  {t:<22} {n:>5}")
    print()

    # Top-20 canonicals by alias count — these are the entities the
    # extraction-time prime (PR-G2) will benefit from most.
    top = sorted(by_canonical.items(), key=lambda kv: -len(kv[1]))[:20]
    print("top 20 canonicals by alias count:")
    for handle, rows in top:
        kinds = sorted({r.alias_kind for r in rows})
        sample = sorted({r.alias for r in rows})[:5]
        print(f"  {handle:<25} {len(rows):>3} aliases  "
              f"kinds={kinds}  sample={sample}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dump the unioned entity_aliases view (BL-038)",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--json", action="store_true",
        help="Emit the flat list as JSON",
    )
    parser.add_argument(
        "--grouped-json", action="store_true",
        help="Emit ``{canonical_handle: [aliases]}`` index form",
    )
    parser.add_argument(
        "--collisions", action="store_true",
        help="Print only rows where the same alias string is claimed "
             "by multiple canonicals (audit aid)",
    )
    args = parser.parse_args(argv)

    vault = args.vault_dir.resolve()
    if not vault.is_dir():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2

    store = EntityStore(db_path=vault / "60-Logs" / "knowledge.db")
    aliases = collect_entity_aliases(vault_dir=vault, entity_store=store)

    if args.collisions:
        # Build index without our preferred precedence — we want the
        # raw collisions before resolution.
        by_alias: dict[str, list[EntityAlias]] = defaultdict(list)
        for a in aliases:
            by_alias[a.alias].append(a)
        collisions = {
            k: v for k, v in by_alias.items()
            if len({r.canonical_handle for r in v}) > 1
        }
        if args.json:
            print(json.dumps(
                {k: [asdict(r) for r in v] for k, v in collisions.items()},
                ensure_ascii=False, indent=2,
            ))
        else:
            print(f"alias collisions: {len(collisions)}")
            for k, v in sorted(collisions.items())[:50]:
                pointers = sorted({
                    f"{r.canonical_handle}({r.source})" for r in v
                })
                print(f"  {k:<30} → {pointers}")
        return 0

    if args.json:
        print(json.dumps(
            [asdict(a) for a in aliases],
            ensure_ascii=False, indent=2,
        ))
        return 0

    if args.grouped_json:
        index = build_alias_index(aliases)
        grouped: dict[str, list[str]] = defaultdict(list)
        for alias, row in index.items():
            grouped[row.canonical_handle].append(alias)
        print(json.dumps(
            {h: sorted(set(v)) for h, v in grouped.items()},
            ensure_ascii=False, indent=2,
        ))
        return 0

    _human_summary(aliases)
    return 0


if __name__ == "__main__":
    sys.exit(main())
