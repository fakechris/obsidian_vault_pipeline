from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExtractionSpan:
    source_path: str
    section_title: str
    char_start: int
    char_end: int
    quote: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "section_title": self.section_title,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "quote": self.quote,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ExtractionSpan":
        return cls(
            source_path=str(data.get("source_path") or ""),
            section_title=str(data.get("section_title") or ""),
            char_start=int(data.get("char_start") or 0),
            char_end=int(data.get("char_end") or 0),
            quote=str(data.get("quote") or ""),
        )


@dataclass
class ExtractionRecord:
    values: dict[str, object]
    spans: list[ExtractionSpan] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "values": self.values,
            "spans": [span.to_dict() for span in self.spans],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ExtractionRecord":
        return cls(
            values=dict(data.get("values") or {}),
            spans=[ExtractionSpan.from_dict(item) for item in data.get("spans", [])],
        )


@dataclass
class ExtractionRelation:
    relation_type: str
    source_id: str
    target_id: str
    spans: list[ExtractionSpan] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "relation_type": self.relation_type,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "spans": [span.to_dict() for span in self.spans],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ExtractionRelation":
        return cls(
            relation_type=str(data.get("relation_type") or ""),
            source_id=str(data.get("source_id") or ""),
            target_id=str(data.get("target_id") or ""),
            spans=[ExtractionSpan.from_dict(item) for item in data.get("spans", [])],
        )


@dataclass
class ExtractionRunResult:
    pack_name: str
    profile_name: str
    source_path: str
    records: list[ExtractionRecord] = field(default_factory=list)
    relations: list[ExtractionRelation] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "pack_name": self.pack_name,
            "profile_name": self.profile_name,
            "source_path": self.source_path,
            "records": [record.to_dict() for record in self.records],
            "relations": [relation.to_dict() for relation in self.relations],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ExtractionRunResult":
        return cls(
            pack_name=str(data.get("pack_name") or ""),
            profile_name=str(data.get("profile_name") or ""),
            source_path=str(data.get("source_path") or ""),
            records=[ExtractionRecord.from_dict(item) for item in data.get("records", [])],
            relations=[ExtractionRelation.from_dict(item) for item in data.get("relations", [])],
        )
