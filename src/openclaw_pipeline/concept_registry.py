#!/usr/bin/env python3
"""
Concept Registry - Canonical truth source for Evergreen concepts.

Manages the registry of all active/candidate/alias concepts with their
aliases, definitions, and metadata. This is the single source of truth
for link resolution.

Registry file: 10-Knowledge/Atlas/concept-registry.jsonl
Alias index:    10-Knowledge/Atlas/alias-index.json
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any


# Status constants
STATUS_ACTIVE = "active"
STATUS_CANDIDATE = "candidate"
STATUS_ALIAS = "alias"
STATUS_DEPRECATED = "deprecated"
STATUS_REJECTED = "rejected"

VALID_STATUSES = {STATUS_ACTIVE, STATUS_CANDIDATE, STATUS_ALIAS, STATUS_DEPRECATED, STATUS_REJECTED}


@dataclass
class ConceptEntry:
    """A single concept entry in the registry."""

    slug: str
    title: str
    aliases: list[str]
    definition: str
    area: str
    status: str = STATUS_ACTIVE
    source_count: int = 0
    evidence_count: int = 0
    last_seen_at: str = ""
    review_state: str = ""

    def __post_init__(self):
        if not self.last_seen_at:
            self.last_seen_at = datetime.now().strftime("%Y-%m-%d")
        if self.status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConceptEntry:
        return cls(
            slug=data["slug"],
            title=data["title"],
            aliases=data.get("aliases", []),
            definition=data.get("definition", ""),
            area=data.get("area", ""),
            status=data.get("status", STATUS_ACTIVE),
            source_count=data.get("source_count", 0),
            evidence_count=data.get("evidence_count", 0),
            last_seen_at=data.get("last_seen_at", ""),
            review_state=data.get("review_state", ""),
        )


class ConceptRegistry:
    """
    Central registry for all concept knowledge.

    Loads from / persists to:
    - 10-Knowledge/Atlas/concept-registry.jsonl
    - 10-Knowledge/Atlas/alias-index.json
    """

    REGISTRY_FILENAME = "concept-registry.jsonl"
    ALIAS_INDEX_FILENAME = "alias-index.json"
    ATLAS_DIR = Path("10-Knowledge/Atlas")
    CANDIDATES_DIR = Path("10-Knowledge/Evergreen/_Candidates")

    def __init__(self, vault_dir: Path):
        self.vault_dir = vault_dir
        self.atlas_dir = vault_dir / self.ATLAS_DIR
        self.candidates_dir = vault_dir / self.CANDIDATES_DIR
        self._entries: list[ConceptEntry] = []
        self._alias_index: dict[str, str] = {}  # alias -> slug

    # ========== Persistence ==========

    @property
    def registry_path(self) -> Path:
        return self.atlas_dir / self.REGISTRY_FILENAME

    @property
    def alias_index_path(self) -> Path:
        return self.atlas_dir / self.ALIAS_INDEX_FILENAME

    def load(self) -> ConceptRegistry:
        """Load registry from disk."""
        self._entries = []
        self._alias_index = {}

        if not self.registry_path.exists():
            return self

        with open(self.registry_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                entry = ConceptEntry.from_dict(data)
                self._entries.append(entry)

        self._rebuild_alias_index()
        return self

    def save(self) -> None:
        """Save registry to disk."""
        self.atlas_dir.mkdir(parents=True, exist_ok=True)

        with open(self.registry_path, "w", encoding="utf-8") as f:
            for entry in self._entries:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

        self._rebuild_alias_index()

        with open(self.alias_index_path, "w", encoding="utf-8") as f:
            json.dump(self._alias_index, f, ensure_ascii=False, indent=2)

    def _rebuild_alias_index(self) -> None:
        """Rebuild alias index from entries."""
        self._alias_index = {}
        for entry in self._entries:
            for alias in entry.aliases:
                normalized = self._normalize(alias)
                self._alias_index[normalized] = entry.slug

    # ========== Query ==========

    @property
    def entries(self) -> list[ConceptEntry]:
        return list(self._entries)

    @property
    def active_concepts(self) -> list[ConceptEntry]:
        return [e for e in self._entries if e.status == STATUS_ACTIVE]

    @property
    def candidates(self) -> list[ConceptEntry]:
        return [e for e in self._entries if e.status == STATUS_CANDIDATE]

    def find_by_slug(self, slug: str) -> ConceptEntry | None:
        """Find entry by exact slug match."""
        for entry in self._entries:
            if entry.slug == slug:
                return entry
        return None

    def find_by_alias(self, alias: str) -> ConceptEntry | None:
        """Find entry by exact alias match (case-insensitive)."""
        normalized = self._normalize(alias)
        slug = self._alias_index.get(normalized)
        if slug:
            return self.find_by_slug(slug)
        return None

    def find_by_surface(self, surface: str) -> ConceptEntry | None:
        """Find entry by surface form (slug, title, or alias)."""
        # First try exact slug match
        entry = self.find_by_slug(surface)
        if entry:
            return entry
        # Then try alias match
        return self.find_by_alias(surface)

    def has_active_slug(self, slug: str) -> bool:
        """Check if slug exists and is active."""
        entry = self.find_by_slug(slug)
        return entry is not None and entry.status == STATUS_ACTIVE

    def has_alias(self, alias: str) -> bool:
        """Check if alias exists in index."""
        return self._normalize(alias) in self._alias_index

    def search(self, query: str, area: str | None = None, topk: int = 10) -> list[tuple[ConceptEntry, float]]:
        """
        Search concepts by keyword similarity.

        Uses simple token overlap scoring (BM25-lite).
        Returns list of (entry, score) tuples sorted by descending score.
        """
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scored: list[tuple[ConceptEntry, float]] = []

        # Weight constants for scoring
        WEIGHT_SLUG = 3.0
        WEIGHT_TITLE = 2.0
        WEIGHT_ALIAS = 2.0
        WEIGHT_DEFINITION = 0.5
        MAX_SCORE = WEIGHT_SLUG + WEIGHT_TITLE + WEIGHT_ALIAS + WEIGHT_DEFINITION  # 7.5

        for entry in self._entries:
            if area and entry.area.lower() != area.lower():
                continue

            # Score based on token overlap
            score = 0.0

            # Slug match (highest weight)
            slug_tokens = self._tokenize(entry.slug)
            score += self._score_overlap(query_tokens, slug_tokens) * WEIGHT_SLUG

            # Title match (high weight)
            title_tokens = self._tokenize(entry.title)
            score += self._score_overlap(query_tokens, title_tokens) * WEIGHT_TITLE

            # Alias match
            for alias in entry.aliases:
                alias_tokens = self._tokenize(alias)
                score += self._score_overlap(query_tokens, alias_tokens) * WEIGHT_ALIAS

            # Definition match (lower weight)
            def_tokens = self._tokenize(entry.definition)
            score += self._score_overlap(query_tokens, def_tokens) * WEIGHT_DEFINITION

            # Normalize score to [0, 1] range
            score = score / MAX_SCORE

            if score > 0:
                scored.append((entry, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:topk]

    # ========== Mutation ==========

    def add_entry(self, entry: ConceptEntry) -> None:
        """Add a new entry. Raises if slug already exists."""
        if self.find_by_slug(entry.slug):
            raise ValueError(f"Entry with slug '{entry.slug}' already exists")
        self._entries.append(entry)
        self._rebuild_alias_index()

    def upsert_entry(self, entry: ConceptEntry) -> None:
        """Add or update an entry."""
        existing = self.find_by_slug(entry.slug)
        if existing:
            # Update fields
            idx = self._entries.index(existing)
            self._entries[idx] = entry
        else:
            self._entries.append(entry)
        self._rebuild_alias_index()

    def upsert_candidate(self, slug: str, title: str, definition: str, area: str,
                         aliases: list[str] | None = None) -> ConceptEntry:
        """Create or update a candidate concept."""
        entry = ConceptEntry(
            slug=slug,
            title=title,
            aliases=aliases or [],
            definition=definition,
            area=area,
            status=STATUS_CANDIDATE,
            source_count=1,
            evidence_count=1,
            last_seen_at=datetime.now().strftime("%Y-%m-%d"),
            review_state="needs_review",
        )

        existing = self.find_by_slug(slug)
        if existing:
            if existing.status == STATUS_CANDIDATE:
                # Update existing candidate
                existing.source_count += 1
                existing.evidence_count += 1
                existing.last_seen_at = entry.last_seen_at
                return existing
            else:
                # Conflict with active concept
                raise ValueError(f"Cannot create candidate '{slug}': active concept exists")
        else:
            self._entries.append(entry)
            self._rebuild_alias_index()
            return entry

    def promote_to_active(self, slug: str) -> ConceptEntry:
        """Promote a candidate to active status."""
        entry = self.find_by_slug(slug)
        if not entry:
            raise ValueError(f"Entry '{slug}' not found")
        if entry.status != STATUS_CANDIDATE:
            raise ValueError(f"Entry '{slug}' is not a candidate (status: {entry.status})")

        entry.status = STATUS_ACTIVE
        entry.review_state = "promoted"
        self._rebuild_alias_index()
        return entry

    def merge_as_alias(self, candidate_slug: str, target_slug: str,
                       aliases_to_add: list[str]) -> ConceptEntry:
        """Merge a candidate as an alias of an existing active concept."""
        candidate = self.find_by_slug(candidate_slug)
        target = self.find_by_slug(target_slug)

        if not candidate:
            raise ValueError(f"Candidate '{candidate_slug}' not found")
        if not target:
            raise ValueError(f"Target '{target_slug}' not found")
        if target.status != STATUS_ACTIVE:
            raise ValueError(f"Target '{target_slug}' is not active")

        # Add aliases to target
        for alias in aliases_to_add:
            if alias not in target.aliases:
                target.aliases.append(alias)
        target.source_count += candidate.source_count
        target.evidence_count += candidate.evidence_count
        target.last_seen_at = datetime.now().strftime("%Y-%m-%d")

        # Remove candidate
        self._entries.remove(candidate)
        self._rebuild_alias_index()
        return target

    def reject(self, slug: str) -> None:
        """Mark a candidate as rejected."""
        entry = self.find_by_slug(slug)
        if not entry:
            raise ValueError(f"Entry '{slug}' not found")
        entry.status = STATUS_REJECTED
        entry.review_state = "rejected"
        self._rebuild_alias_index()

    # ========== Helpers ==========

    def _normalize(self, text: str) -> str:
        """Normalize text for alias matching (case-insensitive, accent-insensitive)."""
        # Simple lowercase for now; could expand to full Unicode normalization
        return text.lower().strip()

    def _tokenize(self, text: str) -> set[str]:
        """Tokenize text for matching."""
        if not text:
            return set()
        # Split on non-alphanumeric, lowercase
        tokens = re.findall(r'\w+', text.lower())
        return set(tokens)

    def _score_overlap(self, query_tokens: set[str], target_tokens: set[str]) -> float:
        """Compute token overlap score."""
        if not query_tokens or not target_tokens:
            return 0.0
        overlap = query_tokens & target_tokens
        return len(overlap) / len(query_tokens)


def load_registry(vault_dir: Path) -> ConceptRegistry:
    """Convenience function to load registry."""
    return ConceptRegistry(vault_dir).load()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Concept Registry CLI")
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument("--list", action="store_true", help="List all concepts")
    parser.add_argument("--search", type=str, help="Search concepts")
    args = parser.parse_args()

    registry = load_registry(args.vault_dir)

    if args.list:
        print(f"Registry: {len(registry.entries)} entries")
        print(f"Active: {len(registry.active_concepts)}")
        print(f"Candidates: {len(registry.candidates)}")
        print()
        for entry in registry.entries:
            print(f"[{entry.status}] {entry.slug}")
            if entry.aliases:
                print(f"  aliases: {entry.aliases}")

    elif args.search:
        results = registry.search(args.search, topk=10)
        print(f"Search results for: {args.search}")
        for entry, score in results:
            print(f"  [{score:.2f}] {entry.slug} ({entry.area})")
            print(f"    {entry.definition[:80]}...")
