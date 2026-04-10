from __future__ import annotations

from pathlib import Path

from ..derived.paths import compiled_view_path
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


def build_view(vault_dir: Path, spec: WikiViewSpec) -> Path:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    output_path = compiled_view_path(layout, pack_name=spec.pack, view_name=spec.name)
    output_path.parent.mkdir(parents=True, exist_ok=True)

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
