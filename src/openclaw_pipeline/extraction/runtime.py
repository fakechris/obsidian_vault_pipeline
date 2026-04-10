from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .results import ExtractionRecord, ExtractionRunResult
from .specs import ExtractionProfileSpec


class ExtractionRuntime:
    def __init__(self, *, extractor: object, chunk_size: int = 2048, overlap: int = 256):
        self.extractor = extractor
        self.chunk_size = chunk_size
        self.overlap = overlap

    def run_text(
        self,
        *,
        profile: ExtractionProfileSpec,
        text: str,
        source_path: Path,
    ) -> ExtractionRunResult:
        records: list[ExtractionRecord] = []
        for chunk_index, chunk_text in enumerate(self._chunk_text(text)):
            records.extend(
                self.extractor.extract(
                    chunk_text,
                    chunk_index=chunk_index,
                    source_path=source_path,
                    profile=profile,
                )
            )
        return ExtractionRunResult(
            pack_name=profile.pack,
            profile_name=profile.name,
            source_path=str(source_path),
            records=self._merge_records(records, profile),
        )

    def _chunk_text(self, text: str) -> Iterable[str]:
        if len(text) <= self.chunk_size:
            yield text
            return

        step = max(self.chunk_size - self.overlap, 1)
        for start in range(0, len(text), step):
            chunk = text[start:start + self.chunk_size]
            if chunk:
                yield chunk

    def _merge_records(
        self,
        records: list[ExtractionRecord],
        profile: ExtractionProfileSpec,
    ) -> list[ExtractionRecord]:
        if not profile.identifier_fields:
            return records

        merged: dict[tuple[object, ...], ExtractionRecord] = {}
        ordered_keys: list[tuple[object, ...]] = []

        for record in records:
            key = tuple(record.values.get(field) for field in profile.identifier_fields)
            if key not in merged:
                merged[key] = ExtractionRecord(values=dict(record.values), spans=list(record.spans))
                ordered_keys.append(key)
                continue

            current = merged[key]
            for field_name, value in record.values.items():
                if value not in (None, "", [], {}):
                    current.values[field_name] = value
            current.spans.extend(record.spans)

        return [merged[key] for key in ordered_keys]
