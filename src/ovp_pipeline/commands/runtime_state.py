from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..runtime import resolve_vault_dir
from ..runtime_state import build_runtime_state, write_runtime_state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ovp-runtime-state",
        description="Read the operational runtime graph from projection repair, pipeline, and reuse logs.",
    )
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault root (default: cwd)")
    parser.add_argument("--limit", type=int, default=20, help="Recent events to include per stream")
    parser.add_argument("--write", action="store_true", help="Write current.json and current.md under 60-Logs/runtime-state/")
    parser.add_argument("--json", action="store_true", help="Print the full runtime state JSON to stdout")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    state = build_runtime_state(vault_dir, recent_limit=args.limit)
    paths = write_runtime_state(vault_dir, state) if args.write else None

    if args.json:
        payload = dict(state)
        if paths:
            payload["paths"] = {
                "json": str(paths.json_path),
                "markdown": str(paths.markdown_path),
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    metrics = state["metrics"]
    print("=" * 60)
    print("OVP RUNTIME STATE")
    print("=" * 60)
    print(f"Status:                 {state['status']}")
    print(f"Open repair markers:    {metrics['open_projection_repair_markers']}")
    print(f"Expired repair leases:  {metrics['expired_projection_repair_leases']}")
    print(f"Pipeline events:        {metrics['pipeline_events']}")
    print(f"Reuse events:           {metrics['reuse_events']}")
    print(f"Reuse surfaces:         {metrics['reuse_surfaces']}")
    if paths:
        print(f"JSON:                   {paths.json_path}")
        print(f"Markdown:               {paths.markdown_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
