from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..packs.loader import load_pack
from ..runtime import resolve_vault_dir
from ..wiki_views.runtime import build_view


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a compiled wiki view from a pack-defined view spec.")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument("--pack", default="default-knowledge", help="Pack name")
    parser.add_argument("--view", required=True, help="Wiki view name")
    parser.add_argument("--object-id", help="Object ID for object-specific materializers")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    pack = load_pack(args.pack)
    view = pack.wiki_view(args.view)
    output_path = build_view(vault_dir, view, object_id=args.object_id)
    print(json.dumps({"output_path": str(output_path)}, ensure_ascii=False))
    return 0
