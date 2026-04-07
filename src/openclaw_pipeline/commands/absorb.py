from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..auto_evergreen_extractor import run_absorb_workflow
from ..runtime import resolve_vault_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Absorb interpreted notes into the knowledge layer")
    parser.add_argument("--file", type=Path, help="Absorb one deep-dive file")
    parser.add_argument("--dir", type=Path, help="Absorb a directory of deep-dive files")
    parser.add_argument("--recent", type=int, help="Absorb recent N days of deep-dives")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument("--dry-run", action="store_true", help="Show absorb scope without mutating state")
    parser.add_argument("--auto-promote", action="store_true", help="Allow automatic promotion when threshold is met")
    parser.add_argument("--promote-threshold", type=int, default=3, help="Promotion threshold for auto-promote")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    payload = {
        "mode": "absorb",
        "vault_dir": str(vault_dir),
        "file": str(args.file) if args.file else None,
        "dir": str(args.dir) if args.dir else None,
        "recent": args.recent,
        "dry_run": args.dry_run,
        "auto_promote": args.auto_promote,
        "promote_threshold": args.promote_threshold,
    }

    if args.dry_run:
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("absorb dry-run")
        return 0

    workflow_payload = run_absorb_workflow(
        vault_dir,
        file_path=args.file,
        directory=args.dir,
        recent=args.recent,
        dry_run=False,
        auto_promote=args.auto_promote,
        promote_threshold=args.promote_threshold,
    )

    if args.json:
        print(json.dumps(workflow_payload, ensure_ascii=False, indent=2))
    else:
        summary = workflow_payload["summary"]
        print("absorb complete")
        print(f"files processed: {summary['files_processed']}")
        print(f"concepts extracted: {summary['concepts_extracted']}")
        print(f"candidates added: {summary['candidates_added']}")
        if args.auto_promote:
            print(f"concepts promoted: {summary['concepts_promoted']}")
            print(f"files created: {summary['concepts_created']}")
        print(f"concepts skipped: {summary['concepts_skipped']}")
        if summary["errors"]:
            print(f"errors: {summary['errors']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
