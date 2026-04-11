from __future__ import annotations

import sqlite3
from pathlib import Path

from ..derived.paths import compiled_view_path
from ..runtime import VaultLayout, resolve_vault_dir


def materialize_topic_view(vault_dir: Path, *, pack_name: str, view_name: str) -> Path:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    output_path = compiled_view_path(layout, pack_name=pack_name, view_name=view_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(layout.knowledge_db) as conn:
        rows = conn.execute(
            """
            SELECT objects.object_id, objects.title, compiled_summaries.summary_text
            FROM objects
            LEFT JOIN compiled_summaries ON compiled_summaries.object_id = objects.object_id
            ORDER BY objects.title
            """
        ).fetchall()
        relation_rows = conn.execute(
            """
            SELECT source_object_id, target_object_id, relation_type
            FROM relations
            ORDER BY source_object_id, target_object_id
            """
        ).fetchall()

    related_map: dict[str, list[tuple[str, str]]] = {}
    for source_object_id, target_object_id, relation_type in relation_rows:
        related_map.setdefault(source_object_id, []).append((target_object_id, relation_type))

    lines = [
        f"# {view_name}",
        "",
        f"- pack: {pack_name}",
        "- builder: topic_view",
        "",
        "## Object Summaries",
        "",
    ]

    if not rows:
        lines.append("- (none)")
    else:
        for object_id, title, summary_text in rows:
            lines.extend(
                [
                    f"### {title}",
                    "",
                    f"- object_id: {object_id}",
                    f"- summary: {summary_text or '(none)'}",
                ]
            )
            related = related_map.get(object_id, [])
            if related:
                lines.append("- related:")
                for target_object_id, relation_type in related:
                    lines.append(f"  - [[{target_object_id}]] ({relation_type})")
            lines.append("")

    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_path
