from __future__ import annotations

import sqlite3
from pathlib import Path

from ..derived.paths import compiled_view_path
from ..runtime import VaultLayout, resolve_vault_dir


def materialize_event_dossier(vault_dir: Path, *, pack_name: str, view_name: str) -> Path:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    output_path = compiled_view_path(layout, pack_name=pack_name, view_name=view_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(layout.knowledge_db) as conn:
        timeline_rows = conn.execute(
            """
            SELECT timeline_events.event_date, timeline_events.event_type, objects.object_id, objects.title, compiled_summaries.summary_text
            FROM timeline_events
            JOIN objects ON objects.object_id = timeline_events.slug
            LEFT JOIN compiled_summaries ON compiled_summaries.object_id = objects.object_id
            ORDER BY timeline_events.event_date, objects.title
            """
        ).fetchall()

    lines = [
        f"# {view_name}",
        "",
        f"- pack: {pack_name}",
        "- builder: event_dossier",
        "",
        "## Timeline",
        "",
    ]

    if not timeline_rows:
        lines.append("- (none)")
    else:
        current_date = None
        for event_date, event_type, object_id, title, summary_text in timeline_rows:
            if event_date != current_date:
                current_date = event_date
                lines.extend([f"### {event_date}", ""])
            lines.append(f"- [[{object_id}]] — {title} ({event_type})")
            if summary_text:
                lines.append(f"  - {summary_text}")

    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_path
