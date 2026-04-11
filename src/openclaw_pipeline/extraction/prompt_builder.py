from __future__ import annotations

from .specs import ExtractionProfileSpec


def build_extraction_prompt(profile: ExtractionProfileSpec) -> str:
    field_lines = [
        f"- {field.name} (required: {field.required}; type: {field.field_type}; span_required: {profile.grounding_policy.require_quote})"
        for field in profile.fields
    ]
    relation_names = ", ".join(relation.name for relation in profile.relations) or "(none)"
    return (
        f"Profile: {profile.name}\n"
        f"Output mode: {profile.output_mode}\n"
        "Fields:\n"
        f"{chr(10).join(field_lines)}\n"
        f"Relations: {relation_names}\n"
        f"Grounding required: {profile.grounding_policy.require_quote}\n"
    )
