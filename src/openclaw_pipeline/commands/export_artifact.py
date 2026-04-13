from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from ..packs.loader import PRIMARY_PACK_NAME, load_pack
from ..runtime import resolve_vault_dir
from ..wiki_views.runtime import build_view


TARGET_TO_VIEW = {
    "object-page": "object/page",
    "topic-overview": "overview/topic",
    "event-dossier": "event/dossier",
    "contradictions": "truth/contradictions",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export pack-backed compiled artifacts to an explicit output path."
    )
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument("--pack", default=PRIMARY_PACK_NAME, help=f"Pack name (default: {PRIMARY_PACK_NAME})")
    parser.add_argument("--target", required=True, choices=sorted(TARGET_TO_VIEW), help="Export target")
    parser.add_argument("--object-id", help="Required for object-page exports")
    parser.add_argument("--output-path", type=Path, required=True, help="Where to write the exported artifact")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    pack = load_pack(args.pack)
    view_name = TARGET_TO_VIEW[args.target]
    try:
        view = pack.wiki_view(view_name)
    except Exception as exc:
        parser.error(f"failed to resolve view '{view_name}' for pack '{pack.name}': {exc}")

    if args.target == "object-page" and not args.object_id:
        parser.error("the --object-id argument is required for object-page exports")

    try:
        source_path = build_view(vault_dir, view, object_id=args.object_id)
    except Exception as exc:
        parser.error(
            f"failed to build export target '{args.target}' for view '{view_name}' "
            f"and object_id={args.object_id!r}: {exc}"
        )
    output_path = args.output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, output_path)

    print(
        json.dumps(
            {
                "target": args.target,
                "pack": pack.name,
                "source_path": str(source_path),
                "output_path": str(output_path),
            },
            ensure_ascii=False,
        )
    )
    return 0
