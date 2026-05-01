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


def _projection_label_for_output(
    *,
    surface: str,
    projection_kind: str,
    layer: str,
    owner_pack: str,
    generated_by: str,
    derived_from: Iterable[str],
    rebuild_policy: str,
) -> dict[str, object]:
    return projection_label(
        surface=surface,
        projection_kind=projection_kind,
        layer=layer,
        owner_pack=owner_pack,
        generated_by=generated_by,
        derived_from=derived_from,
        rebuild_policy=rebuild_policy,
    )


def _format_projection_value(value: object, *, frontmatter: bool) -> str:
    if isinstance(value, list):
        joined = ", ".join(str(item) for item in value)
        return f"[{joined}]" if frontmatter else joined
    return str(value)


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
    label = _projection_label_for_output(
        surface=surface,
        projection_kind=projection_kind,
        layer=layer,
        owner_pack=owner_pack,
        generated_by=generated_by,
        derived_from=derived_from,
        rebuild_policy=rebuild_policy,
    )
    return [f"- {key}: {_format_projection_value(value, frontmatter=False)}" for key, value in label.items()]


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
    label = _projection_label_for_output(
        surface=surface,
        projection_kind=projection_kind,
        layer=layer,
        owner_pack=owner_pack,
        generated_by=generated_by,
        derived_from=derived_from,
        rebuild_policy=rebuild_policy,
    )
    return [f"{key}: {_format_projection_value(value, frontmatter=True)}" for key, value in label.items()]
