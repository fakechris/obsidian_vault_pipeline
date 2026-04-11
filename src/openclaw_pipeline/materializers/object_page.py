from __future__ import annotations

import sqlite3
from pathlib import Path

from ..runtime import VaultLayout, resolve_vault_dir


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def materialize_object_page(vault_dir: Path, *, pack_name: str, object_id: str) -> Path:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    output_path = layout.compiled_views_dir / pack_name / "objects" / f"{object_id}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    escaped_object_id = _escape_like(object_id)
    with sqlite3.connect(layout.knowledge_db) as conn:
        object_row = conn.execute(
            """
            SELECT object_id, object_kind, title, canonical_path, source_slug
            FROM objects
            WHERE object_id = ?
            """,
            (object_id,),
        ).fetchone()
        if object_row is None:
            raise ValueError(f"Unknown object_id: {object_id}")

        summary_row = conn.execute(
            "SELECT summary_text FROM compiled_summaries WHERE object_id = ?",
            (object_id,),
        ).fetchone()
        claim_rows = conn.execute(
            """
            SELECT claim_kind, claim_text
            FROM claims
            WHERE object_id = ?
            ORDER BY claim_id
            """,
            (object_id,),
        ).fetchall()
        relation_rows = conn.execute(
            """
            SELECT target_object_id, relation_type
            FROM relations
            WHERE source_object_id = ?
            ORDER BY target_object_id
            """,
            (object_id,),
        ).fetchall()
        contradiction_rows = conn.execute(
            """
            SELECT contradiction_id, subject_key, status, resolution_note
            FROM contradictions
            WHERE positive_claim_ids_json LIKE ? ESCAPE '\\' OR negative_claim_ids_json LIKE ? ESCAPE '\\'
            ORDER BY subject_key
            """,
            (f"%{escaped_object_id}::%", f"%{escaped_object_id}::%"),
        ).fetchall()

    _object_id, object_kind, title, canonical_path, source_slug = object_row
    summary_text = summary_row[0] if summary_row else ""

    lines = [
        f"# {title}",
        "",
        f"- object_id: {object_id}",
        f"- object_kind: {object_kind}",
        f"- pack: {pack_name}",
        f"- canonical_path: {canonical_path}",
        "",
        "## Compiled Summary",
        "",
        summary_text or "(none)",
        "",
        "## Claims",
        "",
    ]
    if claim_rows:
        lines.extend(f"- [{claim_kind}] {claim_text}" for claim_kind, claim_text in claim_rows)
    else:
        lines.append("- (none)")

    lines.extend(
        [
            "",
            "## Related Objects",
            "",
        ]
    )
    if relation_rows:
        lines.extend(f"- [[{target_object_id}]] ({relation_type})" for target_object_id, relation_type in relation_rows)
    else:
        lines.append("- (none)")

    lines.extend(
        [
            "",
            "## Contradictions",
            "",
        ]
    )
    if contradiction_rows:
        for contradiction_id, subject_key, status, resolution_note in contradiction_rows:
            lines.append(f"- {subject_key} [{status}] ({contradiction_id})")
            if resolution_note:
                lines.append(f"  - note: {resolution_note}")
    else:
        lines.append("- (none)")

    lines.extend(
        [
            "",
            "## Evidence",
            "",
            f"- source_slug: {source_slug}",
        ]
    )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path
