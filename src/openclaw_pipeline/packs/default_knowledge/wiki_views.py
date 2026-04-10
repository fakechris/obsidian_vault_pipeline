from __future__ import annotations

from ...wiki_views.specs import TraceabilityPolicy, WikiViewInputSpec, WikiViewSpec


DEFAULT_WIKI_VIEWS = [
    WikiViewSpec(
        name="overview/domain",
        pack="default-knowledge",
        purpose_path="90-Templates/purpose/domain.md",
        schema_path="90-Templates/schema/domain.md",
        input_sources=[WikiViewInputSpec(source_kind="evergreen", description="Canonical evergreen notes")],
        traceability_policy=TraceabilityPolicy(include_sources=True, include_generated_from=True),
        publish_target="compiled_markdown",
    ),
    WikiViewSpec(
        name="overview/topic",
        pack="default-knowledge",
        purpose_path="90-Templates/purpose/topic.md",
        schema_path="90-Templates/schema/topic.md",
        input_sources=[WikiViewInputSpec(source_kind="evergreen", description="Topic-level evergreen notes")],
        traceability_policy=TraceabilityPolicy(include_sources=True, include_generated_from=True),
        publish_target="compiled_markdown",
    ),
    WikiViewSpec(
        name="saved_answer/query",
        pack="default-knowledge",
        purpose_path="90-Templates/purpose/saved-answer.md",
        schema_path="90-Templates/schema/saved-answer.md",
        input_sources=[WikiViewInputSpec(source_kind="query", description="Saved query outputs")],
        traceability_policy=TraceabilityPolicy(include_sources=True, include_generated_from=True),
        publish_target="compiled_markdown",
    ),
]
