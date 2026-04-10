from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..operations.runtime import run_operation_profile
from ..packs.loader import load_pack
from ..runtime import resolve_vault_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a pack-defined knowledge operation profile.")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument("--pack", default="default-knowledge", help="Pack name")
    parser.add_argument("--profile", required=True, help="Operation profile name")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    pack = load_pack(args.pack)
    profile = pack.operation_profile(args.profile)
    written = run_operation_profile(vault_dir, profile)
    print(json.dumps({"written": [str(path) for path in written]}, ensure_ascii=False))
    return 0
