from __future__ import annotations

import sqlite3
from pathlib import Path

from ..derived.paths import compiled_view_path
from ..runtime import VaultLayout, resolve_vault_dir


def materialize_contradiction_view(vault_dir: Path, *, pack_name: str, view_name: str) -> Path:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    output_path = compiled_view_path(layout, pack_name=pack_name, view_name=view_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(layout.knowledge_db) as conn:
        try:
            rows = conn.execute(
                """
                SELECT contradiction_id, subject_key, status, resolution_note, resolved_at
                FROM contradictions
                ORDER BY subject_key
                """
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table: contradictions" not in str(exc):
                raise
            rows = []

    lines = [
        f"# {view_name}",
        "",
        f"- pack: {pack_name}",
        "- builder: contradiction_view",
        "",
        "## Contradiction Records",
        "",
    ]

    if not rows:
        lines.append("- (none)")
    else:
        for contradiction_id, subject_key, status, resolution_note, resolved_at in rows:
            lines.extend(
                [
                    f"### {subject_key}",
                    "",
                    f"- contradiction_id: {contradiction_id}",
                    f"- status: {status}",
                    f"- resolved_at: {resolved_at or '(open)'}",
                    f"- resolution_note: {resolution_note or '(none)'}",
                    "",
                ]
            )

    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_path
