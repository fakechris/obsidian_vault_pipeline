from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..runtime import resolve_vault_dir
from ..truth_api import (
    get_object_detail,
    get_topic_neighborhood,
    list_contradictions,
    list_graph_clusters,
    list_objects,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read truth-store data from knowledge.db")
    subparsers = parser.add_subparsers(dest="command", required=True)

    objects_parser = subparsers.add_parser("objects", help="List objects")
    objects_parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    objects_parser.add_argument("--limit", type=int, default=100)
    objects_parser.add_argument("--offset", type=int, default=0)
    objects_parser.add_argument("--query", default=None, help="Case-insensitive object search")

    object_parser = subparsers.add_parser("object", help="Fetch object detail")
    object_parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    object_parser.add_argument("--id", required=True, help="Object id")

    contradictions_parser = subparsers.add_parser("contradictions", help="List contradictions")
    contradictions_parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    contradictions_parser.add_argument("--limit", type=int, default=100)
    contradictions_parser.add_argument("--status", default=None)
    contradictions_parser.add_argument("--query", default=None, help="Case-insensitive contradiction search")

    clusters_parser = subparsers.add_parser("clusters", help="List graph clusters")
    clusters_parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    clusters_parser.add_argument("--limit", type=int, default=100)
    clusters_parser.add_argument("--query", default=None, help="Case-insensitive cluster search")

    neighborhood_parser = subparsers.add_parser("neighborhood", help="Fetch topic neighborhood")
    neighborhood_parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    neighborhood_parser.add_argument("--id", required=True, help="Center object id")
    neighborhood_parser.add_argument("--depth", type=int, default=1)

    args = parser.parse_args(argv)
    vault_dir = resolve_vault_dir(getattr(args, "vault_dir", None))

    if args.command == "neighborhood" and args.depth != 1:
        parser.error("Only depth=1 is currently supported")

    try:
        if args.command == "objects":
            payload = {
                "items": list_objects(vault_dir, limit=args.limit, offset=args.offset, query=args.query)
            }
        elif args.command == "object":
            payload = get_object_detail(vault_dir, args.id)
        elif args.command == "contradictions":
            payload = {
                "items": list_contradictions(
                    vault_dir,
                    limit=args.limit,
                    status=args.status,
                    query=args.query,
                )
            }
        elif args.command == "clusters":
            payload = {
                "items": list_graph_clusters(
                    vault_dir,
                    limit=args.limit,
                    query=args.query,
                )
            }
        elif args.command == "neighborhood":
            payload = get_topic_neighborhood(vault_dir, args.id, depth=args.depth)
        else:  # pragma: no cover - argparse enforces commands
            parser.error(f"unknown command: {args.command}")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc

    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
