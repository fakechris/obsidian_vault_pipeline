from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..knowledge_index import rebuild_compiled_summaries
from ..runtime import VaultLayout, resolve_vault_dir


def _object_ids_from_queue(layout: VaultLayout, queue_name: str) -> list[str]:
    queue_dir = layout.review_queue_dir / queue_name
    if not queue_dir.exists():
        return []

    object_ids: list[str] = []
    for artifact in sorted(queue_dir.rglob("*.json")):
        try:
            payload = json.loads(artifact.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            print(f"Skipping malformed queue artifact {artifact}: {exc}", file=sys.stderr)
            continue
        object_id = payload.get("object_id")
        if object_id:
            object_ids.append(str(object_id))
    return list(dict.fromkeys(object_ids))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild compiled summaries from truth-store claims and relations.")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument("--object-id", action="append", help="Specific object ID to rebuild")
    parser.add_argument("--from-queue", help="Load object IDs from a review queue directory")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    layout = VaultLayout.from_vault(vault_dir)

    object_ids = list(args.object_id or [])
    if args.from_queue:
        object_ids.extend(_object_ids_from_queue(layout, args.from_queue))
    object_ids = list(dict.fromkeys(object_ids))

    payload = rebuild_compiled_summaries(vault_dir, object_ids=object_ids or None)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"objects rebuilt: {payload['objects_rebuilt']}")
        for object_id in payload["object_ids"]:
            print(f"- {object_id}")
    return 0
