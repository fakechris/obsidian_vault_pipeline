"""Canonical Object Kind Taxonomy for OVP.

This module is the single source of truth for object kind constants.
All subsystems — concept_registry, truth_store, view_models, packs,
semantic_relations — must import from here rather than defining their
own string literals.

The taxonomy is intentionally small (< 15 kinds).  Packs may extend it
via ``object_kind_specs()`` but the canonical set is fixed here so that
Layer 1 frontmatter, Layer 2 indexes, and Layer 3 UI all speak the same
vocabulary.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Core knowledge object kinds
# ---------------------------------------------------------------------------

KIND_CONCEPT = "concept"
"""Abstract idea, principle, or theory."""

KIND_ENTITY = "entity"
"""Generic named entity (fallback when a more specific kind does not apply)."""

KIND_PERSON = "person"
"""Named individual."""

KIND_COMPANY = "company"
"""Named organization or company."""

KIND_TOOL = "tool"
"""Software tool, library, framework, or product."""

KIND_PROJECT = "project"
"""Named project, initiative, or open-source repository."""

KIND_PAPER = "paper"
"""Research paper, publication, or academic work."""

KIND_EVENT = "event"
"""Named event, conference, or dated occurrence."""

KIND_FRAMEWORK = "framework"
"""Methodology, mental model, or analytical framework."""

KIND_METHOD = "method"
"""Specific technique, algorithm, or protocol."""

# ---------------------------------------------------------------------------
# Structural / meta kinds
# ---------------------------------------------------------------------------

KIND_EVERGREEN = "evergreen"
"""Reusable Evergreen note (superset — may carry any core kind)."""

KIND_DOCUMENT = "document"
"""Interpreted or raw document artifact."""

KIND_CLAIM = "claim"
"""Discrete assertion or proposition extracted from a source."""

# ---------------------------------------------------------------------------
# Aggregated sets
# ---------------------------------------------------------------------------

CORE_OBJECT_KINDS: frozenset[str] = frozenset(
    {
        KIND_CONCEPT,
        KIND_ENTITY,
        KIND_PERSON,
        KIND_COMPANY,
        KIND_TOOL,
        KIND_PROJECT,
        KIND_PAPER,
        KIND_EVENT,
        KIND_FRAMEWORK,
        KIND_METHOD,
    }
)
"""Kinds that represent real-world knowledge objects (used for entity_type)."""

STRUCTURAL_OBJECT_KINDS: frozenset[str] = frozenset(
    {
        KIND_EVERGREEN,
        KIND_DOCUMENT,
        KIND_CLAIM,
    }
)
"""Kinds that represent OVP-internal structural roles."""

ALL_OBJECT_KINDS: frozenset[str] = CORE_OBJECT_KINDS | STRUCTURAL_OBJECT_KINDS
"""Every recognized object kind."""

# Convenience tuple for pack semantic relation contracts.
RELATABLE_OBJECT_KINDS: tuple[str, ...] = tuple(sorted(CORE_OBJECT_KINDS))
"""Object kinds that may participate in semantic relations."""

# ---------------------------------------------------------------------------
# Registry-level kind mapping (concept_registry backwards compatibility)
# ---------------------------------------------------------------------------

REGISTRY_VALID_KINDS: frozenset[str] = frozenset(
    {
        KIND_ENTITY,
        KIND_CONCEPT,
        KIND_FRAMEWORK,
        KIND_METHOD,
        KIND_PERSON,
        KIND_COMPANY,
        KIND_TOOL,
        KIND_PROJECT,
        KIND_PAPER,
        KIND_EVENT,
    }
)
"""Kinds valid for ConceptEntry.kind (excludes structural kinds)."""

# Legacy aliases kept for backwards compatibility with existing registry data.
KIND_PROTOCOL = "protocol"
KIND_PROPOSITION = "proposition"
KIND_CASE = "case"

LEGACY_KIND_MAP: dict[str, str] = {
    KIND_PROTOCOL: KIND_METHOD,
    KIND_PROPOSITION: KIND_CONCEPT,
    KIND_CASE: KIND_CONCEPT,
}
"""Map legacy kind values to their canonical replacements."""


def normalize_kind(kind: str) -> str:
    """Normalize an object kind string to its canonical form.

    Handles legacy aliases and case normalization.
    """
    k = kind.strip().lower()
    return LEGACY_KIND_MAP.get(k, k)


# ---------------------------------------------------------------------------
# UI display labels (used by view_models and reader profiles)
# ---------------------------------------------------------------------------

OBJECT_KIND_LABELS: dict[str, str] = {
    KIND_PERSON: "Person",
    KIND_CONCEPT: "Concept",
    KIND_COMPANY: "Company",
    KIND_TOOL: "Tool",
    KIND_PROJECT: "Project",
    KIND_EVENT: "Event",
    KIND_PAPER: "Paper",
    KIND_FRAMEWORK: "Framework",
    KIND_METHOD: "Method",
    KIND_ENTITY: "Entity",
    KIND_CLAIM: "Claim",
    KIND_EVERGREEN: "Concept",
    KIND_DOCUMENT: "Document",
}


def display_label(kind: str) -> str:
    """Return a human-readable label for an object kind."""
    normalized = normalize_kind(kind)
    return OBJECT_KIND_LABELS.get(normalized, normalized.replace("_", " ").title())
