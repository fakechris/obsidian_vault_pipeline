from __future__ import annotations

from pathlib import Path

from ..derived.paths import compiled_view_path
from ..runtime import VaultLayout, resolve_vault_dir
from ..truth_api import list_graph_clusters


def materialize_cluster_view(vault_dir: Path, *, pack_name: str, view_name: str) -> Path:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    output_path = compiled_view_path(layout, pack_name=pack_name, view_name=view_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = list_graph_clusters(resolved_vault, pack_name=pack_name, limit=200)

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
            lines.extend(
                [
                    f"### {row['label']}",
                    "",
                    f"- cluster_id: {row['cluster_id']}",
                    f"- cluster_kind: {row['cluster_kind']}",
                    f"- center: [[{row['center_object_id']}]]",
                    f"- member_count: {row['member_count']}",
                    f"- score: {row['score']}",
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
            lines.append("")

    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_path
