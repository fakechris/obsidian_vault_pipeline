from __future__ import annotations

from pathlib import Path

import yaml

from ..derived.paths import compiled_view_path
from ..runtime import VaultLayout, resolve_vault_dir
from .specs import WikiViewSpec


def _load_title(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            metadata = yaml.safe_load(parts[1]) or {}
            if metadata.get("title"):
                return str(metadata["title"])
    return path.stem


def build_view(vault_dir: Path, spec: WikiViewSpec) -> Path:
    resolved_vault = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved_vault)
    output_path = compiled_view_path(layout, pack_name=spec.pack, view_name=spec.name)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    titles: list[str] = []
    if layout.evergreen_dir.exists():
        for note in sorted(layout.evergreen_dir.glob("*.md")):
            titles.append(_load_title(note))

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
