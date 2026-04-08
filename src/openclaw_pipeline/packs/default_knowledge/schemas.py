from __future__ import annotations

from ..base import ObjectKindSpec


DEFAULT_KNOWLEDGE_OBJECT_KINDS = [
    ObjectKindSpec(
        kind="concept",
        display_name="Concept",
        description="Canonical concept-like knowledge object",
        canonical=True,
    ),
    ObjectKindSpec(
        kind="entity",
        display_name="Entity",
        description="Named people, organizations, tools, or products",
        canonical=True,
    ),
    ObjectKindSpec(
        kind="evergreen",
        display_name="Evergreen",
        description="Reusable evergreen note in the default knowledge pack",
        canonical=True,
    ),
    ObjectKindSpec(
        kind="document",
        display_name="Document",
        description="Interpreted or raw document artifact tracked by the pack",
        canonical=False,
    ),
]
