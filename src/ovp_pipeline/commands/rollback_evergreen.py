"""``ovp-rollback-evergreen`` — restore an evergreen to a prior
BL-061 revision.

Usage::

    ovp-rollback-evergreen <slug> <version> [--pack PACK]
        [--canonical-path PATH] [--dry-run] [--json]

What it does:

1. Reads ``evergreen_revisions(pack, object_id=slug, version)`` to
   get the ``content_md`` snapshot.
2. Resolves the canonical_path — either from ``--canonical-path`` or
   from ``objects.canonical_path``.
3. Overwrites the file on disk with the snapshot.
4. Appends a new revision row with ``change_type='rollback'`` and a
   change_note referencing the source version.

The rollback is itself audited via BL-061 — the truth-store is
append-only; this command never deletes history, it just adds a
new revision that happens to carry old content.

Out of scope:

* Conflict detection (operator's responsibility to know if the file
  is being edited concurrently).
* Wikilink / Atlas reconciliation (those projections rebuild on the
  next ``ovp-knowledge-index``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..revisions_view import (
    list_evergreen_revisions,
    rollback_evergreen,
)
from ..runtime import resolve_vault_dir


def _resolve_pack(args_pack: str | None) -> str:
    """Resolve the truth pack name for the rollback.  Mirrors the
    rest of the codebase's default ('default_knowledge' when the
    operator doesn't pass --pack).  Lazy import so this CLI doesn't
    depend on truth_api at module load."""
    if args_pack:
        return args_pack
    try:
        from ..knowledge_index import _truth_pack_name
        return _truth_pack_name(None)
    except Exception:  # noqa: BLE001 — best-effort pack default
        return "default_knowledge"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Restore an evergreen to a prior BL-061 revision.  The "
            "rollback itself appends a new revision (change_type="
            "'rollback'), so history is never lost."
        ),
    )
    parser.add_argument("slug", help="Evergreen slug (object_id)")
    parser.add_argument(
        "version",
        type=int,
        nargs="?",
        help="Target revision version to restore.  Omit to list "
             "available versions and exit.",
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=None,
        help="Vault directory (default: $OVP_VAULT_DIR / cwd marker).",
    )
    parser.add_argument(
        "--pack",
        default=None,
        help="Truth pack name (default: resolved via knowledge_index).",
    )
    parser.add_argument(
        "--canonical-path",
        default=None,
        help="Override the canonical_path resolution; required when "
             "objects.canonical_path isn't available.",
    )
    parser.add_argument(
        "--changed-by",
        default="cli:rollback",
        help="Audit attribution for the new revision row.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the rollback target without writing anything.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    pack = _resolve_pack(args.pack)

    # No version → list mode (operator's first stop before deciding).
    if args.version is None:
        revisions = list_evergreen_revisions(
            vault_dir, pack=pack, object_id=args.slug,
        )
        payload = {
            "mode": "list",
            "pack": pack,
            "slug": args.slug,
            "revisions": [
                {
                    "version": r.version,
                    "change_type": r.change_type,
                    "changed_by": r.changed_by,
                    "derived_at": r.derived_at,
                    "change_note": r.change_note,
                    "content_chars": len(r.content_md),
                }
                for r in revisions
            ],
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"revisions for {pack}::{args.slug}: {len(revisions)}")
            for r in revisions:
                print(
                    f"  v{r.version:<3}  {r.derived_at:<24}  "
                    f"{r.change_type:<10}  {r.changed_by}"
                )
                if r.change_note:
                    print(f"           note: {r.change_note}")
        return 0 if revisions else 1

    if args.dry_run:
        from ..revisions_view import get_evergreen_revision
        target = get_evergreen_revision(
            vault_dir, pack=pack, object_id=args.slug, version=args.version,
        )
        if target is None:
            payload = {
                "mode": "dry_run",
                "status": "not_found",
                "pack": pack,
                "slug": args.slug,
                "version": args.version,
            }
        else:
            payload = {
                "mode": "dry_run",
                "status": "would_rollback",
                "pack": pack,
                "slug": args.slug,
                "version": args.version,
                "content_chars": len(target.content_md),
                "change_type": target.change_type,
                "derived_at": target.derived_at,
            }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                f"dry-run rollback: {pack}::{args.slug} v{args.version}  "
                f"status={payload['status']}"
            )
        return 0 if payload.get("status") != "not_found" else 1

    try:
        result = rollback_evergreen(
            vault_dir,
            pack=pack,
            object_id=args.slug,
            target_version=args.version,
            canonical_path=args.canonical_path,
            changed_by=args.changed_by,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"rolled back {pack}::{args.slug} → v{args.version}  "
            f"(new revision v{result['new_version']}, "
            f"canonical_path={result['canonical_path']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
