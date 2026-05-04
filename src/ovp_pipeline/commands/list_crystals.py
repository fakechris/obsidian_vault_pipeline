"""ovp-list-crystals — surface the crystal version chain (BL-044, M13).

Reads the ``community_crystals`` and ``contradiction_crystals``
tables and prints one row per chain (or per version with
``--show-chain``).  Read-only — never writes to disk or DB.

Usage::

    ovp-list-crystals --vault-dir ~/Documents/ovp-vault
    ovp-list-crystals --vault-dir ... --kind contradiction
    ovp-list-crystals --vault-dir ... --show-chain
    ovp-list-crystals --vault-dir ... --pack research-tech
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from ..runtime import VaultLayout

_KIND_COMMUNITY = "community"
_KIND_CONTRADICTION = "contradiction"
_KIND_ALL = "all"


def _list_community_chains(
    conn: sqlite3.Connection, pack: str,
) -> list[tuple[str, str, int, str]]:
    """Return ``[(cluster_id, label, version_count, latest_synth_at), ...]``
    sorted by cluster_id."""
    rows = conn.execute(
        """
        SELECT cc.cluster_id,
               COALESCE(gc.label, '') AS label,
               COUNT(*) AS n_versions,
               MAX(cc.synthesized_at) AS latest_at
          FROM community_crystals AS cc
          LEFT JOIN graph_clusters AS gc
            ON gc.pack = cc.pack AND gc.cluster_id = cc.cluster_id
         WHERE cc.pack = ?
         GROUP BY cc.cluster_id, gc.label
         ORDER BY cc.cluster_id
        """,
        (pack,),
    ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def _list_contradiction_chains(
    conn: sqlite3.Connection, pack: str,
) -> list[tuple[str, str, int, str]]:
    """Return ``[(contradiction_id, subject_key, version_count,
    latest_synth_at), ...]`` sorted by contradiction_id."""
    rows = conn.execute(
        """
        SELECT contradiction_id,
               subject_key,
               COUNT(*) AS n_versions,
               MAX(synthesized_at) AS latest_at
          FROM contradiction_crystals
         WHERE pack = ?
         GROUP BY contradiction_id, subject_key
         ORDER BY contradiction_id
        """,
        (pack,),
    ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def _list_community_versions(
    conn: sqlite3.Connection, pack: str, cluster_id: str,
) -> list[tuple[str, str]]:
    """Return ``[(synthesized_at, superseded_by_synthesized_at), ...]``
    in chronological order."""
    rows = conn.execute(
        """
        SELECT synthesized_at, superseded_by_synthesized_at
          FROM community_crystals
         WHERE pack = ? AND cluster_id = ?
         ORDER BY synthesized_at
        """,
        (pack, cluster_id),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _list_contradiction_versions(
    conn: sqlite3.Connection, pack: str, contradiction_id: str,
) -> list[tuple[str, str]]:
    rows = conn.execute(
        """
        SELECT synthesized_at, superseded_by_synthesized_at
          FROM contradiction_crystals
         WHERE pack = ? AND contradiction_id = ?
         ORDER BY synthesized_at
        """,
        (pack, contradiction_id),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _print_chains(
    *,
    title: str,
    id_col_name: str,
    label_col_name: str,
    chains: list[tuple[str, str, int, str]],
) -> None:
    if not chains:
        print(f"=== {title} ===")
        print("  (no crystals)")
        print()
        return
    n_chains = len(chains)
    n_versions = sum(c[2] for c in chains)
    print(f"=== {title} ===")
    print(f"  {n_chains} chain{'s' if n_chains != 1 else ''}, "
          f"{n_versions} total version{'s' if n_versions != 1 else ''}")
    # Tabular layout — keep id column wide enough for the longest one.
    id_w = max(len(id_col_name), max(len(c[0]) for c in chains))
    print(f"  {id_col_name:<{id_w}}  {'versions':>8}  "
          f"{'latest':<26}  {label_col_name}")
    for cid, label, n, latest in chains:
        print(f"  {cid:<{id_w}}  {n:>8}  {latest:<26}  {label}")
    print()


def _print_versions(
    *,
    title: str,
    chain_id: str,
    versions: list[tuple[str, str]],
) -> None:
    print(f"  {title}: {chain_id}")
    for synth_at, superseded_by in versions:
        marker = "current" if not superseded_by else f"→ {superseded_by}"
        print(f"    {synth_at:<26}  {marker}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List crystal chains and version counts.",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--pack", type=str, default="research-tech",
        help="Pack scope to list (default: research-tech).",
    )
    parser.add_argument(
        "--kind", choices=[_KIND_COMMUNITY, _KIND_CONTRADICTION, _KIND_ALL],
        default=_KIND_ALL,
        help="Crystal kind to list (default: all).",
    )
    parser.add_argument(
        "--show-chain", action="store_true",
        help="Also print every version in each chain with its "
             "supersede pointer.  Default is one row per chain.",
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
        if args.kind in (_KIND_COMMUNITY, _KIND_ALL):
            chains = _list_community_chains(conn, args.pack)
            _print_chains(
                title=f"Community crystals ({args.pack})",
                id_col_name="cluster_id",
                label_col_name="label",
                chains=chains,
            )
            if args.show_chain:
                for cid, _label, _n, _latest in chains:
                    versions = _list_community_versions(conn, args.pack, cid)
                    _print_versions(title="cluster", chain_id=cid,
                                    versions=versions)
                if chains:
                    print()
        if args.kind in (_KIND_CONTRADICTION, _KIND_ALL):
            chains = _list_contradiction_chains(conn, args.pack)
            _print_chains(
                title=f"Contradiction crystals ({args.pack})",
                id_col_name="contradiction_id",
                label_col_name="subject_key",
                chains=chains,
            )
            if args.show_chain:
                for cid, _label, _n, _latest in chains:
                    versions = _list_contradiction_versions(conn, args.pack, cid)
                    _print_versions(title="contradiction", chain_id=cid,
                                    versions=versions)
                if chains:
                    print()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
