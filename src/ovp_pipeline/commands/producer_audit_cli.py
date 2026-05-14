"""``ovp-producer-audit`` CLI (M24.2).

Reads ``knowledge.db`` and reports which hot-path producers have
silently stopped emitting their declared audit rows.  Read-only —
no writes, no producer calls.  Hooks into the same
``commands/`` dispatch pattern the rest of the M24 surface uses
(``ovp-ops-state``, ``ovp-lifecycle-show``).

Two modes:

* ``--report`` (default): pretty-print a human-readable table
  grouped by producer with severity flags.
* ``--json``: emit the audit report as structured JSON, suitable
  for a future ``/ops/digest-health`` panel or for ad-hoc
  scripting.

Exit codes:

* ``0`` — every must-emit row appeared in the window.
* ``2`` — at least one must-emit row is missing OR unknown
  event_types observed (drift).  CI / cron can treat ``2`` as a
  hard signal that an emit site needs investigation.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from ..producer_audit import audit_against_log
from ..runtime import resolve_vault_dir


KNOWLEDGE_DB_REL = "60-Logs/knowledge.db"


def _db_path(vault_dir: Path) -> Path:
    return vault_dir / KNOWLEDGE_DB_REL


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "M24.2: audit hot-path producers against audit_events. "
            "Reports event_types each producer is expected to emit "
            "but didn't in the lookback window."
        )
    )
    parser.add_argument(
        "--vault-dir", type=Path, default=None, help="Vault directory"
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=7,
        help="Lookback window in days (default: 7)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON output"
    )
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    db_path = _db_path(vault_dir)
    if not db_path.is_file():
        print(
            f"producer-audit: no knowledge.db at {db_path} — run "
            "`ovp-knowledge-index` first.",
            file=sys.stderr,
        )
        return 1

    with sqlite3.connect(str(db_path)) as conn:
        report = audit_against_log(conn, window_days=args.window_days)

    missing = [f for f in report.findings if f.severity == "missing"]
    drift = bool(report.unknown_event_types)
    exit_code = 2 if (missing or drift) else 0

    if args.json:
        payload = {
            "vault_dir": str(vault_dir),
            "window_start": report.window_start,
            "window_end": report.window_end,
            "findings": [asdict(f) for f in report.findings],
            "unknown_event_types": list(report.unknown_event_types),
            "missing_count": len(missing),
            "drift_count": len(report.unknown_event_types),
            "exit_code": exit_code,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return exit_code

    print(f"producer-audit  window: {report.window_start} → {report.window_end}")
    print()

    # Group by producer for readability.
    by_producer: dict[str, list] = {}
    for finding in report.findings:
        by_producer.setdefault(finding.producer, []).append(finding)

    for producer in sorted(by_producer):
        findings = by_producer[producer]
        worst = "ok"
        if any(f.severity == "missing" for f in findings):
            worst = "MISSING"
        print(f"## {producer}  [{worst}]")
        for f in findings:
            mark = "✓" if f.severity == "ok" else "✗"
            last = f.last_seen or "(never)"
            print(
                f"  {mark} {f.event_type:<35} "
                f"count={f.count_in_window:<5} last={last}"
            )
        print()

    if report.unknown_event_types:
        print("## drift (event_types observed but not declared)")
        for et in report.unknown_event_types:
            print(f"  ? {et}")
        print()
        print(
            "These rows are either intentional forensic-only events "
            "(register them with user_visible=False) or a producer "
            "that started emitting something we didn't promise — "
            "audit the source."
        )
        print()

    if missing:
        print(
            f"FAIL: {len(missing)} must-emit row(s) missing in the "
            f"last {args.window_days} day(s).  Exit code 2."
        )
    elif drift:
        print(
            f"FAIL: {len(report.unknown_event_types)} undeclared "
            "event_type(s) observed.  Exit code 2."
        )
    else:
        print("OK: every hot-path producer emitted its declared rows.")

    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
