from __future__ import annotations

from pathlib import Path

from ..runtime import VaultLayout, resolve_vault_dir
from ..ui.view_models import build_cluster_detail_payload


def materialize_cluster_crystal(vault_dir: Path, *, pack_name: str, cluster_id: str) -> Path:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    output_path = layout.compiled_views_dir / pack_name / "clusters" / f"{cluster_id}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = build_cluster_detail_payload(resolved_vault, cluster_id=cluster_id, pack_name=pack_name)
    cluster = payload["cluster"]

    lines = [
        f"# cluster/{cluster_id}",
        "",
        f"- pack: {pack_name}",
        "- builder: cluster_crystal",
        f"- cluster_kind: {cluster['cluster_kind']}",
        f"- center: [[{cluster['center_object_id']}]]",
        f"- member_count: {cluster['member_count']}",
        f"- score: {cluster['score']}",
        f"- display_title: {payload['display_title']}",
        "",
        "## Cluster Synthesis",
        "",
    ]

    if payload["summary_bullets"]:
        lines.extend(f"- {bullet}" for bullet in payload["summary_bullets"])
    else:
        lines.append("- (none)")

    lines.extend(
        [
            "",
            "## Structural Label",
            "",
            f"- kind: {payload['structural_label']['kind']}",
            f"- title: {payload['structural_label']['title']}",
            f"- reason: {payload['structural_label']['reason']}",
            "",
            "## Edge Summary",
            "",
        ]
    )
    if payload["edge_summary_items"]:
        for item in payload["edge_summary_items"]:
            lines.append(
                f"- {item['edge_kind']} ({item['edge_family']}) = {item['count']} [{item['display_name']}]"
            )
    else:
        lines.append("- (none)")

    lines.extend(
        [
            "",
            "## Relation Patterns",
            "",
        ]
    )
    if payload["relation_pattern_items"]:
        for item in payload["relation_pattern_items"]:
            lines.append(f"- {item['display_name']} = {item['count']}")
    else:
        lines.append("- (none)")

    lines.extend(
        [
            "",
            "## Coverage",
            "",
            f"- source_note_count: {payload['review_context']['source_note_count']}",
            f"- moc_count: {payload['review_context']['moc_count']}",
            f"- open_contradiction_count: {payload['review_context']['open_contradiction_count']}",
            f"- stale_summary_count: {payload['review_context']['stale_summary_count']}",
            "",
            "## Members",
            "",
        ]
    )
    if cluster["member_links"]:
        for member in cluster["member_links"]:
            lines.append(f"- [[{member['object_id']}]] ({member['title']})")
    else:
        lines.append("- (none)")

    lines.extend(
        [
            "",
            "## Internal Edges",
            "",
        ]
    )
    if payload["edges"]:
        for edge in payload["edges"]:
            lines.append(
                f"- [[{edge['source_object_id']}]] -[{edge['edge_kind']}]-> "
                f"[[{edge['target_object_id']}]]"
            )
    else:
        lines.append("- (none)")

    lines.extend(
        [
            "",
            "## Top Source Notes",
            "",
        ]
    )
    if payload["top_source_notes"]:
        for item in payload["top_source_notes"]:
            lines.append(f"- {item['title']} ({item['object_count']} objects)")
    else:
        lines.append("- (none)")

    lines.extend(
        [
            "",
            "## Top Atlas Pages",
            "",
        ]
    )
    if payload["top_mocs"]:
        for item in payload["top_mocs"]:
            lines.append(f"- {item['title']} ({item['object_count']} objects)")
    else:
        lines.append("- (none)")

    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_path
