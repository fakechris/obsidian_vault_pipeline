from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExtractionFieldSpec:
    name: str
    field_type: str
    description: str
    required: bool = False


@dataclass(frozen=True)
class ExtractionRelationSpec:
    name: str
    source_field: str
    target_field: str
    description: str = ""


@dataclass(frozen=True)
class GroundingPolicy:
    require_quote: bool = True
    include_char_offsets: bool = True
    include_section_title: bool = True


@dataclass(frozen=True)
class MergePolicy:
    strategy: str = "by_identifier"
    allow_partial_updates: bool = True


@dataclass(frozen=True)
class ProjectionTarget:
    object_kind: str
    channel: str
    target_name: str | None = None


@dataclass(frozen=True)
class ExtractionProfileSpec:
    name: str
    pack: str
    input_object_kinds: list[str]
    output_mode: str
    fields: list[ExtractionFieldSpec]
    relations: list[ExtractionRelationSpec] = field(default_factory=list)
    grounding_policy: GroundingPolicy = field(default_factory=GroundingPolicy)
    identifier_fields: list[str] = field(default_factory=list)
    merge_policy: MergePolicy = field(default_factory=MergePolicy)
    projection_target: ProjectionTarget = field(
        default_factory=lambda: ProjectionTarget(object_kind="document", channel="extraction")
    )
    display_fields: list[str] = field(default_factory=list)
    notes: str = ""
