from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..extraction.artifacts import load_run_results
from ..packs.loader import load_pack
from ..runtime import VaultLayout, resolve_vault_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show a preview of the latest extraction artifacts for a profile.")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument("--pack", default="default-knowledge", help="Pack name")
    parser.add_argument("--profile", required=True, help="Extraction profile name")
    parser.add_argument("--source", type=Path, default=None, help="Optional source path to filter by")
    parser.add_argument("--limit", type=int, default=5, help="Maximum number of records to show")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    pack = load_pack(args.pack)
    pack.extraction_profile(args.profile)

    runs = load_run_results(VaultLayout.from_vault(vault_dir), pack_name=pack.name, profile_name=args.profile)
    if args.source is not None:
        source_filter = str(args.source)
        runs = [run for run in runs if run.source_path == source_filter]

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
