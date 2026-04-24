"""``ovp-note-type-normalize`` — collapse legacy ``note_type`` values into the
canonical 8-type set defined in :mod:`ovp_pipeline.note_type_normalize`.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from ..note_type_normalize import (
    apply_normalization,
    load_mapping,
    plan_normalization,
)
from ..runtime import resolve_vault_dir


def _print_report_summary(report, *, verbose: bool) -> None:
    counts = Counter((c.old_value, c.new_value) for c in report.changed)
    print(f"changed:  {len(report.changed)}")
    print(f"skipped:  {len(report.skipped)} (already canonical)")
    if report.errors:
        print(f"errors:   {len(report.errors)}")

    if counts:
        print("\nplanned mappings (old → new, file count):")
        for (old, new), count in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {count:5d}  {old}  →  {new}")

    if verbose and report.changed:
        print("\nfiles to change:")
        for change in report.changed[:50]:
            print(f"  [{change.old_value} → {change.new_value}] {change.path}")
        if len(report.changed) > 50:
            print(f"  ... and {len(report.changed) - 50} more")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ovp-note-type-normalize",
        description=(
            "Rewrite frontmatter `type:` values to one of the 8 canonical "
            "note_type values. Original values are preserved as "
            "`original_note_type:` so the migration is invertible."
        ),
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=None,
        help="Vault root (defaults to current directory).",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=None,
        help="Path to a YAML mapping file (defaults to bundled "
        "data/note_type_normalization.yaml).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan changes without writing.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="List the first 50 files that would change.",
    )

    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    mapping = load_mapping(args.mapping)

    if args.dry_run:
        report = plan_normalization(vault_dir, mapping)
        print(f"[dry-run] vault: {vault_dir}")
    else:
        report = apply_normalization(vault_dir, mapping, dry_run=False)
        print(f"[applied] vault: {vault_dir}")

    _print_report_summary(report, verbose=args.verbose)

    if report.errors:
        print("\nerrors:", file=sys.stderr)
        for path, msg in report.errors[:20]:
            print(f"  {path}: {msg}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
