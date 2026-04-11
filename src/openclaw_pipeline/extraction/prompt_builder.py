from __future__ import annotations

from .specs import ExtractionProfileSpec


def build_extraction_prompt(profile: ExtractionProfileSpec) -> str:
    field_names = ", ".join(field.name for field in profile.fields)
    return (
        f"Profile: {profile.name}\n"
        f"Output mode: {profile.output_mode}\n"
        f"Fields: {field_names}\n"
        f"Grounding required: {profile.grounding_policy.require_quote}\n"
    )
