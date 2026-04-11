from __future__ import annotations

from .results import ExtractionRecord
from .specs import ExtractionProfileSpec


def validate_record(profile: ExtractionProfileSpec, record: ExtractionRecord) -> bool:
    for field in profile.fields:
        if field.required and record.values.get(field.name) in (None, "", [], {}):
            return False
    if profile.grounding_policy.require_quote and not record.spans:
        return False
    return True


def filter_valid_records(profile: ExtractionProfileSpec, records: list[ExtractionRecord]) -> list[ExtractionRecord]:
    return [record for record in records if validate_record(profile, record)]
