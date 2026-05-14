"""CLI surface for the lifecycle kernel + projection (M24.1).

Two entry points land here:

* ``ovp-ops-state`` — rebuild / show-counts for the ``ops_state``
  projection.  Hooks into the pipeline DAG via the ``ops_state``
  step (see :mod:`unified_pipeline_enhanced`).
* ``ovp-lifecycle-show`` — print the kernel's full evidence trail
  for one item.  Debugging surface for M24.2's producer audit; the
  output is intentionally human-readable rather than JSON-first.

Both commands read from ``<vault>/60-Logs/knowledge.db`` exclusively
— no markdown, no producer calls.  Keep it that way; the kernel's
purity is what makes M24's truth claims defensible.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from ..ops_lifecycle import (
    ALL_ITEM_KINDS,
    ALL_STATES,
    lifecycle_state_of,
)
from ..ops_state import counts_from_projection, rebuild
from ..packs.loader import DEFAULT_WORKFLOW_PACK_NAME
from ..runtime import resolve_vault_dir


KNOWLEDGE_DB_REL = "60-Logs/knowledge.db"


def _db_path(vault_dir: Path) -> Path:
    return vault_dir / KNOWLEDGE_DB_REL


def main(argv: list[str] | None = None) -> int:
    """``ovp-ops-state`` entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild or inspect the ops_state projection "
            "(M24.1 lifecycle contract layer)."
        )
    )
    parser.add_argument(
        "--vault-dir", type=Path, default=None, help="Vault directory"
    )
    parser.add_argument(
        "--pack",
        default=DEFAULT_WORKFLOW_PACK_NAME,
        help="Pack to rebuild / show",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Truncate + repopulate ops_state rows for --pack",
    )
    parser.add_argument(
        "--show-counts",
        action="store_true",
        help="Print the five-state count distribution",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON output"
    )
    args = parser.parse_args(argv)

    if not (args.rebuild or args.show_counts):
        parser.error("specify --rebuild or --show-counts")

    vault_dir = resolve_vault_dir(args.vault_dir)
    db_path = _db_path(vault_dir)
    if not db_path.is_file():
        print(
            f"ops-state: no knowledge.db at {db_path} — "
            "run `ovp-knowledge-index` first.",
            file=sys.stderr,
        )
        return 1

    if args.rebuild:
        with sqlite3.connect(str(db_path)) as conn:
            counts = rebuild(conn, pack=args.pack)
        payload = {
            "vault_dir": str(vault_dir),
            "pack": args.pack,
            "counts": counts,
            "total": sum(counts.values()),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"ops-state rebuilt for pack={args.pack}")
            for state in ALL_STATES:
                print(f"  {state:<14} {counts.get(state, 0)}")
            print(f"  {'TOTAL':<14} {payload['total']}")
        return 0

    # --show-counts (read-only).
    with sqlite3.connect(str(db_path)) as conn:
        counts = counts_from_projection(conn, pack=args.pack)
    payload = {
        "vault_dir": str(vault_dir),
        "pack": args.pack,
        "counts": counts,
        "total": sum(counts.values()),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"ops-state counts for pack={args.pack}")
        for state in ALL_STATES:
            print(f"  {state:<14} {counts.get(state, 0)}")
        print(f"  {'TOTAL':<14} {payload['total']}")
    return 0


def show_main(argv: list[str] | None = None) -> int:
    """``ovp-lifecycle-show`` entry point.

    Usage: ``ovp-lifecycle-show <kind> <id> [--pack P]``.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Print the lifecycle kernel's evidence trail for one "
            "item.  Reads ops_lifecycle directly — no projection "
            "involved, so this works even before --rebuild has run."
        )
    )
    parser.add_argument(
        "kind",
        choices=ALL_ITEM_KINDS,
        help=f"Item kind: one of {ALL_ITEM_KINDS}",
    )
    parser.add_argument("item_id", help="Item identifier (slug / object_id / cluster_id)")
    parser.add_argument(
        "--vault-dir", type=Path, default=None, help="Vault directory"
    )
    parser.add_argument(
        "--pack",
        default=DEFAULT_WORKFLOW_PACK_NAME,
        help="Pack scope for the lookup",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON output"
    )
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    db_path = _db_path(vault_dir)
    if not db_path.is_file():
        print(
            f"lifecycle-show: no knowledge.db at {db_path}",
            file=sys.stderr,
        )
        return 1

    with sqlite3.connect(str(db_path)) as conn:
        state = lifecycle_state_of(
            conn, args.kind, args.item_id, pack=args.pack
        )

    if state is None:
        msg = {
            "kind": args.kind,
            "item_id": args.item_id,
            "pack": args.pack,
            "state": None,
            "reason": "no audit evidence and no projection row",
        }
        if args.json:
            print(json.dumps(msg, ensure_ascii=False, indent=2))
        else:
            print(
                f"lifecycle-show: no audit evidence and no projection "
                f"row for {args.kind} {args.item_id!r} in "
                f"pack={args.pack}"
            )
        return 2

    payload = {
        "kind": state.item_kind,
        "item_id": state.item_id,
        "pack": state.pack,
        "state": state.state,
        "sub_state": state.sub_state,
        "last_evidence_at": state.last_evidence_at,
        "needs_action_reason": state.needs_action_reason,
        "evidence": list(state.evidence),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"lifecycle: {state.item_kind} {state.item_id!r} (pack={state.pack})")
    print(f"  state           : {state.state}")
    if state.sub_state:
        print(f"  sub_state       : {state.sub_state}")
    print(f"  last_evidence_at: {state.last_evidence_at or '(none)'}")
    if state.needs_action_reason:
        print(f"  needs_action    : {state.needs_action_reason}")
    print(f"  evidence (newest first):")
    if not state.evidence:
        print("    (none — classification from projection only)")
    for et in state.evidence:
        print(f"    - {et}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
