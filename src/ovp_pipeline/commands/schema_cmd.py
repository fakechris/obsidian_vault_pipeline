"""``ovp-schema`` CLI — inspect and validate the pack schema registry.

Usage::

    ovp-schema list                       # list all object kinds from active pack
    ovp-schema list --pack research-tech  # explicit pack
    ovp-schema validate                   # check Evergreen entity_type vs pack schema
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _resolve_pack(pack_name: str | None):
    from ..pack_resolution import resolve_pack

    return resolve_pack(pack_name or "research-tech")


def cmd_list(args: argparse.Namespace) -> None:
    pack = _resolve_pack(args.pack)
    specs = pack.object_kinds()
    print(f"Pack: {pack.name} v{pack.version}")
    print(f"{'Kind':<15} {'Display':<15} {'Canonical':<10} {'Layout':<15} Description")
    print("-" * 80)
    for s in specs:
        print(
            f"{s.kind:<15} {s.display_name:<15} "
            f"{'yes' if s.canonical else 'no':<10} "
            f"{s.reader_layout or '-':<15} {s.description}"
        )
    print(f"\nTotal: {len(specs)} kinds ({sum(1 for s in specs if s.canonical)} canonical)")


def cmd_validate(args: argparse.Namespace) -> None:
    pack = _resolve_pack(args.pack)
    valid_types = pack.valid_entity_types()
    vault_dir = Path(args.vault_dir).resolve()
    eg_dir = vault_dir / "10-Knowledge" / "Evergreen"

    if not eg_dir.is_dir():
        print(f"Evergreen directory not found: {eg_dir}", file=sys.stderr)
        sys.exit(1)

    total = 0
    missing = 0
    invalid = 0
    type_counts: dict[str, int] = {}

    for md in eg_dir.glob("*.md"):
        total += 1
        text = md.read_text(encoding="utf-8", errors="replace")
        if not text.startswith("---"):
            missing += 1
            continue

        import re

        m = re.search(r"^entity_type:\s*(.+)$", text, re.MULTILINE)
        if not m:
            missing += 1
            continue

        et = m.group(1).strip()
        type_counts[et] = type_counts.get(et, 0) + 1

        if et not in valid_types:
            invalid += 1
            if not args.quiet:
                print(f"  INVALID entity_type={et!r} in {md.name}")

    print(f"\nSchema validation: {total} Evergreens scanned")
    print(f"  With entity_type: {total - missing}")
    print(f"  Missing entity_type: {missing}")
    print(f"  Invalid entity_type: {invalid}")
    if type_counts:
        print("\nDistribution:")
        for k, v in sorted(type_counts.items(), key=lambda x: -x[1]):
            marker = " *" if k not in valid_types else ""
            print(f"  {k:<15} {v:>5}{marker}")

    if invalid > 0:
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ovp-schema", description="Pack schema registry")
    parser.add_argument("--pack", default=None, help="Pack name (default: research-tech)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List all object kinds")

    val = sub.add_parser("validate", help="Validate Evergreen entity_type against pack schema")
    val.add_argument("--vault-dir", default=".", help="Vault root directory")
    val.add_argument("--quiet", "-q", action="store_true", help="Suppress per-file output")

    args = parser.parse_args(argv)

    if args.command == "list":
        cmd_list(args)
    elif args.command == "validate":
        cmd_validate(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
