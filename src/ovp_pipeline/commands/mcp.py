"""``ovp-mcp`` — CLI entry for the Phase 37 MCP stdio server.

Three modes:

* ``ovp-mcp --vault-dir <path>`` — long-lived stdio server. Reads JSON-RPC
  requests on stdin, writes replies on stdout, one per line.
* ``ovp-mcp --vault-dir <path> --tools-list`` — one-shot listing of the
  registered tool descriptors as JSON. Useful for shell scripting and CI.
* ``ovp-mcp --vault-dir <path> --call NAME --json '{...}'`` — invoke a
  single tool with the supplied JSON arguments and print the JSON result.

The server class lives in :mod:`ovp_pipeline.mcp_server`; this module is a
thin argparse + dispatch wrapper.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..mcp_server import MCPServer
from ..runtime import resolve_vault_dir


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ovp-mcp",
        description="Phase 37 MCP stdio server for the OVP compiler primitives.",
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=None,
        help="Path to the vault. Defaults to the current working directory.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--tools-list",
        action="store_true",
        help="One-shot: print the registered tool descriptors as JSON and exit.",
    )
    mode.add_argument(
        "--call",
        metavar="NAME",
        default=None,
        help="One-shot: invoke a single tool by name. Pair with --json.",
    )
    parser.add_argument(
        "--json",
        metavar="JSON",
        default="{}",
        help="JSON object passed as the tool's arguments (used with --call).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    vault_dir = resolve_vault_dir(args.vault_dir)
    server = MCPServer(vault_dir)

    if args.tools_list:
        print(json.dumps({"tools": server.list_tools()}, ensure_ascii=False, indent=2))
        return 0

    if args.call:
        try:
            arguments = json.loads(args.json)
        except json.JSONDecodeError as exc:
            print(json.dumps({"error": f"Invalid --json: {exc}"}), file=sys.stderr)
            return 2
        if not isinstance(arguments, dict):
            print(json.dumps({"error": "--json must encode an object"}), file=sys.stderr)
            return 2
        try:
            result = server.call_tool(args.call, arguments)
        except KeyError:
            print(json.dumps({"error": f"Unknown tool: {args.call}"}), file=sys.stderr)
            return 2
        except (TypeError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    server.serve()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
