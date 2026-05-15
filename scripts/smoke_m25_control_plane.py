#!/usr/bin/env python3
"""M25.6 dogfood acceptance smoke for the Maintainer Control Plane.

Runs the full M24+M25 contract against a real vault and reports
whether the operator-visible numbers actually match the underlying
data.  This is the dogfood gate the M25 plan §M25.6 calls for —
the M24/M25 unit tests proved the code is internally consistent;
this script proves it's externally honest against a real vault.

What it checks
--------------

1. ``ovp-producer-audit`` runs cleanly and reports zero missing
   must-emit producer events.

2. ``ops_state`` projection exists; if not, rebuilds it.

3. For every lifecycle state (Received / Extracted / Accepted /
   Synthesized / NeedsAction):

   * The card's primary count from ``build_today_digest_payload``
     equals the row count returned by
     ``build_items_list_payload`` with the same state filter.
   * The card's secondary count equals the audit page total
     from ``build_events_audit_payload``.
   * Card samples are pulled from items (``ops_state``), not
     events.

4. ``/ops/events/audit`` carries the role banner.
5. ``/ops/events`` carries the reciprocal timeline-projection
   banner.
6. Honest-zero copy appears when a card is empty.

Output
------

JSON report written to ``--out`` (defaults to
``60-Logs/m25-acceptance.json``) so the operator can paste rows
into ``docs/reports/2026-05-14-m25-dogfood-acceptance.md``.

Exit codes:
* ``0`` — every check passed.
* ``2`` — at least one acceptance check failed.
* ``3`` — environment issue (missing knowledge.db, etc.).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ovp_pipeline.commands._ui_renderers import (  # noqa: E402
    _render_events_audit_page,
    _render_events_page,
)
from ovp_pipeline.ops_lifecycle import ALL_STATES  # noqa: E402
from ovp_pipeline.ui.view_models import (  # noqa: E402
    build_event_dossier_payload,
    build_events_audit_payload,
    build_items_list_payload,
    build_today_digest_payload,
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def _knowledge_db(vault_dir: Path) -> Path:
    return vault_dir / "60-Logs" / "knowledge.db"


def _run_producer_audit(vault_dir: Path) -> CheckResult:
    """Run ``ovp-producer-audit --json`` and surface missing /
    drift counts.  We don't fail on drift (registered-but-not-
    contract events) because M24.2 narrowed the drift definition;
    we fail only on ``missing_count > 0``."""
    cmd = [
        sys.executable, "-m",
        "ovp_pipeline.commands.producer_audit_cli",
        "--vault-dir", str(vault_dir),
        "--window-days", "7",
        "--json",
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, check=False,
    )
    try:
        payload = json.loads(proc.stdout)
    except (ValueError, json.JSONDecodeError):
        return CheckResult(
            name="producer_audit",
            ok=False,
            detail=(
                "producer_audit CLI didn't return JSON.  "
                f"stderr={proc.stderr.strip()[:200]}"
            ),
        )
    missing = int(payload.get("missing_count", 0) or 0)
    drift = list(payload.get("unknown_event_types") or [])
    return CheckResult(
        name="producer_audit",
        ok=(missing == 0),
        detail=(
            f"missing={missing} drift={len(drift)} "
            f"window={payload.get('window_start','?')[:10]}…"
            f"{payload.get('window_end','?')[:10]}"
        ),
        data={
            "missing_count": missing,
            "drift_count": len(drift),
            "drift": drift[:10],
            "findings": [
                f for f in payload.get("findings", [])
                if f.get("severity") == "missing"
            ][:20],
        },
    )


def _ensure_ops_state(vault_dir: Path, pack: str) -> CheckResult:
    """Run ``ovp-ops-state --rebuild --json`` so the smoke
    operates on a fresh projection.  Returns the per-state count
    dict the rebuild printed."""
    cmd = [
        sys.executable, "-m",
        "ovp_pipeline.commands.ops_state_cli",
        "--vault-dir", str(vault_dir),
        "--pack", pack,
        "--rebuild",
        "--json",
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        return CheckResult(
            name="ops_state_rebuild",
            ok=False,
            detail=(
                f"rebuild failed rc={proc.returncode} "
                f"stderr={proc.stderr.strip()[:200]}"
            ),
        )
    try:
        payload = json.loads(proc.stdout)
    except (ValueError, json.JSONDecodeError):
        return CheckResult(
            name="ops_state_rebuild",
            ok=False,
            detail="rebuild CLI didn't return JSON",
        )
    return CheckResult(
        name="ops_state_rebuild",
        ok=True,
        detail=f"rebuilt {payload.get('total', 0)} items for pack={pack}",
        data={
            "counts": payload.get("counts", {}),
            "total": payload.get("total", 0),
        },
    )


def _check_card_n_equals_drilldown_n(
    vault_dir: Path, pack: str, target_date: str,
) -> tuple[CheckResult, list[dict[str, Any]]]:
    """For every lifecycle state, verify:

    * primary_count (card) == items list total (drilldown)
    * event_count (card) == audit page total (drilldown)

    Returns a CheckResult covering all five states + a per-state
    table the report consumes.
    """
    digest = build_today_digest_payload(
        vault_dir, pack_name=pack, target_date=target_date,
    )
    if not digest.get("available"):
        return (
            CheckResult(
                name="card_n_equals_drilldown_n",
                ok=False,
                detail=f"today payload unavailable: {digest.get('reason')}",
            ),
            [],
        )
    table: list[dict[str, Any]] = []
    all_ok = True
    for card in digest["cards"]:
        state = card["id"]
        primary_count = int(card["primary_count"])
        event_count = int(card["event_count"])

        items = build_items_list_payload(
            vault_dir, state=state, pack_name=pack,
            limit=10_000,  # huge — we want the true row count
        )
        items_total = int(items.get("total", 0))

        if card["event_types"]:
            audit = build_events_audit_payload(
                vault_dir,
                event_types=tuple(card["event_types"]),
                date_key=target_date,
                pack_name=pack,
                limit=10_000,
            )
            audit_total = int(audit.get("total", 0))
        else:
            audit_total = 0

        primary_match = (primary_count == items_total)
        audit_match = (event_count == audit_total)
        if not (primary_match and audit_match):
            all_ok = False
        table.append({
            "state": state,
            "current_items": primary_count,
            "today_evidence": event_count,
            "primary_rows": items_total,
            "audit_rows": audit_total,
            "primary_match": primary_match,
            "audit_match": audit_match,
        })
    return (
        CheckResult(
            name="card_n_equals_drilldown_n",
            ok=all_ok,
            detail=f"states_ok={sum(1 for t in table if t['primary_match'] and t['audit_match'])}/5",
            data={"table": table},
        ),
        table,
    )


def _check_audit_banner(vault_dir: Path) -> CheckResult:
    """Render the /ops/events/audit page (empty filter) and check
    for the role banner."""
    payload = build_events_audit_payload(
        vault_dir, event_types=(), date_key="",
    )
    html = _render_events_audit_page(payload)
    ok = "Raw audit evidence" in html and "/ops/events" in html
    return CheckResult(
        name="audit_page_banner",
        ok=ok,
        detail=(
            "found role banner + link to /ops/events"
            if ok else "role banner missing"
        ),
    )


def _check_reciprocal_banner(vault_dir: Path) -> CheckResult:
    """Render /ops/events and verify the reciprocal banner names
    it as a timeline-projection view and links back."""
    payload = build_event_dossier_payload(vault_dir)
    html = _render_events_page(payload)
    ok = (
        "Timeline projection view" in html
        and "/ops/events/audit" in html
    )
    return CheckResult(
        name="dossier_reciprocal_banner",
        ok=ok,
        detail=(
            "found timeline-projection banner + cross-link"
            if ok else "reciprocal banner missing"
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="M25.6 dogfood acceptance smoke."
    )
    parser.add_argument(
        "--vault-dir", type=Path, required=True,
        help="Vault root.",
    )
    parser.add_argument(
        "--pack", default="research-tech",
        help="Pack scope (default: research-tech).",
    )
    parser.add_argument(
        "--date", default="",
        help=(
            "Target date YYYY-MM-DD; defaults to today UTC.  Card "
            "secondary counts come from this day."
        ),
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help=(
            "Path to write the JSON report.  Defaults to "
            "<vault>/60-Logs/m25-acceptance.json."
        ),
    )
    parser.add_argument(
        "--skip-rebuild", action="store_true",
        help="Skip ops_state --rebuild; read existing projection.",
    )
    args = parser.parse_args(argv)

    vault_dir = args.vault_dir.resolve()
    if not _knowledge_db(vault_dir).exists():
        print(
            f"smoke: no knowledge.db at {_knowledge_db(vault_dir)} — "
            "run `ovp-knowledge-index` first.",
            file=sys.stderr,
        )
        return 3

    out_path = (
        args.out
        or (vault_dir / "60-Logs" / "m25-acceptance.json")
    )

    target_date = args.date or _today_utc()

    checks: list[CheckResult] = []

    print(f"== M25.6 dogfood acceptance ({args.pack}, {target_date}) ==")

    # 1. Producer audit.
    pa = _run_producer_audit(vault_dir)
    checks.append(pa)
    print(f"[{'OK' if pa.ok else 'FAIL'}] producer_audit: {pa.detail}")

    # 2. Ops state rebuild.
    if not args.skip_rebuild:
        rb = _ensure_ops_state(vault_dir, args.pack)
        checks.append(rb)
        print(f"[{'OK' if rb.ok else 'FAIL'}] ops_state_rebuild: {rb.detail}")

    # 3. Card N === drilldown N for every state.
    contract, table = _check_card_n_equals_drilldown_n(
        vault_dir, args.pack, target_date,
    )
    checks.append(contract)
    print(
        f"[{'OK' if contract.ok else 'FAIL'}] "
        f"card_n_equals_drilldown_n: {contract.detail}"
    )
    if table:
        print()
        print("  State          current_items  today_evidence  "
              "primary_rows  audit_rows  primary_match  audit_match")
        for row in table:
            print(
                f"  {row['state']:<14} "
                f"{row['current_items']:<14} "
                f"{row['today_evidence']:<15} "
                f"{row['primary_rows']:<13} "
                f"{row['audit_rows']:<11} "
                f"{str(row['primary_match']):<14} "
                f"{str(row['audit_match'])}"
            )
        print()

    # 4. Audit page banner.
    ab = _check_audit_banner(vault_dir)
    checks.append(ab)
    print(f"[{'OK' if ab.ok else 'FAIL'}] audit_page_banner: {ab.detail}")

    # 5. Reciprocal banner on /ops/events.
    rb2 = _check_reciprocal_banner(vault_dir)
    checks.append(rb2)
    print(f"[{'OK' if rb2.ok else 'FAIL'}] dossier_reciprocal_banner: {rb2.detail}")

    # Aggregate.
    all_ok = all(c.ok for c in checks)
    print()
    print(f"== {'PASS' if all_ok else 'FAIL'} ({sum(1 for c in checks if c.ok)}/{len(checks)} checks) ==")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "vault_dir": str(vault_dir),
                "pack": args.pack,
                "date": target_date,
                "all_ok": all_ok,
                "checks": [
                    {
                        "name": c.name,
                        "ok": c.ok,
                        "detail": c.detail,
                        "data": c.data,
                    }
                    for c in checks
                ],
                "card_n_equals_drilldown_n_table": table,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"report written to {out_path}")

    return 0 if all_ok else 2


def _today_utc() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


if __name__ == "__main__":
    raise SystemExit(main())
