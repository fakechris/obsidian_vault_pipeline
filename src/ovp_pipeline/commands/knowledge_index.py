from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..knowledge_index import (
    dispatch_knowledge_tool,
    get_knowledge_page,
    knowledge_index_stats,
    knowledge_tools_json,
    query_knowledge_index,
    recent_audit_events,
    rebuild_knowledge_index,
    search_knowledge_index,
    serve_knowledge_index,
)
from ..packs.loader import DEFAULT_WORKFLOW_PACK_NAME
from ..runtime import resolve_vault_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild the derived SQLite knowledge index")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument(
        "--pack",
        default=DEFAULT_WORKFLOW_PACK_NAME,
        help="Pack name used to resolve the truth projection builder",
    )
    parser.add_argument("--search", help="Run keyword search against the knowledge index")
    parser.add_argument("--query", help="Run a read-only semantic-style query against chunk embeddings")
    parser.add_argument("--get", help="Fetch a canonical page payload by slug")
    parser.add_argument("--stats", action="store_true", help="Show table counts and DB path")
    parser.add_argument("--audit-recent", type=int, help="Show the most recent audit events")
    parser.add_argument("--source-log", help="Filter audit events by source log")
    parser.add_argument("--tools-json", action="store_true", help="Emit tool discovery JSON")
    parser.add_argument("--serve", action="store_true", help="Serve read-only knowledge tools over stdio JSONL")
    parser.add_argument("--limit", type=int, default=5, help="Maximum number of query results")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    if args.tools_json:
        print(json.dumps(knowledge_tools_json(), ensure_ascii=False, indent=2))
        return 0

    if args.serve:
        serve_knowledge_index(vault_dir, sys.stdin, sys.stdout)
        return 0

    if args.search:
        payload = {
            "vault_dir": str(vault_dir),
            "search": args.search,
            "limit": args.limit,
            "results": search_knowledge_index(vault_dir, args.search, limit=args.limit),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"knowledge search: {args.search}")
            for row in payload["results"]:
                print(f"- {row['slug']} ({row['title']}) score={row['score']:.4f}")
        return 0

    if args.query:
        payload = {
            "vault_dir": str(vault_dir),
            "query": args.query,
            "limit": args.limit,
            "results": query_knowledge_index(vault_dir, args.query, limit=args.limit),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"knowledge query: {args.query}")
            for row in payload["results"]:
                print(f"- {row['slug']} [{row['section_title']}] score={row['score']:.4f}")
        return 0

    if args.get:
        payload = {
            "vault_dir": str(vault_dir),
            "slug": args.get,
            "page": get_knowledge_page(vault_dir, args.get),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            page = payload["page"]
            if page is None:
                print(f"knowledge page not found: {args.get}")
            else:
                print(f"# {page['title']}")
                print(page["body"])
        return 0

    if args.stats:
        payload = {
            "vault_dir": str(vault_dir),
            "stats": knowledge_index_stats(vault_dir),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            stats = payload["stats"]
            print("knowledge stats")
            for key in ("pages", "links", "raw_records", "timeline_events", "audit_events", "embedding_chunks"):
                print(f"{key}: {stats[key]}")
            print(f"db path: {stats['db_path']}")
        return 0

    if args.audit_recent is not None:
        payload = {
            "vault_dir": str(vault_dir),
            "limit": args.audit_recent,
            "source_log": args.source_log,
            "events": recent_audit_events(vault_dir, limit=args.audit_recent, source_log=args.source_log),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("recent audit events")
            for row in payload["events"]:
                print(f"- {row['timestamp']} {row['source_log']} {row['event_type']} {row['slug']}")
        return 0

    payload = rebuild_knowledge_index(vault_dir, pack_name=args.pack)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("knowledge index rebuilt")
        print(f"pages indexed: {payload['pages_indexed']}")
        print(f"links indexed: {payload['links_indexed']}")
        print(f"db path: {payload['db_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
