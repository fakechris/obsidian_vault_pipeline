from __future__ import annotations

from pathlib import Path

from ..derived.paths import compiled_view_path
from ..knowledge_index import list_contradictions
from ..projection_labels import markdown_projection_lines
from ..runtime import VaultLayout, resolve_vault_dir


def materialize_contradiction_view(vault_dir: Path, *, pack_name: str, view_name: str) -> Path:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    output_path = compiled_view_path(layout, pack_name=pack_name, view_name=view_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = list_contradictions(resolved_vault, limit=500)

    lines = [
        f"# {view_name}",
        "",
        f"- pack: {pack_name}",
        "- builder: contradiction_view",
        *markdown_projection_lines(
            surface="contradiction_view",
            projection_kind="compiled_wiki_projection",
            owner_pack=pack_name,
            generated_by="materialize_contradiction_view",
            derived_from=("knowledge.db.contradictions", "review audit"),
            rebuild_policy="on_derived_refresh",
        ),
        "",
        "## Contradiction Records",
        "",
    ]

    if not rows:
        lines.append("- (none)")
    else:
        for row in rows:
            lines.extend(
                [
                    f"### {row['subject_key']}",
                    "",
                    f"- contradiction_id: {row['contradiction_id']}",
                    f"- status: {row['status']}",
                    f"- resolved_at: {row['resolved_at'] or '(open)'}",
                    f"- resolution_note: {row['resolution_note'] or '(none)'}",
                    "",
                ]
            )

    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_path
