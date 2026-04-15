from __future__ import annotations

from pathlib import Path

from ..runtime import VaultLayout, resolve_vault_dir
from ..truth_api import get_object_detail


def materialize_object_page(vault_dir: Path, *, pack_name: str, object_id: str) -> Path:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    output_path = layout.compiled_views_dir / pack_name / "objects" / f"{object_id}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    detail = get_object_detail(resolved_vault, object_id)
    object_row = detail["object"]
    object_kind = object_row["object_kind"]
    title = object_row["title"]
    canonical_path = object_row["canonical_path"]
    source_slug = object_row["source_slug"]
    summary_text = detail.get("summary", {}).get("summary_text", "")

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
    if detail["claims"]:
        lines.extend(f"- [{item['claim_kind']}] {item['claim_text']}" for item in detail["claims"])
    else:
        lines.append("- (none)")

    lines.extend(
        [
            "",
            "## Related Objects",
            "",
        ]
    )
    if detail["relations"]:
        lines.extend(
            f"- [[{item['target_object_id']}]] ({item['relation_type']})" for item in detail["relations"]
        )
    else:
        lines.append("- (none)")

    lines.extend(
        [
            "",
            "## Contradictions",
            "",
        ]
    )
    if detail["contradictions"]:
        for item in detail["contradictions"]:
            lines.append(f"- {item['subject_key']} [{item['status']}] ({item['contradiction_id']})")
            if item["resolution_note"]:
                lines.append(f"  - note: {item['resolution_note']}")
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
