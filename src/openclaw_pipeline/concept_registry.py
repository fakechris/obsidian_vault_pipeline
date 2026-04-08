#!/usr/bin/env python3
"""
Concept Registry - Canonical truth source for Evergreen concepts.

Manages the registry of all active/candidate/alias concepts with their
aliases, definitions, and metadata. This is the single source of truth
for link resolution.

Registry file: 10-Knowledge/Atlas/concept-registry.jsonl
Alias index:    10-Knowledge/Atlas/alias-index.json

================================================================================
RESOLVER CONTRACT
================================================================================
This module resolves mentions to canonical registry entries using deterministic
surface matching.

- Full-text / vector semantic search is NOT used for automatic link resolution.
- If no exact or safe near surface match exists, the resolver must abstain
  and create a candidate.
- QMD may be used only to attach related context for review or candidate
  generation.
- abstain is not failure, it is correct behavior.

================================================================================
"""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from .discovery import discover_related

try:
    import jieba
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False


# Status constants
STATUS_ACTIVE = "active"
STATUS_CANDIDATE = "candidate"
STATUS_ALIAS = "alias"
STATUS_DEPRECATED = "deprecated"
STATUS_REJECTED = "rejected"

VALID_STATUSES = {STATUS_ACTIVE, STATUS_CANDIDATE, STATUS_ALIAS, STATUS_DEPRECATED, STATUS_REJECTED}


# =============================================================================
# New Data Structures
# =============================================================================

class ResolutionAction(str, Enum):
    """Possible actions for resolving a mention."""
    LINK_EXISTING = "link_existing"
    CREATE_CANDIDATE = "create_candidate"
    REVIEW_AMBIGUOUS = "review_ambiguous"
    PASSTHROUGH_PATH = "passthrough_path"


@dataclass(frozen=True)
class RegistryEntry:
    """A registry entry with surface forms for matching."""
    slug: str
    title: str
    aliases: tuple[str, ...] = ()
    redirects: tuple[str, ...] = ()  # Alternative surface forms
    definition: str | None = None
    area: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class MatchEvidence:
    """Evidence for a match decision."""
    reason: str
    score: float
    normalized_query: str
    matched_surface: str
    extra: dict[str, object] = field(default_factory=dict)


@dataclass
class RelatedContext:
    """Related context from QMD (for review/candidate generation only)."""
    slug: str
    title: str
    score: float
    engine: str = "knowledge"
    kind: str = "semantic"
    snippet: str | None = None


@dataclass
class ResolutionResult:
    """Result of resolving a mention to a registry entry."""
    action: ResolutionAction
    mention: str
    normalized_mention: str
    entry: RegistryEntry | None = None
    confidence: float = 0.0
    evidence: list[MatchEvidence] = field(default_factory=list)
    ambiguous_entries: list[RegistryEntry] = field(default_factory=list)
    related_context: list[RelatedContext] = field(default_factory=list)


@dataclass(frozen=True)
class SurfaceRecord:
    """A surface form record in the surface index."""
    entry: RegistryEntry
    surface: str
    normalized: str
    source: str  # title | alias | redirect | slug


@dataclass(frozen=True)
class NearMatch:
    """A near-surface match candidate."""
    record: SurfaceRecord
    score: float
    metrics: dict[str, float]


# =============================================================================
# Normalizer - Surface normalization only, no semantic expansion
# =============================================================================

_SPLIT_RE = re.compile(r"[\s\-_/:]+")


def normalize_surface(text: str) -> str:
    """
    Normalize text for surface matching.

    Does NOT do semantic expansion. Only applies surface-level transformations:
    - Unicode normalization
    - Case folding
    - Whitespace/punctuation normalization
    - CamelCase splitting
    """
    text = unicodedata.normalize("NFKC", text).strip()
    # Split CamelCase: "ContextEngineering" -> "Context Engineering"
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = text.lower()
    # Remove quote characters
    text = re.sub(r"[''""'`]", "", text)
    # Normalize punctuation to spaces
    text = re.sub(r"[()\[\]{}.,;!?]", " ", text)
    # Split on common delimiters
    text = _SPLIT_RE.sub(" ", text)
    # Collapse multiple spaces
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize_surface(text: str) -> tuple[str, ...]:
    """Tokenize normalized surface text."""
    norm = normalize_surface(text)
    return tuple(tok for tok in norm.split(" ") if tok)


def slug_to_surface(slug: str) -> str:
    """Convert a slug to its surface form."""
    return normalize_surface(slug.replace("/", " ").replace("-", " ").replace("_", " "))


# =============================================================================
# Legacy ConceptEntry (for backwards compatibility with existing code)
# =============================================================================

# Kind constants for registry entry classification
KIND_ENTITY = "entity"
KIND_CONCEPT = "concept"
KIND_FRAMEWORK = "framework"
KIND_PROTOCOL = "protocol"
KIND_PROPOSITION = "proposition"
KIND_CASE = "case"

VALID_KINDS = {KIND_ENTITY, KIND_CONCEPT, KIND_FRAMEWORK, KIND_PROTOCOL, KIND_PROPOSITION, KIND_CASE}

@dataclass
class ConceptEntry:
    """A single concept entry in the registry."""

    slug: str
    title: str
    aliases: list[str]
    definition: str
    area: str
    status: str = STATUS_ACTIVE
    kind: str = KIND_CONCEPT
    resolver_enabled: bool = True
    canonical_surface: str = ""
    redirects: list[str] = field(default_factory=list)
    blocked_surfaces: list[str] = field(default_factory=list)
    replaced_by: str | None = None
    source_count: int = 0
    evidence_count: int = 0
    last_seen_at: str = ""
    review_state: str = ""

    def __post_init__(self):
        if not self.last_seen_at:
            self.last_seen_at = datetime.now().strftime("%Y-%m-%d")
        if self.status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {self.status}")
        if self.kind not in VALID_KINDS:
            raise ValueError(f"Invalid kind: {self.kind}")
        # Auto-set canonical_surface from title if not provided
        if not self.canonical_surface:
            self.canonical_surface = self.title

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
            kind=data.get("kind", KIND_CONCEPT),
            resolver_enabled=data.get("resolver_enabled", True),
            canonical_surface=data.get("canonical_surface", ""),
            redirects=data.get("redirects", []),
            blocked_surfaces=data.get("blocked_surfaces", []),
            replaced_by=data.get("replaced_by"),
            source_count=data.get("source_count", 0),
            evidence_count=data.get("evidence_count", 0),
            last_seen_at=data.get("last_seen_at", ""),
            review_state=data.get("review_state", ""),
        )

    def to_registry_entry(self) -> RegistryEntry:
        """Convert to new RegistryEntry format."""
        return RegistryEntry(
            slug=self.slug,
            title=self.title,
            aliases=tuple(self.aliases),
            redirects=tuple(self.redirects),
            definition=self.definition,
            area=self.area,
            status=self.status,
        )

    def is_resolver_eligible(self) -> bool:
        """
        Check if this entry should participate in surface resolution.

        Rules:
        1. status must be active
        2. resolver_enabled must be True
        3. kind cannot be proposition or case
        4. title cannot be sentence-like (unless resolver_enabled is explicitly True)
        """
        if self.status not in {STATUS_ACTIVE}:
            return False
        if not self.resolver_enabled:
            return False
        if self.kind in {KIND_PROPOSITION, KIND_CASE}:
            return False
        if is_sentence_like_title(self.title) and not self.resolver_enabled:
            return False
        return True


def asdict(obj):
    """Helper for dataclass to_dict."""
    if hasattr(obj, '__dataclass_fields__'):
        result = {}
        for name, field in obj.__dataclass_fields__.items():
            value = getattr(obj, name)
            if isinstance(value, (list, tuple)) and field.type in ("tuple[str, ...]", "list[str]"):
                value = list(value)
            result[name] = value
        return result
    raise TypeError(f"Object {obj} is not a dataclass")


# =============================================================================
# ConceptRegistry - Surface-index based resolver
# =============================================================================

_PATH_LIKE_RE = re.compile(r"[/\\]")


class ConceptRegistry:
    """
    Central registry for all concept knowledge.

    Loads from / persists to:
    - 10-Knowledge/Atlas/concept-registry.jsonl
    - 10-Knowledge/Atlas/alias-index.json

    Resolver uses surface index only (no full-text semantic search).
    QMD is demoted to auxiliary context provider only.
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
        self._registry_entries: list[RegistryEntry] = []
        self._alias_index: dict[str, str] = {}  # alias -> slug (legacy)
        self._token_cache: dict[str, frozenset[str]] = {}  # text -> tokens
        self._qmd_checked: bool = False
        self._qmd_available: bool = False

        # Surface index for deterministic resolution
        self._surface_index: dict[str, list[SurfaceRecord]] = defaultdict(list)
        self._surface_records: list[SurfaceRecord] = []

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
        self._registry_entries = []

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
                self._registry_entries.append(entry.to_registry_entry())

        self._rebuild_alias_index()
        self._build_surface_index()
        self._precompute_tokens()  # Pre-compute tokens for faster search
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
        """Rebuild alias index from entries (legacy)."""
        self._alias_index = {}
        for entry in self._entries:
            for alias in entry.aliases:
                normalized = self._normalize(alias)
                self._alias_index[normalized] = entry.slug

    def _build_surface_index(self) -> None:
        """Build surface index from registry entries.

        Only entries that pass is_resolver_eligible() are included.
        """
        self._surface_index = defaultdict(list)
        self._surface_records = []

        # Use _entries (ConceptEntry) to check resolver eligibility
        for entry in self._entries:
            # Skip entries that are not resolver-eligible
            if not entry.is_resolver_eligible():
                continue

            # Get the corresponding RegistryEntry
            registry_entry = entry.to_registry_entry()

            surfaces: list[tuple[str, str]] = [
                ("canonical", entry.canonical_surface),
                ("title", entry.title),
                ("slug", slug_to_surface(entry.slug)),
            ]
            surfaces.extend(("alias", x) for x in entry.aliases)
            surfaces.extend(("redirect", x) for x in entry.redirects)

            seen: set[str] = set()
            for source, surface in surfaces:
                norm = normalize_surface(surface)
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                rec = SurfaceRecord(
                    entry=registry_entry,
                    surface=surface,
                    normalized=norm,
                    source=source,
                )
                self._surface_index[norm].append(rec)
                self._surface_records.append(rec)

    # ========== Query (Legacy compatibility) ==========

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
        Search concepts (legacy interface for backwards compatibility).

        DEPRECATED: Use resolve_mention() instead.

        Legacy callers expect search() to return a ranked candidate list even when
        the deterministic resolver abstains. Keep exact-resolution semantics first,
        then fall back to a lexical surface search over registry-managed identifiers.
        """
        result = self.resolve_mention(query, area=area)
        if result.action == ResolutionAction.LINK_EXISTING and result.entry:
            # Find legacy ConceptEntry
            entry = self.find_by_slug(result.entry.slug)
            if entry:
                return [(entry, result.confidence)]
        if result.action == ResolutionAction.REVIEW_AMBIGUOUS:
            results = []
            for reg_entry in result.ambiguous_entries[:topk]:
                entry = self.find_by_slug(reg_entry.slug)
                if entry:
                    results.append((entry, 0.0))
            return results
        return self._legacy_surface_search(query, area=area, topk=topk)

    def to_object_records(self) -> list[Any]:
        """Project legacy concept entries into pack-aware object records."""
        from .object_registry import record_from_concept_entry

        return [record_from_concept_entry(entry) for entry in self._entries]

    # ========== New Resolution API ==========

    def resolve_mention(self, mention: str, area: str | None = None) -> ResolutionResult:
        """
        Resolve a mention to a registry entry using deterministic surface matching.

        Resolution cascade:
        1. Path-like mentions -> passthrough_path
        2. Exact surface match -> link_existing (confidence=1.0)
        3. Ambiguous exact match -> review_ambiguous
        4. Safe near match -> link_existing
        5. No match -> create_candidate (with QMD related context)

        QMD is NOT used for auto-link decisions, only for auxiliary context.
        """
        norm = normalize_surface(mention)

        # Step 1: Path-like mentions (e.g., Evergreen/Agent-Harness)
        if is_path_like_mention(mention):
            return ResolutionResult(
                action=ResolutionAction.PASSTHROUGH_PATH,
                mention=mention,
                normalized_mention=norm,
                confidence=1.0,
                evidence=[MatchEvidence(
                    reason="path_like_mention",
                    score=1.0,
                    normalized_query=norm,
                    matched_surface=mention,
                )],
            )

        # Step 2: Exact surface match
        exact = self._surface_index.get(norm, [])
        if area:
            exact = [x for x in exact if x.entry.area == area]

        unique_entries = self._dedupe_entries(exact)

        if len(unique_entries) == 1:
            rec = exact[0]
            return ResolutionResult(
                action=ResolutionAction.LINK_EXISTING,
                mention=mention,
                normalized_mention=norm,
                entry=rec.entry,
                confidence=1.0,
                evidence=[MatchEvidence(
                    reason=f"exact_{rec.source}",
                    score=1.0,
                    normalized_query=norm,
                    matched_surface=rec.surface,
                )],
            )

        if len(unique_entries) > 1:
            return ResolutionResult(
                action=ResolutionAction.REVIEW_AMBIGUOUS,
                mention=mention,
                normalized_mention=norm,
                ambiguous_entries=unique_entries,
                confidence=0.0,
                evidence=[MatchEvidence(
                    reason="ambiguous_exact_surface",
                    score=0.0,
                    normalized_query=norm,
                    matched_surface=norm,
                )],
                related_context=self._discover_related_context(mention),
            )

        # Step 3: Safe near match
        near = self._safe_near_candidates(mention, area=area, topk=5)
        chosen = self._is_safe_near_match(near)
        if chosen:
            return ResolutionResult(
                action=ResolutionAction.LINK_EXISTING,
                mention=mention,
                normalized_mention=norm,
                entry=chosen.record.entry,
                confidence=chosen.score,
                evidence=[MatchEvidence(
                    reason=f"near_{chosen.record.source}",
                    score=chosen.score,
                    normalized_query=norm,
                    matched_surface=chosen.record.surface,
                    extra=chosen.metrics,
                )],
            )

        # Step 4: No safe match -> create candidate
        return ResolutionResult(
            action=ResolutionAction.CREATE_CANDIDATE,
            mention=mention,
            normalized_mention=norm,
            confidence=0.0,
            evidence=[MatchEvidence(
                reason="no_safe_surface_match",
                score=0.0,
                normalized_query=norm,
                matched_surface=norm,
            )],
            related_context=self._discover_related_context(mention),
        )

    def _dedupe_entries(self, records: list[SurfaceRecord]) -> list[RegistryEntry]:
        """Deduplicate entries from surface records."""
        out = []
        seen = set()
        for rec in records:
            if rec.entry.slug in seen:
                continue
            seen.add(rec.entry.slug)
            out.append(rec.entry)
        return out

    # ========== Surface Matching Metrics ==========

    @staticmethod
    def _token_f1(a: tuple[str, ...], b: tuple[str, ...]) -> float:
        """Compute token-level F1 score."""
        sa, sb = set(a), set(b)
        if not sa or not sb:
            return 0.0
        inter = len(sa & sb)
        if inter == 0:
            return 0.0
        p = inter / len(sb)
        r = inter / len(sa)
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    @staticmethod
    def _char_ngrams(s: str, n: int = 3) -> set[str]:
        """Generate character n-grams."""
        s = f"  {s}  "
        if len(s) < n:
            return {s}
        return {s[i:i+n] for i in range(len(s) - n + 1)}

    @staticmethod
    def _trigram_jaccard(a: str, b: str) -> float:
        """Compute character trigram Jaccard similarity."""
        ga, gb = ConceptRegistry._char_ngrams(a, 3), ConceptRegistry._char_ngrams(b, 3)
        if not ga or not gb:
            return 0.0
        return len(ga & gb) / len(ga | gb)

    @staticmethod
    def _length_ratio(a: str, b: str) -> float:
        """Compute length ratio."""
        if not a or not b:
            return 0.0
        return min(len(a), len(b)) / max(len(a), len(b))

    def _safe_near_candidates(self, mention: str, area: str | None = None, topk: int = 5) -> list[NearMatch]:
        """
        Find near-surface match candidates using strict criteria.

        This is NOT a general search. It's specifically for catching
        format variations (case, punctuation, minor token differences).
        """
        norm = normalize_surface(mention)
        q_tokens = tokenize_surface(norm)
        out: list[NearMatch] = []

        for rec in self._surface_records:
            if area and rec.entry.area and rec.entry.area != area:
                continue
            t_tokens = tokenize_surface(rec.normalized)

            tf1 = self._token_f1(q_tokens, t_tokens)
            tri = self._trigram_jaccard(norm, rec.normalized)
            lr = self._length_ratio(norm, rec.normalized)
            extra = max(0, len(set(t_tokens) - set(q_tokens)))
            missing = max(0, len(set(q_tokens) - set(t_tokens)))

            # Weighted combination
            score = 0.45 * tf1 + 0.35 * tri + 0.20 * lr

            # Strong penalty: target has many extra content words
            if extra >= 2:
                score -= 0.20
            if missing >= 1:
                score -= 0.15

            # Prevent short entity names from matching long命题 slugs
            # e.g., "Claude Code" should NOT auto-match "claude-code-uses-index-not..."
            if len(q_tokens) <= 3 and len(t_tokens) - len(q_tokens) >= 2:
                score -= 0.25

            if score >= 0.72:
                out.append(NearMatch(
                    record=rec,
                    score=max(0.0, min(1.0, score)),
                    metrics={
                        "token_f1": tf1,
                        "trigram_jaccard": tri,
                        "length_ratio": lr,
                        "extra_tokens": float(extra),
                        "missing_tokens": float(missing),
                    }
                ))

        out.sort(key=lambda x: x.score, reverse=True)
        return out[:topk]

    def _is_safe_near_match(self, cands: list[NearMatch]) -> NearMatch | None:
        """
        Check if the top candidate is a safe near match.

        Criteria are VERY strict - this is meant to catch format variations,
        not semantic similarities.
        """
        if not cands:
            return None
        top1 = cands[0]
        top2 = cands[1] if len(cands) > 1 else None

        m = top1.metrics

        # Token F1 must be very high (almost exact match)
        if m["token_f1"] < 0.90:
            return None
        # Trigram must be very similar
        if m["trigram_jaccard"] < 0.82:
            return None
        # Length ratio must be very close
        if m["length_ratio"] < 0.80:
            return None
        # Cannot have extra content words
        if m["extra_tokens"] > 1:
            return None
        # Must be significantly better than second place
        if top2 and (top1.score - top2.score) < 0.08:
            return None

        return top1

    def _legacy_surface_search(
        self,
        query: str,
        area: str | None = None,
        topk: int = 10,
    ) -> list[tuple[ConceptEntry, float]]:
        """
        Compatibility search for legacy callers.

        This is intentionally lexical-only. It ranks entries using their registry
        surfaces (canonical title / title / slug / aliases / redirects) without
        introducing semantic expansion or non-deterministic matching.
        """
        norm_query = normalize_surface(query)
        if not norm_query:
            return []

        q_tokens = set(tokenize_surface(norm_query))
        ranked: list[tuple[ConceptEntry, float]] = []

        for entry in self._entries:
            if area and entry.area and entry.area != area:
                continue

            best_score = 0.0
            surfaces = [
                entry.canonical_surface,
                entry.title,
                slug_to_surface(entry.slug),
                *entry.aliases,
                *entry.redirects,
            ]

            for surface in surfaces:
                norm_surface = normalize_surface(surface)
                if not norm_surface:
                    continue

                score = 0.0
                surface_tokens = set(tokenize_surface(norm_surface))

                if norm_query == norm_surface:
                    score = 1.0
                elif norm_query in surface_tokens:
                    score = 0.90
                elif norm_query in norm_surface:
                    score = 0.82
                elif q_tokens and q_tokens <= surface_tokens:
                    score = 0.78
                else:
                    tf1 = self._token_f1(tuple(q_tokens), tuple(surface_tokens))
                    tri = self._trigram_jaccard(norm_query, norm_surface)
                    lr = self._length_ratio(norm_query, norm_surface)
                    score = 0.45 * tf1 + 0.35 * tri + 0.20 * lr

                best_score = max(best_score, score)

            if best_score >= 0.25:
                ranked.append((entry, round(best_score, 6)))

        ranked.sort(
            key=lambda item: (
                -item[1],
                item[0].status != STATUS_ACTIVE,
                item[0].title.lower(),
                item[0].slug,
            )
        )
        return ranked[:topk]

    # ========== QMD (Auxiliary only) ==========

    def _qmd_related_context(self, mention: str, topk: int = 5) -> list[RelatedContext]:
        """
        Get related context from QMD for auxiliary purposes only.

        QMD is NOT used for auto-link resolution. It only provides
        related context for:
        - create_candidate: suggest related existing concepts
        - review_ambiguous: show what QMD thinks is related
        """
        if not self._qmd_checked:
            self._qmd_checked = True
            self._qmd_available = False
            try:
                result = subprocess.run(
                    ["qmd", "query", "--help"],
                    capture_output=True, timeout=5
                )
                if result.returncode == 0:
                    self._qmd_available = True
            except (subprocess.SubprocessError, FileNotFoundError):
                self._qmd_available = False

            if self._qmd_available:
                try:
                    result = subprocess.run(
                        ["qmd", "collection", "list"],
                        capture_output=True, text=True, timeout=10
                    )
                    if not re.search(r'^registry\s', result.stdout, re.MULTILINE):
                        self._qmd_available = False
                except subprocess.SubprocessError:
                    self._qmd_available = False

        if not self._qmd_available:
            return []

        try:
            cmd = ["qmd", "query", mention, "--collection", "registry", "--top-k", str(topk), "--json"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                return []

            import json
            data = json.loads(result.stdout)
            items = data if isinstance(data, list) else data.get("results", [])

            out: list[RelatedContext] = []
            for item in items:
                url = item.get("file", "")
                score = item.get("score", 0.0)
                slug = url.split("/")[-1].replace(".md", "")
                out.append(RelatedContext(
                    slug=slug,
                    title=item.get("title", slug),
                    score=float(score),
                    snippet=item.get("snippet"),
                ))
            return out

        except (subprocess.SubprocessError, json.JSONDecodeError, ValueError, TimeoutError):
            return []

    def _discover_related_context(self, mention: str, topk: int = 5) -> list[RelatedContext]:
        """
        Shared related-context discovery for candidate/review flows.

        This is auxiliary only. It never determines canonical identity.
        """
        rows = discover_related(self.vault_dir, mention, engine="knowledge", limit=topk)
        return [
            RelatedContext(
                slug=str(row["slug"]),
                title=str(row["title"]),
                score=float(row["score"]),
                engine=str(row.get("engine") or "knowledge"),
                kind=str(row.get("kind") or "semantic"),
                snippet=str(row.get("snippet") or "") or None,
            )
            for row in rows
        ]

    # ========== Legacy Search Methods (Deprecated) ==========

    def _search_via_qmd(self, query: str, area: str | None, topk: int) -> list[tuple[ConceptEntry, float]]:
        """DEPRECATED: Use resolve_mention() instead."""
        return []

    def _search_jaccard(self, query: str, area: str | None, topk: int) -> list[tuple[ConceptEntry, float]]:
        """DEPRECATED: Use resolve_mention() instead."""
        return []

    # ========== Mutation ==========

    def add_entry(self, entry: ConceptEntry) -> None:
        """Add a new entry. Raises if slug already exists."""
        if self.find_by_slug(entry.slug):
            raise ValueError(f"Entry with slug '{entry.slug}' already exists")
        self._entries.append(entry)
        self._registry_entries.append(entry.to_registry_entry())
        self._rebuild_alias_index()
        self._build_surface_index()

    def upsert_entry(self, entry: ConceptEntry) -> None:
        """Add or update an entry."""
        existing = self.find_by_slug(entry.slug)
        if existing:
            idx = self._entries.index(existing)
            self._entries[idx] = entry
            self._registry_entries[idx] = entry.to_registry_entry()
        else:
            self._entries.append(entry)
            self._registry_entries.append(entry.to_registry_entry())
        self._rebuild_alias_index()
        self._build_surface_index()

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
                existing.source_count += 1
                existing.evidence_count += 1
                existing.last_seen_at = entry.last_seen_at
                return existing
            else:
                raise ValueError(f"Cannot create candidate '{slug}': active concept exists")
        else:
            self._entries.append(entry)
            self._registry_entries.append(entry.to_registry_entry())
            self._rebuild_alias_index()
            self._build_surface_index()
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
        self._build_surface_index()
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

        for alias in aliases_to_add:
            if alias not in target.aliases:
                target.aliases.append(alias)
        if candidate.slug not in target.redirects:
            target.redirects.append(candidate.slug)
        target.source_count += candidate.source_count
        target.evidence_count += candidate.evidence_count
        target.last_seen_at = datetime.now().strftime("%Y-%m-%d")

        self._entries.remove(candidate)
        self._registry_entries = [e for e in self._registry_entries if e.slug != candidate.slug]
        self._rebuild_alias_index()
        self._build_surface_index()
        return target

    def reject(self, slug: str) -> None:
        """Mark a candidate as rejected."""
        entry = self.find_by_slug(slug)
        if not entry:
            raise ValueError(f"Entry '{slug}' not found")
        entry.status = STATUS_REJECTED
        entry.review_state = "rejected"
        self._rebuild_alias_index()
        self._build_surface_index()

    # ========== Registry Fix Flow ==========

    def find_surface_conflicts(self) -> dict[str, list[tuple[str, str, str]]]:
        """
        Find all surface conflicts where one normalized surface maps to multiple slugs.

        Only considers resolver-eligible entries (already filtered in _surface_records).

        Returns:
            Dict mapping normalized surface -> list of (slug, title, source) tuples
        """
        # Group by normalized surface using _surface_records (already filtered)
        surface_to_entries = defaultdict(list)
        for rec in self._surface_records:
            surface_to_entries[rec.normalized].append((
                rec.entry.slug,
                rec.entry.title,
                rec.source,
            ))

        # Filter to only conflicts (multiple slugs)
        conflicts = {
            surf: entries
            for surf, entries in surface_to_entries.items()
            if len({e[0] for e in entries}) > 1
        }
        return conflicts

    def get_qmd_similarity(self, query_title: str, target_slug: str, reverse: bool = False) -> float:
        """
        Get QMD semantic similarity between a query title and a target slug.

        Does bidirectional query: query→target and target→query, returns max score.

        Args:
            query_title: Title to search with
            target_slug: Slug to find similarity for
            reverse: If True, also query target_title→query_slug for bidirectional check

        Returns:
            Similarity score (0.0 - 1.0), or 0.0 if QMD unavailable
        """
        if not self._qmd_available:
            return 0.0

        try:
            cmd = [
                "qmd", "query", query_title,
                "--collection", "registry",
                "--top-k", "5", "--json"
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                return 0.0

            data = json.loads(result.stdout)
            items = data if isinstance(data, list) else data.get("results", [])

            for item in items:
                item_slug = item.get("file", "").split("/")[-1].replace(".md", "")
                if item_slug.lower() == target_slug.lower():
                    return float(item.get("score", 0.0))
            return 0.0

        except (subprocess.SubprocessError, json.JSONDecodeError, TimeoutError):
            return 0.0

    def get_bidirectional_similarity(self, title1: str, slug1: str, title2: str, slug2: str) -> float:
        """
        Get bidirectional QMD similarity between two entries.

        Queries both directions and returns the max score.
        """
        score1 = self.get_qmd_similarity(title1, slug2)
        score2 = self.get_qmd_similarity(title2, slug1)
        return max(score1, score2)

    def is_sentence_like_title(self, title: str) -> tuple[bool, str]:
        """
        Detect if a title looks like a sentence/description rather than a concept name.

        Returns:
            (is_sentence, reason)
        """
        # Sentence-like patterns in Chinese
        sentence_patterns = [
            r"采用", r"实现", r"缺乏", r"通过", r"利用", r"基于",
            r"使用", r"提供", r"进行", r"完成", r"解决"
        ]
        # Sentence-like patterns in English
        english_sentence_patterns = [
            r" uses ", r" implements ", r" lacks ", r" achieves ",
            r" enables ", r" provides ", r" does ", r" how to ",
            r" a .* way to", r" to .* and .*", r" is a .* for"
        ]

        import re as regex_module
        for pattern in sentence_patterns:
            if regex_module.search(pattern, title):
                return True, f"contains narrative pattern: {pattern}"

        for pattern in english_sentence_patterns:
            if regex_module.search(pattern, title.lower()):
                return True, f"contains narrative pattern: {pattern}"

        # Check for sentence structure: verb + object or subject + predicate
        # Long titles with multiple clauses are likely sentences
        chinese_clause_markers = ["，", "。", "；", "、"]
        clause_count = sum(1 for m in chinese_clause_markers if m in title)
        if clause_count >= 2:
            return True, f"multiple clauses ({clause_count})"

        # Titles that are > 15 words and contain action verbs are likely sentences
        words = title.split()
        if len(words) > 15:
            action_verbs = ["is", "are", "was", "were", "does", "does", "makes", "using"]
            if any(v in title.lower() for v in action_verbs):
                return True, "long sentence structure"

        return False, ""

    def fix_surface_conflicts(
        self,
        dry_run: bool = True,
        similarity_threshold: float = 0.8,
        min_similarity_for_merge: float = 0.5,
    ) -> dict[str, Any]:
        """
        Detect and fix surface conflicts in the registry.

        A surface conflict occurs when one normalized surface (e.g., "mcp protocol")
        maps to multiple different slugs (e.g., "MCP" and "MCP-Protocol").

        Resolution logic:
        - QMD similarity is a review signal, not an automatic merge trigger
        - If QMD similarity between entries > min_similarity_for_merge -> review_needed
        - If QMD similarity < min_similarity_for_merge -> separate (remove conflict aliases)

        Args:
            dry_run: If True, only report conflicts without making changes
            similarity_threshold: Threshold for automatic merge decision (default 0.8)
            min_similarity_for_merge: Minimum similarity for merge consideration (default 0.5)

        Returns:
            Dict with conflict analysis and suggested actions
        """
        # Ensure QMD availability is checked before calculating similarities
        if not self._qmd_checked:
            self._qmd_related_context("test")  # Triggers QMD check

        conflicts = self.find_surface_conflicts()

        results = {
            "total_conflicts": len(conflicts),
            "dry_run": dry_run,
            "qmd_available": self._qmd_available,
            "conflicts": [],
            "merge_candidates": [],
            "review_needed": [],
            "separate_recommendations": [],
        }

        for surface, entries in sorted(conflicts.items(), key=lambda x: -len(x[1])):
            unique_slugs = list({e[0] for e in entries})
            if len(unique_slugs) <= 1:
                continue

            # Get entry details
            entry_details = []
            for slug, title, source in entries:
                entry_details.append({
                    "slug": slug,
                    "title": title,
                    "source": source,
                })

            # Calculate pairwise QMD similarities (bidirectional)
            similarities = []
            for i, slug1 in enumerate(unique_slugs):
                title1 = next((e[1] for e in entries if e[0] == slug1), "")
                for slug2 in unique_slugs[i+1:]:
                    title2 = next((e[1] for e in entries if e[0] == slug2), "")
                    # Bidirectional: take max of both directions
                    score = self.get_bidirectional_similarity(title1, slug1, title2, slug2)
                    similarities.append({
                        "from": slug1,
                        "to": slug2,
                        "score": score,
                    })

            avg_similarity = sum(s["score"] for s in similarities) / len(similarities) if similarities else 0.0

            # Check for sentence-like titles
            sentence_like_entries = []
            for e in entry_details:
                is_sent, reason = self.is_sentence_like_title(e["title"])
                if is_sent:
                    sentence_like_entries.append({
                        "slug": e["slug"],
                        "title": e["title"],
                        "reason": reason,
                    })

            conflict_info = {
                "surface": surface,
                "entries": entry_details,
                "similarities": similarities,
                "avg_similarity": avg_similarity,
                "sentence_like_entries": sentence_like_entries,
            }

            # Determine action
            # If any title is sentence-like, mark as review_title regardless of similarity
            if sentence_like_entries:
                conflict_info["action"] = "review_title"
                results["review_needed"].append(conflict_info)
            elif avg_similarity >= min_similarity_for_merge:
                conflict_info["action"] = "review"
                results["review_needed"].append(conflict_info)
            else:
                conflict_info["action"] = "separate"
                # Identify which alias to remove from which entry
                conflict_info["recommendations"] = []
                for slug, title, source in entries:
                    if source in ("alias", "redirect"):
                        conflict_info["recommendations"].append({
                            "from_slug": slug,
                            "remove_surface": surface,
                            "reason": f"'{surface}' conflicts with other concepts (similarity={avg_similarity:.2f})"
                        })
                results["separate_recommendations"].append(conflict_info)

            results["conflicts"].append(conflict_info)

        # Execute fixes if not dry_run
        if not dry_run:
            # Apply separate recommendations
            for conflict in results["separate_recommendations"]:
                for rec in conflict.get("recommendations", []):
                    entry = self.find_by_slug(rec["from_slug"])
                    if entry and rec["remove_surface"] in entry.aliases:
                        entry.aliases.remove(rec["remove_surface"])
                        # Also remove from redirects if present
                        if rec["remove_surface"] in entry.redirects:
                            entry.redirects.remove(rec["remove_surface"])

            # Apply merge candidates
            for conflict in results["merge_candidates"]:
                target_slug = conflict["target_slug"]
                for entry_info in conflict["entries"]:
                    if entry_info["slug"] != target_slug and entry_info["source"] in ("alias", "redirect"):
                        source_entry = self.find_by_slug(entry_info["slug"])
                        if source_entry:
                            # Add the conflicting surface as alias to target
                            target_entry = self.find_by_slug(target_slug)
                            if target_entry and conflict["surface"] not in target_entry.aliases:
                                target_entry.aliases.append(conflict["surface"])

            self.save()

        return results

    def print_fix_report(self, results: dict[str, Any]) -> None:
        """Print a human-readable fix report."""
        print(f"\n{'='*60}")
        print("Registry Surface Fix Report")
        print(f"{'='*60}")
        print(f"Total conflicts: {results['total_conflicts']}")
        print(f"Mode: {'DRY-RUN' if results['dry_run'] else 'EXECUTING'}")
        print()

        if results["merge_candidates"]:
            print(f"{'='*60}")
            print(f"【MERGE CANDIDATES】(QMD similarity >= 0.8)")
            print(f"{'='*60}")
            for c in results["merge_candidates"]:
                print(f"\nSurface: '{c['surface']}'")
                print(f"  Entries:")
                for e in c["entries"]:
                    print(f"    - {e['slug']} ({e['source']})")
                print(f"  Avg QMD similarity: {c['avg_similarity']:.3f}")
                print(f"  Suggested target: {c['target_slug']}")

        if results["review_needed"]:
            # Separate review_title from review
            review_title = [c for c in results["review_needed"] if c.get("action") == "review_title"]
            review_similarity = [c for c in results["review_needed"] if c.get("action") == "review"]

            if review_title:
                print(f"\n{'='*60}")
                print(f"【REVIEW TITLE】(titles look like sentences, not concept names)")
                print(f"{'='*60}")
                for c in review_title:
                    print(f"\nSurface: '{c['surface']}'")
                    print(f"  Avg QMD similarity: {c['avg_similarity']:.3f}")
                    print(f"  Sentence-like entries:")
                    for e in c.get("sentence_like_entries", []):
                        print(f"    - {e['slug']}: {e['title'][:60]}")
                        print(f"      Reason: {e['reason']}")

            if review_similarity:
                print(f"\n{'='*60}")
                print(f"【REVIEW SIMILARITY】(0.5 <= QMD similarity < 0.8)")
                print(f"{'='*60}")
                for c in review_similarity:
                    print(f"\nSurface: '{c['surface']}'")
                    print(f"  Entries:")
                    for e in c["entries"]:
                        print(f"    - {e['slug']} ({e['source']})")
                    print(f"  Avg QMD similarity: {c['avg_similarity']:.3f}")

        if results["separate_recommendations"]:
            print(f"\n{'='*60}")
            print(f"【SEPARATE RECOMMENDATIONS】(QMD similarity < 0.5)")
            print(f"{'='*60}")
            for c in results["separate_recommendations"]:
                print(f"\nSurface: '{c['surface']}'")
                print(f"  Entries:")
                for e in c["entries"]:
                    print(f"    - {e['slug']} ({e['source']})")
                print(f"  Avg QMD similarity: {c['avg_similarity']:.3f}")
                print(f"  Recommendations:")
                for rec in c.get("recommendations", []):
                    print(f"    - Remove '{rec['remove_surface']}' from {rec['from_slug']}")

        if results["dry_run"]:
            print(f"\n{'='*60}")
            print("DRY-RUN: No changes made. Use --execute to apply fixes.")
            print(f"{'='*60}")

    # ========== Helpers ==========

    def _normalize(self, text: str) -> str:
        """Normalize text for alias matching (case-insensitive)."""
        return text.lower().strip()

    def _tokenize(self, text: str) -> set[str]:
        """Tokenize text for matching."""
        if not text:
            return set()
        tokens = re.findall(r'\w+', text.lower())
        return set(tokens)

    def _get_cached_tokens(self, text: str) -> frozenset[str]:
        """Get cached tokens for text, computing if not cached."""
        if text in self._token_cache:
            return self._token_cache[text]
        tokens = self._tokenize_mixed(text)
        self._token_cache[text] = frozenset(tokens)
        return self._token_cache[text]

    def _precompute_tokens(self) -> None:
        """Pre-compute tokens for all entries to speed up search."""
        self._token_cache = {}
        for entry in self._entries:
            self._get_cached_tokens(entry.slug)
            self._get_cached_tokens(entry.title)
            self._get_cached_tokens(entry.definition)
            for alias in entry.aliases:
                self._get_cached_tokens(alias)

    def _tokenize_mixed(self, text: str) -> set[str]:
        """Tokenize mixed Chinese/English text using jieba."""
        if not text:
            return set()

        tokens: set[str] = set()

        if JIEBA_AVAILABLE:
            for word in jieba.cut(text.lower()):
                if word.strip() and len(word) > 1:
                    tokens.add(word)

        english_tokens = re.findall(r'[a-zA-Z0-9]+', text.lower())
        tokens.update(english_tokens)

        tokens = {t for t in tokens if len(t) > 1 or re.match(r'[\u4e00-\u9fff]', t)}

        return tokens


# =============================================================================
# Helper Functions
# =============================================================================

def is_path_like_mention(mention: str) -> bool:
    """Check if a mention looks like a path (contains / or \\)."""
    return bool(_PATH_LIKE_RE.search(mention))


# =============================================================================
# Module-level helpers
# =============================================================================

def is_sentence_like_title(title: str) -> bool:
    """
    Check if a title looks like a sentence/description rather than a concept name.

    This is used to classify entries that should not participate in surface resolution.
    """
    import re

    # Sentence-like patterns
    sentence_patterns = [
        r"采用", r"实现", r"缺乏", r"通过", r"利用", r"基于",
        r"使用", r"提供", r"进行", r"完成", r"解决"
    ]
    english_sentence_patterns = [
        r" uses ", r" implements ", r" lacks ", r" achieves ",
        r" enables ", r" provides ", r" does ", r" how to ",
        r" a .* way to", r" to .* and .*", r" is a .* for"
    ]

    for pattern in sentence_patterns:
        if re.search(pattern, title):
            return True

    for pattern in english_sentence_patterns:
        if re.search(pattern, title.lower()):
            return True

    # Long titles with multiple clauses
    chinese_clause_markers = ["，", "。", "；", "、"]
    clause_count = sum(1 for m in chinese_clause_markers if m in title)
    if clause_count >= 2:
        return True

    # Long English sentences
    words = title.split()
    if len(words) > 15:
        action_verbs = ["is", "are", "was", "were", "does", "makes", "using"]
        if any(v in title.lower() for v in action_verbs):
            return True

    return False


def load_registry(vault_dir: Path) -> ConceptRegistry:
    """Convenience function to load registry."""
    return ConceptRegistry(vault_dir).load()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Concept Registry CLI")
    parser.add_argument("vault_dir", type=Path, help="Path to vault directory")
    parser.add_argument("--search", type=str, help="Search for a concept")
    parser.add_argument("--resolve", type=str, help="Resolve a mention")
    args = parser.parse_args()

    registry = ConceptRegistry(args.vault_dir).load()
    print(f"Loaded {len(registry.entries)} entries")

    if args.search:
        results = registry.search(args.search)
        print(f"\nSearch results for '{args.search}':")
        for entry, score in results:
            print(f"  {score:.3f} {entry.slug}: {entry.title}")

    if args.resolve:
        result = registry.resolve_mention(args.resolve)
        print(f"\nResolution for '{args.resolve}':")
        print(f"  Action: {result.action.value}")
        print(f"  Confidence: {result.confidence:.3f}")
        if result.entry:
            print(f"  Entry: {result.entry.slug}")
        if result.evidence:
            print(f"  Reason: {result.evidence[0].reason}")
