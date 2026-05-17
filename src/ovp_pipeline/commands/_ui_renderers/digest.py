# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

from ._constants import *
from ._layer0 import *
from ._layer1 import *
from ._layer2 import *
from ._layer3 import *
from ._layer4 import *




def _render_digest_health_page(payload: dict) -> str:
    """Render ``/ops/digest-health`` — M23 BL-097.

    Three plain panels: skip rate, intake reflection rate, click-
    through breakdown.  Reads from ``build_digest_health_payload``.
    """
    if not payload.get("available", True):
        body = (
            "<section class='card'>"
            "<h2>Digest health unavailable</h2>"
            f"<p class='muted'>{escape(str(payload.get('reason') or 'unknown'))}</p>"
            "<p>Run <code>ovp-knowledge-index</code> to populate "
            "<code>audit_events</code>.</p>"
            "</section>"
        )
        return _layout("Digest health", body)

    def _pct(rate: object) -> str:
        if rate is None:
            return "—"
        return f"{float(rate) * 100:.0f}%"

    generated = int(payload.get("generated_count") or 0)
    skipped = int(payload.get("skipped_count") or 0)
    total = int(payload.get("total_attempts") or 0)
    skip_rate = payload.get("skip_rate")
    intake_rows = list(payload.get("intake_rows") or [])
    intake_rate = payload.get("intake_reflection_rate")
    click_breakdown = dict(payload.get("click_breakdown") or {})

    skip_panel = (
        "<section class='card'>"
        "<h2>Idempotency gate (skip rate)</h2>"
        f"<p class='metric-num'>{_pct(skip_rate)}</p>"
        f"<p class='muted small'>{skipped} skipped / {total} digest attempts. "
        "High is good — the input-hash gate is cutting redundant LLM calls.</p>"
        "</section>"
    )

    if intake_rate is None:
        intake_body = (
            "<p class='muted'>No active days yet "
            "(a day with ≥ 3 article_processed audit events).</p>"
        )
    else:
        active = sum(1 for r in intake_rows if r["active_day"])
        reflected = sum(1 for r in intake_rows if r["active_day"] and r["reflected"])
        intake_body = (
            f"<p class='metric-num'>{_pct(intake_rate)}</p>"
            f"<p class='muted small'>"
            f"{reflected} of {active} active days had Layer 0 surface their intake. "
            "Target: 100%.</p>"
        )
    intake_panel = (
        f"<section class='card'><h2>Intake reflection rate</h2>{intake_body}</section>"
    )

    if not click_breakdown:
        click_body = (
            "<p class='muted'>No clicks recorded yet. "
            "Digests need to be regenerated since BL-097 landed for the "
            "wrap to take effect.</p>"
        )
    else:
        rows = sorted(click_breakdown.items(), key=lambda x: -x[1])
        total_clicks = sum(click_breakdown.values())
        rows_html = "".join(
            f"<tr><td>{escape(action)}</td><td>{count}</td></tr>"
            for action, count in rows
        )
        click_body = (
            f"<p class='metric-num'>{total_clicks}</p>"
            "<table class='kv' style='margin-top:.5rem'>"
            "<tr><th>Action shape</th><th>Clicks</th></tr>"
            f"{rows_html}"
            "</table>"
        )
    click_panel = (
        f"<section class='card'><h2>Click-through breakdown</h2>{click_body}</section>"
    )

    body = (
        "<h1>Digest health</h1>"
        f"<p class='muted'>{generated} digest{'s' if generated != 1 else ''} generated, "
        f"{skipped} skipped (input-hash matched a prior run).</p>"
        + skip_panel
        + intake_panel
        + click_panel
    )
    return _layout("Digest health", body)


__all__ = [
    '_render_digest_health_page'
]
