from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..extraction.artifacts import iter_run_results
from ..packs.loader import DEFAULT_PACK_NAME, PRIMARY_PACK_NAME, load_pack
from ..runtime import VaultLayout, resolve_vault_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize extraction run artifacts for a pack.")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument(
        "--pack",
        default=DEFAULT_PACK_NAME,
        help=f"Pack name (default compatibility pack: {DEFAULT_PACK_NAME}; primary pack: {PRIMARY_PACK_NAME})",
    )
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    pack = load_pack(args.pack)
    layout = VaultLayout.from_vault(vault_dir)

    profiles: dict[str, dict[str, object]] = {}
    total_runs = 0
    for run in iter_run_results(layout, pack_name=pack.name):
        total_runs += 1
        profile_bucket = profiles.setdefault(
            run.profile_name,
            {
                "run_count": 0,
                "record_count": 0,
                "relation_count": 0,
                "latest_source_path": "",
            },
        )
        profile_bucket["run_count"] = int(profile_bucket["run_count"]) + 1
        profile_bucket["record_count"] = int(profile_bucket["record_count"]) + len(run.records)
        profile_bucket["relation_count"] = int(profile_bucket["relation_count"]) + len(run.relations)
        profile_bucket["latest_source_path"] = run.source_path

    print(
        json.dumps(
            {
                "pack": pack.name,
                "total_runs": total_runs,
                "profiles": profiles,
            },
            ensure_ascii=False,
        )
    )
    return 0
