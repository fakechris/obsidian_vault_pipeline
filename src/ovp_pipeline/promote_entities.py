"""
Promote Entities — promote/merge/reject Entity candidates + generate Entity .md files.

Mirrors promote_candidates.py but for the Entity layer:
- promote: candidate → active, generate Entity .md in 10-Knowledge/Entity/
- merge: combine duplicate entities, rewrite wikilinks
- reject: mark as rejected in registry

Entity .md frontmatter includes entity_type instead of generic "concept".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .entity_registry import (
    STATUS_ACTIVE,
    STATUS_CANDIDATE,
    EntityEntry,
    EntityRegistry,
)
from .identity import canonicalize_note_id

ENTITY_DIR = Path("10-Knowledge/Entity")
CANDIDATES_DIR = Path("10-Knowledge/Entity/_Candidates")
WIKILINK_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


@dataclass
class EntityMutation:
    """Record of a promote/merge/reject action for audit trail."""

    action: str
    slug: str
    target_slug: str | None = None
    touched_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    link_updates: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "slug": self.slug,
            "target_slug": self.target_slug,
            "touched_files": self.touched_files,
            "deleted_files": self.deleted_files,
            "link_updates": self.link_updates,
        }


# ---------------------------------------------------------------------------
# File generation
# ---------------------------------------------------------------------------

def _entity_frontmatter(entry: EntityEntry) -> str:
    """Generate YAML frontmatter for an Entity .md file."""
    import json as _json
    aliases = list(dict.fromkeys(a for a in entry.aliases if a))
    aliases_yaml = ", ".join(_json.dumps(a, ensure_ascii=False) for a in aliases)
    now = datetime.now().strftime("%Y-%m-%d")

    return f"""---
note_id: {entry.slug}
title: "{entry.title}"
type: entity
entity_type: {entry.entity_type}
date: {now}
tags: [entity, {entry.entity_type}]
aliases: [{aliases_yaml}]
---"""


def write_entity_file(
    vault_dir: Path,
    entry: EntityEntry,
    *,
    dry_run: bool = True,
) -> Path | None:
    """Write a formal Entity .md file for an active entity."""
    entity_path = vault_dir / ENTITY_DIR / f"{entry.slug}.md"
    candidate_path = vault_dir / CANDIDATES_DIR / f"{entry.slug}.md"

    if dry_run:
        return None

    entity_path.parent.mkdir(parents=True, exist_ok=True)

    body = f"# {entry.title}\n\n> **定义**: {entry.definition}\n"

    if candidate_path.exists():
        candidate_text = candidate_path.read_text(encoding="utf-8")
        if candidate_text.startswith("---"):
            parts = candidate_text.split("---", 2)
            if len(parts) >= 3:
                body = parts[2].strip()
        body = body.replace("*Candidate entity - pending review*", "").strip()
        if not body.startswith("# "):
            body = f"# {entry.title}\n\n{body}".strip()

    frontmatter = _entity_frontmatter(entry)
    content = f"{frontmatter}\n\n{body}\n\n---\n\n*Promoted from candidate on {datetime.now().strftime('%Y-%m-%d')}*\n"

    entity_path.write_text(content, encoding="utf-8")
    return entity_path


def write_candidate_file(
    vault_dir: Path,
    entry: EntityEntry,
    *,
    dry_run: bool = True,
) -> Path | None:
    """Write a candidate Entity .md stub in _Candidates/."""
    candidate_path = vault_dir / CANDIDATES_DIR / f"{entry.slug}.md"

    if dry_run:
        return None

    candidate_path.parent.mkdir(parents=True, exist_ok=True)

    if candidate_path.exists():
        return candidate_path

    import json as _json
    aliases = list(dict.fromkeys(a for a in entry.aliases if a))
    aliases_yaml = ", ".join(_json.dumps(a, ensure_ascii=False) for a in aliases)
    now = datetime.now().strftime("%Y-%m-%d")

    content = f"""---
note_id: {entry.slug}
title: "{entry.title}"
type: entity
entity_type: {entry.entity_type}
status: candidate
date: {now}
tags: [entity, {entry.entity_type}, candidate]
aliases: [{aliases_yaml}]
---

# {entry.title}

> **定义**: {entry.definition}

*Candidate entity - pending review*
"""
    candidate_path.write_text(content, encoding="utf-8")
    return candidate_path


def delete_candidate_file(
    vault_dir: Path,
    slug: str,
    *,
    dry_run: bool = True,
) -> Path | None:
    """Remove the candidate .md file after promotion."""
    candidate_path = vault_dir / CANDIDATES_DIR / f"{slug}.md"
    if candidate_path.exists() and not dry_run:
        candidate_path.unlink()
        return candidate_path
    return None


# ---------------------------------------------------------------------------
# Promote / Merge / Reject
# ---------------------------------------------------------------------------

def promote_entity(
    vault_dir: Path,
    slug: str,
    *,
    dry_run: bool = True,
) -> EntityMutation:
    """Promote a candidate entity to active status and generate its .md file."""
    registry = EntityRegistry(vault_dir).load()
    entry = registry.find_by_slug(slug)
    if entry is None:
        raise ValueError(f"Entity '{slug}' not found")
    if entry.status != STATUS_CANDIDATE:
        raise ValueError(f"Entity '{slug}' is not a candidate (status: {entry.status})")

    mutation = EntityMutation(action="promote", slug=slug, target_slug=slug)

    registry.promote_to_active(slug)
    entry = registry.find_by_slug(slug)

    entity_path = write_entity_file(vault_dir, entry, dry_run=dry_run)
    if entity_path:
        mutation.touched_files.append(str(entity_path))

    deleted = delete_candidate_file(vault_dir, slug, dry_run=dry_run)
    if deleted:
        mutation.deleted_files.append(str(deleted))

    if not dry_run:
        registry.save()

    return mutation


def merge_entity(
    vault_dir: Path,
    source_slug: str,
    target_slug: str,
    *,
    dry_run: bool = True,
) -> EntityMutation:
    """Merge source entity into target, rewriting wikilinks vault-wide."""
    registry = EntityRegistry(vault_dir).load()

    source = registry.find_by_slug(source_slug)
    target = registry.find_by_slug(target_slug)
    if source is None:
        raise ValueError(f"Source entity '{source_slug}' not found")
    if target is None:
        raise ValueError(f"Target entity '{target_slug}' not found")

    mutation = EntityMutation(
        action="merge",
        slug=source_slug,
        target_slug=target_slug,
    )

    if not dry_run:
        registry.merge_entity(source_slug, target_slug)

        link_count = _rewrite_wikilinks(vault_dir, source_slug, target_slug)
        mutation.link_updates = link_count

        deleted = delete_candidate_file(vault_dir, source_slug, dry_run=False)
        if deleted:
            mutation.deleted_files.append(str(deleted))
        entity_path = vault_dir / ENTITY_DIR / f"{source_slug}.md"
        if entity_path.exists():
            entity_path.unlink()
            mutation.deleted_files.append(str(entity_path))

        registry.save()

    return mutation


def reject_entity(
    vault_dir: Path,
    slug: str,
    *,
    dry_run: bool = True,
) -> EntityMutation:
    """Reject a candidate entity."""
    registry = EntityRegistry(vault_dir).load()
    entry = registry.find_by_slug(slug)
    if entry is None:
        raise ValueError(f"Entity '{slug}' not found")

    mutation = EntityMutation(action="reject", slug=slug)

    if not dry_run:
        registry.reject(slug)
        deleted = delete_candidate_file(vault_dir, slug, dry_run=False)
        if deleted:
            mutation.deleted_files.append(str(deleted))
        registry.save()

    return mutation


# ---------------------------------------------------------------------------
# Auto-promote helper
# ---------------------------------------------------------------------------

AUTO_PROMOTE_THRESHOLD = 3
AUTO_PROMOTE_CONFIDENCE = 0.85


def auto_promote_eligible(entry: EntityEntry) -> bool:
    """Check if an entity meets auto-promote criteria."""
    return (
        entry.status == STATUS_CANDIDATE
        and entry.mentioned_in_count >= AUTO_PROMOTE_THRESHOLD
        and entry.confidence_avg >= AUTO_PROMOTE_CONFIDENCE
    )


def auto_promote_all(
    vault_dir: Path,
    *,
    dry_run: bool = True,
) -> list[EntityMutation]:
    """Promote all eligible candidates automatically."""
    registry = EntityRegistry(vault_dir).load()
    mutations: list[EntityMutation] = []

    for entry in list(registry.candidates):
        if auto_promote_eligible(entry):
            mutation = promote_entity(vault_dir, entry.slug, dry_run=dry_run)
            mutations.append(mutation)

    return mutations


# ---------------------------------------------------------------------------
# Wikilink rewriting
# ---------------------------------------------------------------------------

def _rewrite_wikilinks(
    vault_dir: Path,
    old_slug: str,
    new_slug: str,
) -> dict[str, int]:
    """Rewrite [[old_slug]] → [[new_slug]] across all .md files.

    Returns a dict of {file_path: count} for files modified.
    """
    results: dict[str, int] = {}

    for md_file in vault_dir.rglob("*.md"):
        rel = str(md_file.relative_to(vault_dir))
        if rel.startswith(".") or rel.startswith("node_modules"):
            continue

        try:
            text = md_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        new_text, count = _replace_wikilinks_in_text(text, old_slug, new_slug)
        if count > 0:
            md_file.write_text(new_text, encoding="utf-8")
            results[rel] = count

    return results


def _replace_wikilinks_in_text(
    text: str,
    old_slug: str,
    new_slug: str,
) -> tuple[str, int]:
    """Replace [[old_slug]] with [[new_slug]] in text, returns (new_text, count)."""
    count = 0

    def replacer(m: re.Match) -> str:
        nonlocal count
        target = m.group(1)
        alias = m.group(2)
        if canonicalize_note_id(target) == old_slug:
            count += 1
            if alias:
                return f"[[{new_slug}|{alias}]]"
            return f"[[{new_slug}]]"
        return m.group(0)

    new_text = WIKILINK_PATTERN.sub(replacer, text)
    return new_text, count
