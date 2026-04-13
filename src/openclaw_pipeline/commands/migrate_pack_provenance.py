from __future__ import annotations

import argparse
import json

from ..migrate_pack_provenance import migrate_pack_provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rewrite historical log provenance from a compatibility pack to its primary base."
    )
    parser.add_argument("--vault-dir", default=".", help="Vault directory to update")
    parser.add_argument(
        "--from-pack",
        default="default-knowledge",
        help="Source pack name to rewrite from",
    )
    parser.add_argument(
        "--to-pack",
        default=None,
        help="Explicit target pack name. Defaults to the source pack's compatibility base.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply the rewrite in-place. Without this flag the command is a dry run.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON output.",
    )
    args = parser.parse_args(argv)

    result = migrate_pack_provenance(
        args.vault_dir,
        from_pack=args.from_pack,
        to_pack=args.to_pack,
        write=args.write,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0

    mode = "write" if args.write else "dry-run"
    print(
        f"[{mode}] migrated {result['files_changed']} files "
        f"({result['replacements']} replacements): "
        f"{result['from_pack']} -> {result['to_pack']}"
    )
    for path in result["changed_paths"]:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
