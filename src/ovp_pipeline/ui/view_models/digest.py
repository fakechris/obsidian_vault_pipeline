# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

from ._constants import *
from ._layer0 import *
from ._layer1 import *
from ._layer2 import *
from ._layer3 import *




def build_digest_health_payload(vault_dir: Path | str) -> dict[str, Any]:
    """``/ops/digest-health`` payload — three metric panels (M23 / BL-097).

    Reads only ``audit_events``; no new schema.  Returns the data
    shape the renderer consumes — empty / "no data" states are
    explicit so the page doesn't render bogus zeros as authoritative.
    """
    db_path = _db_path(vault_dir)
    if not db_path.exists():
        return {
            "screen": "ops/digest-health",
            "available": False,
            "reason": "knowledge_index has not been built yet",
        }

    with sqlite3.connect(db_path) as conn:
        # Skip rate
        try:
            generated = conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE event_type='digest_generated'"
            ).fetchone()[0]
            skipped = conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE event_type='digest_skipped_no_change'"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            generated = 0
            skipped = 0
        total_attempts = generated + skipped
        skip_rate = (skipped / total_attempts) if total_attempts else None

        # Intake reflection — per generated digest, did Layer 0
        # surface intake when the day had ≥ 3 article_processed events?
        intake_rows: list[dict[str, Any]] = []
        try:
            digests = conn.execute(
                """
                SELECT payload_json, timestamp FROM audit_events
                 WHERE event_type='digest_generated'
                 ORDER BY timestamp DESC LIMIT 60
                """,
            ).fetchall()
            for payload_json, ts in digests:
                try:
                    payload = json.loads(payload_json or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = {}
                if not isinstance(payload, dict):
                    continue
                day_key = (payload.get("window_end") or ts or "")[:10]
                if not day_key:
                    continue
                layer0 = int(payload.get("layer0_events") or 0)
                # Same-day article_processed count from audit_events.
                article_count = conn.execute(
                    """
                    SELECT COUNT(*) FROM audit_events
                     WHERE event_type IN (
                       'article_processed', 'source_archived_to_processed'
                     ) AND timestamp LIKE ?
                    """,
                    (day_key + "%",),
                ).fetchone()[0]
                intake_rows.append({
                    "day": day_key,
                    "layer0_events": layer0,
                    "article_count_for_day": article_count,
                    "active_day": article_count >= 3,
                    "reflected": layer0 > 0,
                })
        except sqlite3.OperationalError:
            intake_rows = []

        active_days = [r for r in intake_rows if r["active_day"]]
        reflected_active = [r for r in active_days if r["reflected"]]
        intake_reflection_rate = (
            len(reflected_active) / len(active_days) if active_days else None
        )

        # Click-through breakdown
        click_breakdown: dict[str, int] = {}
        try:
            rows = conn.execute(
                """
                SELECT payload_json FROM audit_events
                 WHERE event_type='digest_clicked_through'
                """,
            ).fetchall()
            for (payload_json,) in rows:
                try:
                    payload = json.loads(payload_json or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = {}
                action = str(payload.get("action") or "other") if isinstance(payload, dict) else "other"
                click_breakdown[action] = click_breakdown.get(action, 0) + 1
        except sqlite3.OperationalError:
            click_breakdown = {}

    return {
        "screen": "ops/digest-health",
        "available": True,
        "generated_count": generated,
        "skipped_count": skipped,
        "total_attempts": total_attempts,
        "skip_rate": skip_rate,
        "intake_rows": intake_rows,
        "intake_reflection_rate": intake_reflection_rate,
        "click_breakdown": click_breakdown,
    }


__all__ = [
    'build_digest_health_payload'
]
