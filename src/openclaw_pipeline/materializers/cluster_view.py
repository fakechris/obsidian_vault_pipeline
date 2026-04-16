from __future__ import annotations

from pathlib import Path

from ..derived.paths import compiled_view_path
from ..runtime import VaultLayout, resolve_vault_dir
from ..ui.view_models import build_cluster_browser_payload, build_cluster_detail_payload


def materialize_cluster_view(vault_dir: Path, *, pack_name: str, view_name: str) -> Path:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    output_path = compiled_view_path(layout, pack_name=pack_name, view_name=view_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    browser_payload = build_cluster_browser_payload(resolved_vault, pack_name=pack_name, limit=200)
    rows = browser_payload["items"]

    lines = [
        f"# {view_name}",
        "",
        f"- pack: {pack_name}",
        "- builder: cluster_view",
        "",
        "## Graph Clusters",
        "",
    ]

    if not rows:
        lines.append("- (none)")
    else:
        for row in rows:
            detail = build_cluster_detail_payload(
                resolved_vault,
                cluster_id=str(row["cluster_id"]),
                pack_name=pack_name,
            )
            lines.extend(
                [
                    f"### {row.get('display_title') or row['label']}",
                    "",
                    f"- cluster_id: {row['cluster_id']}",
                    f"- canonical_label: {row['label']}",
                    f"- cluster_kind: {row['cluster_kind']}",
                    f"- center: [[{row['center_object_id']}]]",
                    f"- member_count: {row['member_count']}",
                    f"- score: {row['score']}",
                    f"- priority_band: {row['priority_band']}",
                    f"- priority_reason: {row['priority_reason']}",
                    f"- related_cluster_count: {row['related_cluster_count']}",
                    f"- neighborhood_score: {row['neighborhood_score']}",
                    f"- neighborhood_band: {row['neighborhood_band']}",
                    f"- neighborhood_bridge_kind: {row['neighborhood_bridge_kind']}",
                    f"- neighborhood_reason: {row['neighborhood_reason']}",
                    f"- top_reading_route_kind: {row['top_reading_route_kind']}",
                    f"- top_reading_route_title: {row['top_reading_route_title']}",
                    f"- has_reading_route: {row['has_reading_route']}",
                    f"- reading_intent_count: {row['reading_intent_count']}",
                    f"- reading_intent_preview: {row['reading_intent_preview']}",
                    "",
                    "#### Members",
                    "",
                ]
            )
            if row["members"]:
                for member in row["members"]:
                    lines.append(f"- [[{member['object_id']}]] ({member['title']})")
            else:
                lines.append("- (none)")
            lines.extend(
                [
                    "",
                    "#### Cluster Synthesis",
                    "",
                ]
            )
            for bullet in detail["summary_bullets"]:
                lines.append(f"- {bullet}")
            lines.extend(
                [
                    "",
                    "#### Structural Label",
                    "",
                    f"- kind: {detail['structural_label']['kind']}",
                    f"- title: {detail['structural_label']['title']}",
                    f"- reason: {detail['structural_label']['reason']}",
                    "",
                    "#### Relation Patterns",
                    "",
                ]
            )
            if detail["relation_pattern_items"]:
                for item in detail["relation_pattern_items"]:
                    lines.append(f"- {item['display_name']} ({item['count']})")
            else:
                lines.append("- (none)")
            lines.extend(
                [
                    "",
                    "#### Next Reading Route",
                    "",
                ]
            )
            if detail["next_read_cluster"]:
                lines.extend(
                    [
                        f"- title: {detail['next_read_cluster']['display_title']}",
                        f"- bridge_kind: {detail['next_read_cluster']['bridge_kind']}",
                        f"- bridge_band: {detail['next_read_cluster']['bridge_band']}",
                        f"- reason: {detail['next_read_cluster']['reason']}",
                    ]
                )
            else:
                lines.append("- (none)")
            lines.extend(
                [
                    "",
                    "#### Neighborhood Groups",
                    "",
                ]
            )
            if detail["related_cluster_groups"]:
                for item in detail["related_cluster_groups"]:
                    lines.append(f"- {item['bridge_kind']} ({item['count']})")
            else:
                lines.append("- (none)")
            lines.extend(
                [
                    "",
                    "#### Related Clusters",
                    "",
                ]
            )
            if detail["related_clusters"]:
                for item in detail["related_clusters"]:
                    lines.append(
                        f"- {item['display_title']} [{item['bridge_kind']} / {item['bridge_band']}: {item['reason']}]"
                    )
            else:
                lines.append("- (none)")
            lines.extend(
                [
                    "",
                    "#### Coverage",
                    "",
                    f"- source_note_count: {detail['review_context']['source_note_count']}",
                    f"- moc_count: {detail['review_context']['moc_count']}",
                    f"- open_contradiction_count: {detail['review_context']['open_contradiction_count']}",
                    f"- stale_summary_count: {detail['review_context']['stale_summary_count']}",
                    "",
                    "#### Top Source Notes",
                    "",
                ]
            )
            if detail["top_source_notes"]:
                for item in detail["top_source_notes"]:
                    lines.append(f"- {item['title']} ({item['object_count']} objects)")
            else:
                lines.append("- (none)")
            lines.extend(
                [
                    "",
                    "#### Top Atlas Pages",
                    "",
                ]
            )
            if detail["top_mocs"]:
                for item in detail["top_mocs"]:
                    lines.append(f"- {item['title']} ({item['object_count']} objects)")
            else:
                lines.append("- (none)")
            lines.append("")

    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_path
