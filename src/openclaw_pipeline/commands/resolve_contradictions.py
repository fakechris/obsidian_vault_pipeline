from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..knowledge_index import contradiction_object_ids, rebuild_compiled_summaries, resolve_contradictions
from ..runtime import VaultLayout, resolve_vault_dir


def _load_contradiction_ids_from_queue(layout: VaultLayout, queue_name: str) -> tuple[list[str], list[Path]]:
    queue_dir = layout.review_queue_dir / queue_name
    if not queue_dir.exists():
        return [], []

    contradiction_ids: list[str] = []
    queue_files: list[Path] = []
    for artifact in sorted(queue_dir.rglob("*.json")):
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        contradiction_id = payload.get("contradiction_id")
        if contradiction_id:
            contradiction_ids.append(str(contradiction_id))
            queue_files.append(artifact)
    return list(dict.fromkeys(contradiction_ids)), queue_files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve contradiction records in the truth store.")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument("--contradiction-id", action="append", help="Specific contradiction ID to resolve")
    parser.add_argument("--from-queue", help="Load contradiction IDs from a review queue directory")
    parser.add_argument(
        "--status",
        required=True,
        choices=["resolved_keep_positive", "resolved_keep_negative", "dismissed", "needs_human"],
        help="Resolution status to apply",
    )
    parser.add_argument("--note", default="", help="Optional resolution note")
    parser.add_argument("--rebuild-summaries", action="store_true", help="Rebuild compiled summaries for affected objects")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    layout = VaultLayout.from_vault(vault_dir)

    contradiction_ids = list(args.contradiction_id or [])
    queue_files: list[Path] = []
    if args.from_queue:
        queue_ids, queue_files = _load_contradiction_ids_from_queue(layout, args.from_queue)
        contradiction_ids.extend(queue_ids)
    contradiction_ids = list(dict.fromkeys(contradiction_ids))
    affected_object_ids = contradiction_object_ids(vault_dir, contradiction_ids) if args.rebuild_summaries else []

    payload = resolve_contradictions(vault_dir, contradiction_ids, status=args.status, note=args.note)
    if payload["resolved_count"] and affected_object_ids:
        rebuild_payload = rebuild_compiled_summaries(vault_dir, object_ids=affected_object_ids)
        payload["rebuilt_summary_count"] = rebuild_payload["objects_rebuilt"]
        payload["rebuilt_object_ids"] = rebuild_payload["object_ids"]
    else:
        payload["rebuilt_summary_count"] = 0
        payload["rebuilt_object_ids"] = []

    cleared_queue_files: list[str] = []
    if payload["resolved_count"] and queue_files:
        for queue_file in queue_files:
            if queue_file.exists():
                queue_file.unlink()
                cleared_queue_files.append(str(queue_file))

    payload["cleared_queue_files"] = cleared_queue_files
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"resolved contradictions: {payload['resolved_count']}")
        for contradiction_id in payload["contradiction_ids"]:
            print(f"- {contradiction_id}")
    return 0
