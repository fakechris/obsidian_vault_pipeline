"""``ovp-clean-processing-backups`` — remove verified orphan processing backups."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..processing_backups import cleanup_orphan_processing_backups
from ..runtime import VaultLayout


def _check_to_payload(check) -> dict:
    return {
        "backup_path": str(check.backup_path),
        "processed_path": str(check.processed_path) if check.processed_path else None,
        "ok": check.ok,
        "reason": check.reason,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ovp-clean-processing-backups",
        description="Clean orphan .md.backup files in 50-Inbox/02-Processing after verifying archived source coverage.",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd(), help="Vault root directory")
    parser.add_argument("--apply", action="store_true", help="Delete verified backups")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    layout = VaultLayout.from_vault(args.vault_dir)
    checks = cleanup_orphan_processing_backups(layout, apply=args.apply)
    payload = {
        "mode": "cleanup_processing_backups",
        "dry_run": not args.apply,
        "total": len(checks),
        "verified": sum(1 for check in checks if check.ok),
        "skipped": sum(1 for check in checks if not check.ok),
        "deleted": sum(1 for check in checks if check.ok) if args.apply else 0,
        "items": [_check_to_payload(check) for check in checks],
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        action = "deleted" if args.apply else "would delete"
        print(
            f"processing backups: total={payload['total']} "
            f"verified={payload['verified']} skipped={payload['skipped']} "
            f"{action}={payload['deleted'] if args.apply else payload['verified']}"
        )
        for check in checks:
            status = "OK" if check.ok else "SKIP"
            processed = f" -> {check.processed_path}" if check.processed_path else ""
            print(f"{status} {check.reason}: {check.backup_path}{processed}")

    return 0 if payload["skipped"] == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
