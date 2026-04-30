from __future__ import annotations

from typing import Iterable

PROJECTION_LABEL_SCHEMA_VERSION = 1
PROJECTION_AUTHORITY_BOUNDARY = "derived_not_authority"


def projection_label(
    *,
    surface: str,
    projection_kind: str,
    layer: str,
    owner_pack: str,
    generated_by: str,
    derived_from: Iterable[str],
    rebuild_policy: str,
) -> dict[str, object]:
    return {
        "projection_schema_version": PROJECTION_LABEL_SCHEMA_VERSION,
        "projection_kind": projection_kind,
        "projection_surface": surface,
        "projection_layer": layer,
        "projection_owner_pack": owner_pack,
        "projection_generated_by": generated_by,
        "projection_derived_from": list(derived_from),
        "projection_rebuild_policy": rebuild_policy,
        "projection_authority_boundary": PROJECTION_AUTHORITY_BOUNDARY,
    }


def markdown_projection_lines(
    *,
    surface: str,
    projection_kind: str,
    layer: str = "Layer 3",
    owner_pack: str,
    generated_by: str,
    derived_from: Iterable[str],
    rebuild_policy: str,
) -> list[str]:
    return [
        f"- projection_schema_version: {PROJECTION_LABEL_SCHEMA_VERSION}",
        f"- projection_kind: {projection_kind}",
        f"- projection_surface: {surface}",
        f"- projection_layer: {layer}",
        f"- projection_owner_pack: {owner_pack}",
        f"- projection_generated_by: {generated_by}",
        f"- projection_derived_from: {', '.join(derived_from)}",
        f"- projection_rebuild_policy: {rebuild_policy}",
        f"- projection_authority_boundary: {PROJECTION_AUTHORITY_BOUNDARY}",
    ]


def frontmatter_projection_fields(
    *,
    surface: str,
    projection_kind: str,
    layer: str = "Layer 3",
    owner_pack: str,
    generated_by: str,
    derived_from: Iterable[str],
    rebuild_policy: str,
) -> list[str]:
    return [
        f"projection_schema_version: {PROJECTION_LABEL_SCHEMA_VERSION}",
        f"projection_kind: {projection_kind}",
        f"projection_surface: {surface}",
        f"projection_layer: {layer}",
        f"projection_owner_pack: {owner_pack}",
        f"projection_generated_by: {generated_by}",
        f"projection_derived_from: [{', '.join(derived_from)}]",
        f"projection_rebuild_policy: {rebuild_policy}",
        f"projection_authority_boundary: {PROJECTION_AUTHORITY_BOUNDARY}",
    ]
