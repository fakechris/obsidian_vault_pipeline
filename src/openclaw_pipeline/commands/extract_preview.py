from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..extraction.artifacts import load_run_results
from ..packs.loader import DEFAULT_PACK_NAME, PRIMARY_PACK_NAME, load_pack
from ..runtime import VaultLayout, resolve_vault_dir


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("limit must be >= 0")
    return parsed


def _normalize_source_path(path: str | Path, *, vault_dir: Path) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = vault_dir / candidate
    return str(candidate.resolve(strict=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show a preview of the latest extraction artifacts for a profile.")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument(
        "--pack",
        default=DEFAULT_PACK_NAME,
        help=f"Pack name (default compatibility pack: {DEFAULT_PACK_NAME}; primary pack: {PRIMARY_PACK_NAME})",
    )
    parser.add_argument("--profile", required=True, help="Extraction profile name")
    parser.add_argument("--source", type=Path, default=None, help="Optional source path to filter by")
    parser.add_argument("--limit", type=_non_negative_int, default=5, help="Maximum number of records to show")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    pack = load_pack(args.pack)
    pack.extraction_profile(args.profile)

    runs = load_run_results(VaultLayout.from_vault(vault_dir), pack_name=pack.name, profile_name=args.profile)
    if args.source is not None:
        source_filter = _normalize_source_path(args.source, vault_dir=vault_dir)
        runs = [run for run in runs if _normalize_source_path(run.source_path, vault_dir=vault_dir) == source_filter]

    latest = runs[-1] if runs else None
    payload = {
        "pack": pack.name,
        "profile_name": args.profile,
        "source_path": latest.source_path if latest else "",
        "run_count": len(runs),
        "record_count": len(latest.records) if latest else 0,
        "records": [record.to_dict() for record in (latest.records[: args.limit] if latest else [])],
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0
