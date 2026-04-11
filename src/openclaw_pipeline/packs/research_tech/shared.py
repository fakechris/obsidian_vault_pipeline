from __future__ import annotations

from ...extraction.specs import (
    ExtractionFieldSpec,
    ExtractionProfileSpec,
    GroundingPolicy,
    MergePolicy,
    ProjectionTarget,
)
from ...operations.specs import OperationCheckSpec, OperationProfileSpec, OperationProposalSpec
from ...wiki_views.specs import TraceabilityPolicy, WikiViewInputSpec, WikiViewSpec
from ..base import ObjectKindSpec, WorkflowProfile


def build_object_kinds() -> list[ObjectKindSpec]:
    return [
        ObjectKindSpec(
            kind="concept",
            display_name="Concept",
            description="Canonical concept-like knowledge object",
            canonical=True,
        ),
        ObjectKindSpec(
            kind="entity",
            display_name="Entity",
            description="Named people, organizations, tools, or products",
            canonical=True,
        ),
        ObjectKindSpec(
            kind="evergreen",
            display_name="Evergreen",
            description="Reusable evergreen note in the research/knowledge pack",
            canonical=True,
        ),
        ObjectKindSpec(
            kind="document",
            display_name="Document",
            description="Interpreted or raw document artifact tracked by the pack",
            canonical=False,
        ),
    ]


def build_workflow_profiles() -> list[WorkflowProfile]:
    return [
        WorkflowProfile(
            name="full",
            description="Research-tech full pipeline",
            stages=[
                "pinboard",
                "pinboard_process",
                "clippings",
                "articles",
                "quality",
                "fix_links",
                "absorb",
                "registry_sync",
                "moc",
                "knowledge_index",
            ],
        ),
        WorkflowProfile(
            name="autopilot",
            description="Research-tech autopilot runtime",
            stages=[
                "interpretation",
                "quality",
                "absorb",
                "moc",
                "knowledge_index",
            ],
            supports_autopilot=True,
        ),
    ]


def build_tech_extraction_profiles(pack_name: str) -> list[ExtractionProfileSpec]:
    return [
        ExtractionProfileSpec(
            name="tech/doc_structure",
            pack=pack_name,
            input_object_kinds=["document"],
            output_mode="record_list",
            fields=[
                ExtractionFieldSpec("section_title", "string", "Heading text", required=True),
                ExtractionFieldSpec("section_kind", "string", "Body, appendix, code, table, figure"),
                ExtractionFieldSpec("summary", "string", "Short summary of section content"),
                ExtractionFieldSpec("references", "string_list", "Cross references found in the section"),
            ],
            identifier_fields=["section_title"],
            grounding_policy=GroundingPolicy(require_quote=True, include_char_offsets=True),
            merge_policy=MergePolicy(strategy="by_identifier", allow_partial_updates=True),
            projection_target=ProjectionTarget(object_kind="document", channel="extraction"),
            display_fields=["section_title", "section_kind", "summary"],
            notes="Heavily inspired by Hyper-Extract general/doc_structure.",
        ),
        ExtractionProfileSpec(
            name="tech/workflow_graph",
            pack=pack_name,
            input_object_kinds=["document"],
            output_mode="graph",
            fields=[
                ExtractionFieldSpec("step_name", "string", "Workflow step name", required=True),
                ExtractionFieldSpec("step_kind", "string", "Action, decision, input, output"),
                ExtractionFieldSpec("depends_on", "string_list", "Prerequisite step names"),
                ExtractionFieldSpec("produces", "string_list", "Artifacts or outputs produced"),
            ],
            identifier_fields=["step_name"],
            grounding_policy=GroundingPolicy(require_quote=True, include_char_offsets=True),
            merge_policy=MergePolicy(strategy="by_identifier", allow_partial_updates=True),
            projection_target=ProjectionTarget(object_kind="document", channel="extraction"),
            display_fields=["step_name", "step_kind"],
            notes="Heavily inspired by Hyper-Extract general/workflow_graph.",
        ),
    ]


def build_operation_profiles(pack_name: str) -> list[OperationProfileSpec]:
    return [
        OperationProfileSpec(
            name="vault/frontmatter_audit",
            pack=pack_name,
            scope="vault",
            triggers=["manual", "pre-refine"],
            checks=[
                OperationCheckSpec(
                    name="required-frontmatter",
                    description="Ensure title and note metadata exist",
                )
            ],
            proposal_types=[OperationProposalSpec(proposal_type="frontmatter_fix", queue_name="frontmatter")],
            auto_fix_policy="manual",
            review_required=True,
        ),
        OperationProfileSpec(
            name="vault/review_queue",
            pack=pack_name,
            scope="vault",
            triggers=["manual"],
            checks=[OperationCheckSpec(name="queue-health", description="Inspect pending review items")],
            proposal_types=[OperationProposalSpec(proposal_type="queue_review", queue_name="review")],
            auto_fix_policy="manual",
            review_required=True,
        ),
        OperationProfileSpec(
            name="vault/bridge_recommendations",
            pack=pack_name,
            scope="vault",
            triggers=["manual", "post-absorb"],
            checks=[OperationCheckSpec(name="bridge-gaps", description="Suggest cross-note bridge candidates")],
            proposal_types=[OperationProposalSpec(proposal_type="bridge_note", queue_name="bridges")],
            auto_fix_policy="manual",
            review_required=True,
        ),
        OperationProfileSpec(
            name="truth/contradiction_review",
            pack=pack_name,
            scope="truth",
            triggers=["manual", "post-absorb"],
            checks=[
                OperationCheckSpec(
                    name="contradiction-scan",
                    description="Inspect truth-store claims for conflicts",
                )
            ],
            proposal_types=[OperationProposalSpec(proposal_type="truth_contradiction", queue_name="contradictions")],
            auto_fix_policy="manual",
            review_required=True,
        ),
        OperationProfileSpec(
            name="truth/stale_summary_review",
            pack=pack_name,
            scope="truth",
            triggers=["manual", "post-absorb"],
            checks=[OperationCheckSpec(name="stale-summary-scan", description="Inspect weak compiled summaries")],
            proposal_types=[OperationProposalSpec(proposal_type="stale_summary", queue_name="stale-summaries")],
            auto_fix_policy="manual",
            review_required=True,
        ),
    ]


def build_wiki_views(pack_name: str) -> list[WikiViewSpec]:
    return [
        WikiViewSpec(
            name="overview/domain",
            pack=pack_name,
            purpose_path="90-Templates/purpose/domain.md",
            schema_path="90-Templates/schema/domain.md",
            input_sources=[WikiViewInputSpec(source_kind="evergreen", description="Canonical evergreen notes")],
            traceability_policy=TraceabilityPolicy(include_sources=True, include_generated_from=True),
            publish_target="compiled_markdown",
        ),
        WikiViewSpec(
            name="overview/topic",
            pack=pack_name,
            purpose_path="90-Templates/purpose/topic.md",
            schema_path="90-Templates/schema/topic.md",
            input_sources=[WikiViewInputSpec(source_kind="evergreen", description="Topic-level evergreen notes")],
            builder="topic_view",
            traceability_policy=TraceabilityPolicy(include_sources=True, include_generated_from=True),
            publish_target="compiled_markdown",
        ),
        WikiViewSpec(
            name="saved_answer/query",
            pack=pack_name,
            purpose_path="90-Templates/purpose/saved-answer.md",
            schema_path="90-Templates/schema/saved-answer.md",
            input_sources=[WikiViewInputSpec(source_kind="query", description="Saved query outputs")],
            traceability_policy=TraceabilityPolicy(include_sources=True, include_generated_from=True),
            publish_target="compiled_markdown",
        ),
        WikiViewSpec(
            name="overview/extraction",
            pack=pack_name,
            purpose_path="90-Templates/purpose/extraction.md",
            schema_path="90-Templates/schema/extraction.md",
            input_sources=[WikiViewInputSpec(source_kind="extraction", description="Derived extraction run artifacts")],
            traceability_policy=TraceabilityPolicy(include_sources=True, include_generated_from=True),
            publish_target="compiled_markdown",
        ),
        WikiViewSpec(
            name="object/page",
            pack=pack_name,
            purpose_path="90-Templates/purpose/object-page.md",
            schema_path="90-Templates/schema/object-page.md",
            input_sources=[],
            builder="object_page",
            traceability_policy=TraceabilityPolicy(include_sources=True, include_generated_from=True),
            publish_target="compiled_markdown",
        ),
        WikiViewSpec(
            name="event/dossier",
            pack=pack_name,
            purpose_path="90-Templates/purpose/event-dossier.md",
            schema_path="90-Templates/schema/event-dossier.md",
            input_sources=[],
            builder="event_dossier",
            traceability_policy=TraceabilityPolicy(include_sources=True, include_generated_from=True),
            publish_target="compiled_markdown",
        ),
        WikiViewSpec(
            name="truth/contradictions",
            pack=pack_name,
            purpose_path="90-Templates/purpose/contradictions.md",
            schema_path="90-Templates/schema/contradictions.md",
            input_sources=[],
            builder="contradiction_view",
            traceability_policy=TraceabilityPolicy(include_sources=True, include_generated_from=True),
            publish_target="compiled_markdown",
        ),
    ]
