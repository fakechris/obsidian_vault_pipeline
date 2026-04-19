from __future__ import annotations

from ...extraction.specs import (
    ExtractionFieldSpec,
    ExtractionProfileSpec,
    GroundingPolicy,
    MergePolicy,
    ProjectionTarget,
)
from ..research_tech.shared import build_tech_extraction_profiles


MEDIA_EXTRACTION_PROFILES = [
    ExtractionProfileSpec(
        name="media/news_timeline",
        pack="default-knowledge",
        input_object_kinds=["document"],
        output_mode="record_list",
        fields=[
            ExtractionFieldSpec("event_type", "string", "Type of event"),
            ExtractionFieldSpec("actors", "string_list", "People or organizations involved"),
            ExtractionFieldSpec("when", "string", "When the event happened"),
            ExtractionFieldSpec("claim", "string", "Core event statement", required=True),
            ExtractionFieldSpec("impact", "string", "Why it matters"),
        ],
        identifier_fields=["claim", "when"],
        grounding_policy=GroundingPolicy(require_quote=True, include_char_offsets=True),
        merge_policy=MergePolicy(strategy="by_identifier", allow_partial_updates=True),
        projection_target=ProjectionTarget(object_kind="document", channel="extraction"),
        display_fields=["event_type", "when", "claim"],
        notes="Loosely adapted from Hyper-Extract event timeline ideas.",
    ),
    ExtractionProfileSpec(
        name="media/commentary_sentiment",
        pack="default-knowledge",
        input_object_kinds=["document"],
        output_mode="record_list",
        fields=[
            ExtractionFieldSpec("subject", "string", "Primary subject of commentary", required=True),
            ExtractionFieldSpec("stance", "string", "Overall stance or tone"),
            ExtractionFieldSpec("sentiment_score", "number", "Normalized sentiment score"),
            ExtractionFieldSpec("thesis", "string", "Main opinion or framing"),
        ],
        identifier_fields=["subject", "thesis"],
        grounding_policy=GroundingPolicy(require_quote=True, include_char_offsets=True),
        merge_policy=MergePolicy(strategy="by_identifier", allow_partial_updates=True),
        projection_target=ProjectionTarget(object_kind="document", channel="extraction"),
        display_fields=["subject", "stance", "sentiment_score"],
        notes="Moderately adapted from Hyper-Extract sentiment model ideas.",
    ),
]


DEFAULT_EXTRACTION_PROFILES = build_tech_extraction_profiles("default-knowledge") + MEDIA_EXTRACTION_PROFILES
