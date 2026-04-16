from __future__ import annotations

from pathlib import Path

from ..derived.paths import compiled_view_path
from ..extraction.artifacts import iter_run_results
from ..materializers.cluster_view import materialize_cluster_view
from ..materializers.contradiction_view import materialize_contradiction_view
from ..materializers.event_dossier import materialize_event_dossier
from ..materializers.object_page import materialize_object_page
from ..materializers.topic_view import materialize_topic_view
from ..runtime import VaultLayout, iter_markdown_files, markdown_title, resolve_vault_dir
from .specs import WikiViewSpec


def _paths_for_source_kind(layout: VaultLayout, source_kind: str) -> list[Path]:
    source_roots = {
        "evergreen": layout.evergreen_dir,
        "query": layout.queries_dir,
        "atlas": layout.atlas_dir,
        "raw": layout.raw_dir,
    }
    root = source_roots.get(source_kind)
    if root is None or not root.exists():
        return []
    return sorted(iter_markdown_files(root))


def _extraction_lines(layout: VaultLayout, pack_name: str) -> list[str]:
    lines: list[str] = []
    for run in iter_run_results(layout, pack_name=pack_name):
        lines.append(
            f"- profile: {run.profile_name} | source: {run.source_path} | records: {len(run.records)} | relations: {len(run.relations)}"
        )
    return lines


def _resolve_view_inputs(layout: VaultLayout, spec: WikiViewSpec) -> list[Path]:
    if not spec.input_sources:
        return _paths_for_source_kind(layout, "evergreen")

    seen: set[Path] = set()
    resolved: list[Path] = []
    for input_spec in spec.input_sources:
        for path in _paths_for_source_kind(layout, input_spec.source_kind):
            if path in seen:
                continue
            seen.add(path)
            resolved.append(path)
    return resolved


def build_view(vault_dir: Path, spec: WikiViewSpec, *, object_id: str | None = None) -> Path:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)

    if spec.builder == "object_page":
        if not object_id:
            raise ValueError("object_id is required for object_page views")
        return materialize_object_page(resolved_vault, pack_name=spec.pack, object_id=object_id)
    if spec.builder == "topic_view":
        return materialize_topic_view(resolved_vault, pack_name=spec.pack, view_name=spec.name)
    if spec.builder == "event_dossier":
        return materialize_event_dossier(resolved_vault, pack_name=spec.pack, view_name=spec.name)
    if spec.builder == "contradiction_view":
        return materialize_contradiction_view(resolved_vault, pack_name=spec.pack, view_name=spec.name)
    if spec.builder == "cluster_view":
        return materialize_cluster_view(resolved_vault, pack_name=spec.pack, view_name=spec.name)

    output_path = compiled_view_path(layout, pack_name=spec.pack, view_name=spec.name)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if any(input_spec.source_kind == "extraction" for input_spec in (spec.input_sources or [])):
        lines = [
            f"# {spec.name}",
            "",
            f"- pack: {spec.pack}",
            f"- publish_target: {spec.publish_target}",
            "",
            "## Extraction Runs",
            "",
        ]
        extraction_rows = _extraction_lines(layout, spec.pack)
        if extraction_rows:
            lines.extend(extraction_rows)
        else:
            lines.append("- (none)")
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return output_path

    titles: list[str] = []
    for note in _resolve_view_inputs(layout, spec):
        titles.append(markdown_title(note))

    lines = [
        f"# {spec.name}",
        "",
        f"- pack: {spec.pack}",
        f"- publish_target: {spec.publish_target}",
        "",
        "## Included Notes",
        "",
    ]
    if titles:
        lines.extend(f"- {title}" for title in titles)
    else:
        lines.append("- (none)")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path
