from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .packs.loader import DEFAULT_PACK_NAME

if TYPE_CHECKING:
    from .concept_registry import ConceptEntry, ConceptRegistry


DEFAULT_OBJECT_PACK = DEFAULT_PACK_NAME


@dataclass(frozen=True)
class ObjectRecord:
    id: str
    kind: str
    pack: str
    title: str
    status: str
    aliases: tuple[str, ...] = ()
    area: str | None = None
    canonical_ref: str | None = None
    meta: dict[str, object] = field(default_factory=dict)


@dataclass
class ObjectRegistry:
    _records: list[ObjectRecord]

    def records(self) -> list[ObjectRecord]:
        return list(self._records)

    def find_by_id(self, object_id: str) -> ObjectRecord | None:
        for record in self._records:
            if record.id == object_id:
                return record
        return None

    @classmethod
    def from_concept_registry(
        cls,
        registry: ConceptRegistry,
        *,
        pack: str = DEFAULT_OBJECT_PACK,
    ) -> ObjectRegistry:
        return cls([record_from_concept_entry(entry, pack=pack) for entry in registry.entries])


def record_from_concept_entry(
    entry: ConceptEntry,
    *,
    pack: str = DEFAULT_OBJECT_PACK,
) -> ObjectRecord:
    return ObjectRecord(
        id=entry.slug,
        kind=entry.kind,
        pack=pack,
        title=entry.title,
        status=entry.status,
        aliases=tuple(entry.aliases),
        area=entry.area,
        canonical_ref=entry.slug,
        meta={
            "resolver_enabled": entry.resolver_enabled,
            "source_count": entry.source_count,
            "evidence_count": entry.evidence_count,
        },
    )
