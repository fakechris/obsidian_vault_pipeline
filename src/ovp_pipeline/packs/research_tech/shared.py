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
from ...object_kinds import (
    ALL_OBJECT_KINDS,
    KIND_CLAIM,
    KIND_COMPANY,
    KIND_CONCEPT,
    KIND_DOCUMENT,
    KIND_ENTITY,
    KIND_EVENT,
    KIND_EVERGREEN,
    KIND_FRAMEWORK,
    KIND_METHOD,
    KIND_PAPER,
    KIND_PERSON,
    KIND_PROJECT,
    KIND_TOOL,
    OBJECT_KIND_LABELS,
)
from ..base import ObjectKindSpec, WorkflowProfile


def build_object_kinds() -> list[ObjectKindSpec]:
    return [
        ObjectKindSpec(
            kind=KIND_CONCEPT,
            display_name=OBJECT_KIND_LABELS[KIND_CONCEPT],
            description="Abstract idea, principle, or theory",
            canonical=True,
            reader_layout="concept_brief",
            extraction_hint="Abstract ideas, principles, theories, paradigms",
        ),
        ObjectKindSpec(
            kind=KIND_ENTITY,
            display_name=OBJECT_KIND_LABELS[KIND_ENTITY],
            description="Generic named entity",
            canonical=True,
            reader_layout="entity_brief",
            extraction_hint="Named entities that don't fit a more specific kind",
        ),
        ObjectKindSpec(
            kind=KIND_PERSON,
            display_name=OBJECT_KIND_LABELS[KIND_PERSON],
            description="Named individual",
            canonical=True,
            reader_layout="entity_brief",
            extraction_hint="Named individuals, researchers, founders, engineers",
        ),
        ObjectKindSpec(
            kind=KIND_COMPANY,
            display_name=OBJECT_KIND_LABELS[KIND_COMPANY],
            description="Named organization or company",
            canonical=True,
            reader_layout="entity_brief",
            extraction_hint="Organizations, companies, research labs, institutions",
        ),
        ObjectKindSpec(
            kind=KIND_TOOL,
            display_name=OBJECT_KIND_LABELS[KIND_TOOL],
            description="Software tool, library, framework, or product",
            canonical=True,
            reader_layout="entity_brief",
            extraction_hint="Software tools, libraries, frameworks, APIs, products",
        ),
        ObjectKindSpec(
            kind=KIND_PROJECT,
            display_name=OBJECT_KIND_LABELS[KIND_PROJECT],
            description="Named project or open-source repository",
            canonical=True,
            reader_layout="entity_brief",
            extraction_hint="Named projects, open-source repos, research initiatives",
        ),
        ObjectKindSpec(
            kind=KIND_PAPER,
            display_name=OBJECT_KIND_LABELS[KIND_PAPER],
            description="Research paper or publication",
            canonical=True,
            reader_layout="entity_brief",
            extraction_hint="Research papers, publications, preprints, academic works",
        ),
        ObjectKindSpec(
            kind=KIND_EVENT,
            display_name=OBJECT_KIND_LABELS[KIND_EVENT],
            description="Named event or dated occurrence",
            canonical=True,
            reader_layout="entity_brief",
            extraction_hint="Conferences, releases, announcements, dated milestones",
        ),
        ObjectKindSpec(
            kind=KIND_FRAMEWORK,
            display_name=OBJECT_KIND_LABELS[KIND_FRAMEWORK],
            description="Methodology, mental model, or analytical framework",
            canonical=True,
            reader_layout="concept_brief",
            extraction_hint="Methodologies, mental models, analytical frameworks",
        ),
        ObjectKindSpec(
            kind=KIND_METHOD,
            display_name=OBJECT_KIND_LABELS[KIND_METHOD],
            description="Specific technique, algorithm, or protocol",
            canonical=True,
            reader_layout="concept_brief",
            extraction_hint="Techniques, algorithms, protocols, specific methods",
        ),
        ObjectKindSpec(
            kind=KIND_EVERGREEN,
            display_name=OBJECT_KIND_LABELS[KIND_EVERGREEN],
            description="Reusable evergreen note (structural — not a valid entity_type)",
            canonical=False,
        ),
        ObjectKindSpec(
            kind=KIND_DOCUMENT,
            display_name=OBJECT_KIND_LABELS[KIND_DOCUMENT],
            description="Interpreted or raw document artifact",
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
                "entity_extract",
                "dedup",
                "note_type_normalize",
                "registry_sync",
                "moc",
                "knowledge_index",
                # BL-117: budgeted delta synthesis.  Reads the
                # truth/graph tables that ``knowledge_index`` just
                # wrote (especially the post-BL-115 ledger with
                # fresh ``current_cluster_id`` values) so the stale
                # detector compares against current membership.
                # Bounded by ``ovp-resynth-stale-crystals --max``;
                # a quiet vault makes zero LLM calls here.
                "synthesize",
                # M24.1: lifecycle projection.  Reads what
                # ``knowledge_index`` just rebuilt; must run
                # AFTER it.  Missing from the profile pre-M25.6
                # dogfood — caught when ``ops_state`` didn't
                # rebuild on the live operator vault even though
                # the step existed in BASE_PIPELINE_STEPS.
                "ops_state",
            ],
        ),
        WorkflowProfile(
            name="autopilot",
            description="Research-tech autopilot runtime",
            stages=[
                "interpretation",
                "quality",
                "absorb",
                "dedup",
                "moc",
                "knowledge_index",
                # BL-117: autopilot also needs delta synthesis so
                # the lifecycle ``Synthesized`` bucket stays in
                # sync with the actual crystal corpus.  Same
                # budget cap as the full profile.
                "synthesize",
                # M24.1: same reason as the full profile —
                # autopilot also needs a fresh lifecycle
                # projection at end of run.
                "ops_state",
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
            name="overview/clusters",
            pack=pack_name,
            purpose_path="90-Templates/purpose/topic.md",
            schema_path="90-Templates/schema/topic.md",
            input_sources=[],
            builder="cluster_view",
            traceability_policy=TraceabilityPolicy(include_sources=True, include_generated_from=True),
            publish_target="compiled_markdown",
        ),
        WikiViewSpec(
            name="cluster/crystal",
            pack=pack_name,
            purpose_path="90-Templates/purpose/topic.md",
            schema_path="90-Templates/schema/topic.md",
            input_sources=[],
            builder="cluster_crystal",
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
