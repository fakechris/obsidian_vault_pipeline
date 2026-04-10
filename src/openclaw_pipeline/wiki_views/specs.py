from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TraceabilityPolicy:
    include_sources: bool = True
    include_generated_from: bool = True


@dataclass(frozen=True)
class WikiViewInputSpec:
    source_kind: str
    description: str


@dataclass(frozen=True)
class WikiViewSpec:
    name: str
    pack: str
    purpose_path: str
    schema_path: str
    input_sources: list[WikiViewInputSpec] = field(default_factory=list)
    builder: str = "compiled_markdown"
    traceability_policy: TraceabilityPolicy = field(default_factory=TraceabilityPolicy)
    publish_target: str = "compiled_markdown"
