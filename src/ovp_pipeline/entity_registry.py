"""
Entity Registry — Canonical truth source for typed named entities.

Manages the registry of all active/candidate/alias entities with their
aliases, definitions, and metadata. Parallel to ConceptRegistry (which
manages Evergreen concepts), EntityRegistry manages Entity nodes:
person, company, tool, project, paper, event.

Registry file:  10-Knowledge/Entity/entity-registry.jsonl
Alias index:    10-Knowledge/Entity/_aliases.json

Design: mirrors ConceptRegistry patterns (JSONL persistence, surface
index, status lifecycle) but with entity-specific semantics.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .identity import canonicalize_note_id
from .object_kinds import (
    KIND_COMPANY,
    KIND_EVENT,
    KIND_PAPER,
    KIND_PERSON,
    KIND_PROJECT,
    KIND_TOOL,
    normalize_kind,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATUS_CANDIDATE = "candidate"
STATUS_ACTIVE = "active"
STATUS_ALIAS = "alias"
STATUS_REJECTED = "rejected"

VALID_STATUSES = frozenset({STATUS_CANDIDATE, STATUS_ACTIVE, STATUS_ALIAS, STATUS_REJECTED})

ENTITY_LAYER_KINDS = frozenset({
    KIND_PERSON,
    KIND_COMPANY,
    KIND_TOOL,
    KIND_PROJECT,
    KIND_PAPER,
    KIND_EVENT,
})
"""Object kinds that belong in the Entity layer (not Evergreen)."""


def is_entity_kind(kind: str) -> bool:
    """Return True if *kind* belongs in the Entity layer."""
    return normalize_kind(kind) in ENTITY_LAYER_KINDS


# ---------------------------------------------------------------------------
# EntityEntry
# ---------------------------------------------------------------------------

@dataclass
class EntityEntry:
    """A single entity entry in the registry."""

    slug: str
    title: str
    entity_type: str
    aliases: list[str] = field(default_factory=list)
    definition: str = ""
    status: str = STATUS_CANDIDATE
    mentioned_in_count: int = 0
    related_entities: list[str] = field(default_factory=list)
    external_refs: list[str] = field(default_factory=list)
    source_evergreens: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    confidence_avg: float = 0.0

    def __post_init__(self) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        if self.status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {self.status}")
        self.entity_type = normalize_kind(self.entity_type)
        if self.entity_type not in ENTITY_LAYER_KINDS:
            raise ValueError(
                f"Invalid entity_type '{self.entity_type}'; must be one of {sorted(ENTITY_LAYER_KINDS)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "entity_type": self.entity_type,
            "aliases": self.aliases,
            "definition": self.definition,
            "status": self.status,
            "mentioned_in_count": self.mentioned_in_count,
            "related_entities": self.related_entities,
            "external_refs": self.external_refs,
            "source_evergreens": self.source_evergreens,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "confidence_avg": self.confidence_avg,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EntityEntry:
        return cls(
            slug=data["slug"],
            title=data["title"],
            entity_type=data.get("entity_type", KIND_TOOL),
            aliases=data.get("aliases", []),
            definition=data.get("definition", ""),
            status=data.get("status", STATUS_CANDIDATE),
            mentioned_in_count=data.get("mentioned_in_count", 0),
            related_entities=data.get("related_entities", []),
            external_refs=data.get("external_refs", []),
            source_evergreens=data.get("source_evergreens", []),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            confidence_avg=data.get("confidence_avg", 0.0),
        )

    def touch(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def all_surfaces(self) -> list[str]:
        """Return all surface forms that should resolve to this entity."""
        surfaces = [self.title]
        surfaces.extend(self.aliases)
        slug_surface = self.slug.replace("-", " ")
        if slug_surface.lower() not in {s.lower() for s in surfaces}:
            surfaces.append(slug_surface)
        return surfaces


# ---------------------------------------------------------------------------
# Alias Index (_aliases.json)
# ---------------------------------------------------------------------------

def _normalize_alias(text: str) -> str:
    """Normalize a surface form for alias lookup.

    Lowercases, strips, collapses whitespace. Deliberately simple so
    that lookups are O(1) dict hits.
    """
    return re.sub(r"\s+", " ", text.strip().lower())


def _build_alias_map(entries: list[EntityEntry]) -> dict[str, str]:
    """Build normalized-surface -> canonical-slug map from entries.

    Ambiguous aliases (mapping to multiple entities) are excluded from the
    index to prevent silent mis-resolution.  Use ``find_alias_collisions``
    to audit them.
    """
    alias_map: dict[str, str] = {}
    ambiguous: set[str] = set()
    for entry in entries:
        if entry.status == STATUS_REJECTED:
            continue
        for surface in entry.all_surfaces():
            key = _normalize_alias(surface)
            if not key:
                continue
            if key in ambiguous:
                continue
            if key in alias_map and alias_map[key] != entry.slug:
                ambiguous.add(key)
                del alias_map[key]
                continue
            alias_map[key] = entry.slug
    return alias_map


# ---------------------------------------------------------------------------
# EntityRegistry
# ---------------------------------------------------------------------------

class EntityRegistry:
    """Central registry for typed named entities.

    Mirrors :class:`ConceptRegistry` patterns:

    - JSONL persistence  (``entity-registry.jsonl``)
    - Alias index         (``_aliases.json``)
    - Status lifecycle    (candidate -> active -> alias | rejected)
    - Surface resolution  (alias lookup -> near match)
    """

    REGISTRY_FILENAME = "entity-registry.jsonl"
    ALIAS_INDEX_FILENAME = "_aliases.json"
    ENTITY_DIR = Path("10-Knowledge/Entity")
    CANDIDATES_DIR = Path("10-Knowledge/Entity/_Candidates")

    def __init__(self, vault_dir: Path) -> None:
        self.vault_dir = vault_dir
        self.entity_dir = vault_dir / self.ENTITY_DIR
        self.candidates_dir = vault_dir / self.CANDIDATES_DIR
        self._entries: list[EntityEntry] = []
        self._slug_index: dict[str, int] = {}
        self._alias_map: dict[str, str] = {}

    # ===================== Persistence =====================

    @property
    def registry_path(self) -> Path:
        return self.entity_dir / self.REGISTRY_FILENAME

    @property
    def alias_index_path(self) -> Path:
        return self.entity_dir / self.ALIAS_INDEX_FILENAME

    def load(self) -> EntityRegistry:
        """Load registry and alias index from disk."""
        self._entries = []
        self._slug_index = {}

        if self.registry_path.exists():
            with open(self.registry_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    entry = EntityEntry.from_dict(json.loads(line))
                    self._entries.append(entry)

        self._rebuild_slug_index()
        self._rebuild_alias_map()
        return self

    def save(self) -> None:
        """Persist registry and alias index to disk."""
        self.entity_dir.mkdir(parents=True, exist_ok=True)
        self.candidates_dir.mkdir(parents=True, exist_ok=True)

        with open(self.registry_path, "w", encoding="utf-8") as fh:
            for entry in self._entries:
                fh.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

        self._rebuild_alias_map()
        alias_doc = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "aliases": self._alias_map,
        }
        with open(self.alias_index_path, "w", encoding="utf-8") as fh:
            json.dump(alias_doc, fh, ensure_ascii=False, indent=2)

    def _rebuild_slug_index(self) -> None:
        self._slug_index = {e.slug: i for i, e in enumerate(self._entries)}

    def _rebuild_alias_map(self) -> None:
        self._alias_map = _build_alias_map(self._entries)

    # ===================== Query =====================

    @property
    def entries(self) -> list[EntityEntry]:
        return list(self._entries)

    @property
    def active_entities(self) -> list[EntityEntry]:
        return [e for e in self._entries if e.status == STATUS_ACTIVE]

    @property
    def candidates(self) -> list[EntityEntry]:
        return [e for e in self._entries if e.status == STATUS_CANDIDATE]

    def __len__(self) -> int:
        return len(self._entries)

    def all_entries(self) -> list[EntityEntry]:
        """Return all entries (including rejected)."""
        return list(self._entries)

    def find_by_slug(self, slug: str) -> EntityEntry | None:
        idx = self._slug_index.get(slug)
        if idx is not None:
            return self._entries[idx]
        return None

    def find_by_alias(self, surface: str) -> EntityEntry | None:
        """Resolve a surface form to an entity via the alias map."""
        key = _normalize_alias(surface)
        slug = self._alias_map.get(key)
        if slug is not None:
            return self.find_by_slug(slug)
        return None

    def resolve_mention(self, surface: str) -> EntityEntry | None:
        """Resolve a mention to a canonical entity.

        Resolution order:
        1. Exact slug match
        2. Alias index lookup (O(1))

        Returns ``None`` when the mention cannot be resolved.
        """
        entry = self.find_by_slug(surface)
        if entry and entry.status != STATUS_REJECTED:
            return entry
        entry = self.find_by_alias(surface)
        if entry and entry.status != STATUS_REJECTED:
            return entry
        return None

    def has_slug(self, slug: str) -> bool:
        return slug in self._slug_index

    def count_by_type(self) -> dict[str, int]:
        """Return entity counts grouped by entity_type."""
        counts: dict[str, int] = {}
        for entry in self._entries:
            if entry.status == STATUS_REJECTED:
                continue
            counts[entry.entity_type] = counts.get(entry.entity_type, 0) + 1
        return counts

    def find_by_type(self, entity_type: str) -> list[EntityEntry]:
        """Return all non-rejected entries of a given entity_type."""
        kind = normalize_kind(entity_type)
        return [
            e for e in self._entries
            if e.entity_type == kind and e.status != STATUS_REJECTED
        ]

    def top_mentioned(self, n: int = 10) -> list[EntityEntry]:
        """Return the top-N entities by mentioned_in_count."""
        eligible = [e for e in self._entries if e.status != STATUS_REJECTED]
        eligible.sort(key=lambda e: e.mentioned_in_count, reverse=True)
        return eligible[:n]

    # ===================== Mutation =====================

    def upsert_candidate(
        self,
        slug: str,
        title: str,
        entity_type: str,
        *,
        aliases: list[str] | None = None,
        definition: str = "",
        source_evergreen: str | None = None,
        confidence: float = 0.0,
    ) -> EntityEntry:
        """Create or update a candidate entity.

        If the slug already exists:
        - candidate -> increment counts, merge aliases
        - active    -> just increment mentioned_in_count
        - rejected  -> raise ValueError
        """
        slug = canonicalize_note_id(slug)
        existing = self.find_by_slug(slug)

        if existing:
            if existing.status == STATUS_REJECTED:
                raise ValueError(f"Cannot upsert rejected entity '{slug}'")
            existing.mentioned_in_count += 1
            if source_evergreen and source_evergreen not in existing.source_evergreens:
                existing.source_evergreens.append(source_evergreen)
            if aliases:
                for a in aliases:
                    if a not in existing.aliases:
                        existing.aliases.append(a)
            if confidence > 0:
                total = existing.confidence_avg * max(existing.mentioned_in_count - 1, 1)
                existing.confidence_avg = (total + confidence) / existing.mentioned_in_count
            existing.touch()
            self._rebuild_alias_map()
            return existing

        entry = EntityEntry(
            slug=slug,
            title=title,
            entity_type=entity_type,
            aliases=aliases or [],
            definition=definition,
            status=STATUS_CANDIDATE,
            mentioned_in_count=1,
            source_evergreens=[source_evergreen] if source_evergreen else [],
            confidence_avg=confidence,
        )
        self._entries.append(entry)
        self._slug_index[slug] = len(self._entries) - 1
        self._rebuild_alias_map()
        return entry

    def promote_to_active(self, slug: str) -> EntityEntry:
        """Promote a candidate entity to active status."""
        entry = self.find_by_slug(slug)
        if entry is None:
            raise ValueError(f"Entity '{slug}' not found")
        if entry.status != STATUS_CANDIDATE:
            raise ValueError(
                f"Entity '{slug}' is not a candidate (status: {entry.status})"
            )
        entry.status = STATUS_ACTIVE
        entry.touch()
        self._rebuild_alias_map()
        return entry

    def reject(self, slug: str) -> EntityEntry:
        """Reject a candidate entity."""
        entry = self.find_by_slug(slug)
        if entry is None:
            raise ValueError(f"Entity '{slug}' not found")
        entry.status = STATUS_REJECTED
        entry.touch()
        self._rebuild_alias_map()
        return entry

    def merge_entity(self, source_slug: str, target_slug: str) -> EntityEntry:
        """Merge *source* into *target*: transfer aliases, remove source."""
        source = self.find_by_slug(source_slug)
        target = self.find_by_slug(target_slug)
        if source is None:
            raise ValueError(f"Source entity '{source_slug}' not found")
        if target is None:
            raise ValueError(f"Target entity '{target_slug}' not found")

        for alias in source.aliases:
            if alias not in target.aliases:
                target.aliases.append(alias)
        if source.title not in target.aliases:
            target.aliases.append(source.title)
        if source_slug not in target.aliases:
            target.aliases.append(source_slug.replace("-", " "))

        for eg in source.source_evergreens:
            if eg not in target.source_evergreens:
                target.source_evergreens.append(eg)
        target.mentioned_in_count += source.mentioned_in_count

        for rel in source.related_entities:
            if rel not in target.related_entities and rel != target_slug:
                target.related_entities.append(rel)

        target.touch()

        idx = self._slug_index.pop(source_slug)
        self._entries.pop(idx)
        self._rebuild_slug_index()
        self._rebuild_alias_map()
        return target

    def update_mentioned_count(self, slug: str, delta: int = 1) -> None:
        """Increment the mentioned_in_count for an entity."""
        entry = self.find_by_slug(slug)
        if entry is None:
            raise ValueError(f"Entity '{slug}' not found")
        entry.mentioned_in_count += delta
        entry.touch()

    def add_alias(self, slug: str, alias: str) -> None:
        """Add an alias to an existing entity."""
        entry = self.find_by_slug(slug)
        if entry is None:
            raise ValueError(f"Entity '{slug}' not found")
        if alias not in entry.aliases:
            entry.aliases.append(alias)
            entry.touch()
            self._rebuild_alias_map()

    # ===================== Collision Detection =====================

    def find_alias_collisions(self) -> dict[str, list[str]]:
        """Find aliases that map to multiple entities.

        Returns a dict of ``normalized_alias -> [slug1, slug2, ...]`` for
        all ambiguous aliases.
        """
        alias_to_slugs: dict[str, list[str]] = {}
        for entry in self._entries:
            if entry.status == STATUS_REJECTED:
                continue
            for surface in entry.all_surfaces():
                key = _normalize_alias(surface)
                if key:
                    alias_to_slugs.setdefault(key, [])
                    if entry.slug not in alias_to_slugs[key]:
                        alias_to_slugs[key].append(entry.slug)
        return {k: v for k, v in alias_to_slugs.items() if len(v) > 1}


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def load_entity_registry(vault_dir: Path) -> EntityRegistry:
    """Convenience: create and load an EntityRegistry."""
    return EntityRegistry(vault_dir).load()
