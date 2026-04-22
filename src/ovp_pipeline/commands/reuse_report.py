"""
ovp-reuse — read-only reports over the Phase 32 ``reuse_events`` table.

Subcommands:
  weekly  Aggregate trusted_reuse_event rows by ISO-week x pack x surface,
          plus a "never reused after 30 days" list of canonical objects that
          have no events 30 days after their canonical_path mtime.

Reads ``60-Logs/knowledge.db``. Rebuilds the index first if it is missing or
stale, since reuse_events is derived from ``60-Logs/reuse-events.jsonl``.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from ..knowledge_index import ensure_knowledge_db_current
from ..packs.loader import DEFAULT_WORKFLOW_PACK_NAME
from ..runtime import resolve_vault_dir


_NEVER_REUSED_DAYS = 30


def _iso_week(ts_text: str) -> str:
    if not ts_text:
        return ""
    try:
        ts = datetime.strptime(ts_text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return ""
    iso_year, iso_week, _ = ts.isocalendar()
    return f"{iso_year:04d}-W{iso_week:02d}"


def _weekly_aggregate(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT ts, pack, surface, trusted
            FROM reuse_events
            ORDER BY ts
            """
        ).fetchall()

    buckets: dict[tuple[str, str, str], dict[str, int]] = {}
    for ts, pack, surface, trusted in rows:
        week = _iso_week(str(ts))
        if not week:
            continue
        key = (week, str(pack), str(surface))
        bucket = buckets.setdefault(key, {"events": 0, "trusted": 0})
        bucket["events"] += 1
        bucket["trusted"] += int(bool(trusted))

    return [
        {
            "iso_week": week,
            "pack": pack,
            "surface": surface,
            "events": values["events"],
            "trusted_events": values["trusted"],
        }
        for (week, pack, surface), values in sorted(buckets.items())
    ]


def _never_reused(db_path: Path, *, pack: str) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=_NEVER_REUSED_DAYS)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT objects.object_id, objects.title, objects.canonical_path
            FROM objects
            LEFT JOIN reuse_events
                   ON reuse_events.pack = objects.pack
                  AND reuse_events.object_id = objects.object_id
            WHERE objects.pack = ?
            GROUP BY objects.object_id
            HAVING COUNT(reuse_events.event_id) = 0
            ORDER BY objects.object_id
            """,
            (pack,),
        ).fetchall()

    stale: list[dict[str, Any]] = []
    for object_id, title, canonical_path in rows:
        path = Path(str(canonical_path))
        if path.exists():
            mtime_dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime_dt > cutoff:
                continue
            mtime_text = mtime_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            mtime_text = ""
        stale.append(
            {
                "object_id": str(object_id),
                "title": str(title),
                "canonical_path": str(canonical_path),
                "canonical_path_mtime": mtime_text,
            }
        )
    return stale


def build_reuse_report_payload(
    vault_dir: Path,
    *,
    pack: str,
) -> dict[str, Any]:
    """Shared payload used by both ``ovp-reuse weekly`` and the UI ``/reuse`` route."""
    db_path = ensure_knowledge_db_current(vault_dir)
    return {
        "vault_dir": str(vault_dir),
        "pack": pack,
        "weekly": _weekly_aggregate(db_path),
        "never_reused_after_30_days": _never_reused(db_path, pack=pack),
        "never_reused_window_days": _NEVER_REUSED_DAYS,
    }


def _run_weekly(args: argparse.Namespace) -> int:
    vault_dir = resolve_vault_dir(args.vault_dir)
    payload = build_reuse_report_payload(vault_dir, pack=args.pack)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"reuse weekly report ({vault_dir})")
    if not payload["weekly"]:
        print("(no reuse events recorded)")
    for row in payload["weekly"]:
        print(
            f"- {row['iso_week']} pack={row['pack']} surface={row['surface']} "
            f"events={row['events']} trusted={row['trusted_events']}"
        )
    if payload["never_reused_after_30_days"]:
        print("")
        print(f"never reused after {_NEVER_REUSED_DAYS} days:")
        for row in payload["never_reused_after_30_days"]:
            print(f"- {row['object_id']} ({row['title']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 32 reuse-event reports")
    sub = parser.add_subparsers(dest="command", required=True)

    weekly = sub.add_parser("weekly", help="ISO-week x pack x surface aggregate")
    weekly.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    weekly.add_argument(
        "--pack",
        default=DEFAULT_WORKFLOW_PACK_NAME,
        help="Pack scope for the never-reused list",
    )
    weekly.add_argument("--json", action="store_true", help="Emit JSON output")
    weekly.set_defaults(func=_run_weekly)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
