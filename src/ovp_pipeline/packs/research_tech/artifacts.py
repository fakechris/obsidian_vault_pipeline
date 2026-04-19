from __future__ import annotations

from ..base import (
    ArtifactEvidencePolicy,
    ArtifactFieldSpec,
    ArtifactIdentityPolicy,
    ArtifactLifecyclePolicy,
    ArtifactSpec,
    ArtifactStoragePolicy,
)


def build_artifact_specs(pack_name: str = "research-tech") -> list[ArtifactSpec]:
    return [
        ArtifactSpec(
            name="canonical_object",
            pack=pack_name,
            layer="canonical",
            family="object",
            object_kind="evergreen",
            description="Canonical durable knowledge object tracked by the pack.",
            fields=[
                ArtifactFieldSpec("object_id", "string", "Deterministic object identifier", required=True),
                ArtifactFieldSpec("title", "string", "Human-readable object title", required=True),
                ArtifactFieldSpec("canonical_path", "path", "Canonical markdown note path", required=True),
            ],
            identity_policy=ArtifactIdentityPolicy(
                id_strategy="deterministic",
                id_fields=["object_id"],
                subject_fields=["title"],
            ),
            evidence_policy=ArtifactEvidencePolicy(
                requires_evidence=True,
                require_quote=False,
                require_source_slug=True,
                require_traceability_links=True,
            ),
            storage_policy=ArtifactStoragePolicy(
                storage_mode="markdown_note",
                canonical_path_template="10-Knowledge/Evergreen/{object_id}.md",
                truth_row_family="objects",
            ),
        ),
        ArtifactSpec(
            name="canonical_claim",
            pack=pack_name,
            layer="canonical",
            family="claim",
            description="Structured claim attached to a canonical object.",
            fields=[
                ArtifactFieldSpec("claim_id", "string", "Deterministic claim identifier", required=True),
                ArtifactFieldSpec("object_id", "string", "Owning object identifier", required=True),
                ArtifactFieldSpec("claim_text", "string", "Canonicalized claim text", required=True),
            ],
            identity_policy=ArtifactIdentityPolicy(
                id_strategy="deterministic",
                id_fields=["claim_id"],
                subject_fields=["object_id", "claim_text"],
            ),
            storage_policy=ArtifactStoragePolicy(
                storage_mode="truth_projection_row",
                truth_row_family="claims",
            ),
        ),
        ArtifactSpec(
            name="claim_evidence",
            pack=pack_name,
            layer="canonical",
            family="evidence",
            description="Source-grounded evidence row supporting a claim.",
            fields=[
                ArtifactFieldSpec("claim_id", "string", "Claim identifier", required=True),
                ArtifactFieldSpec("source_slug", "string", "Source note slug", required=True),
                ArtifactFieldSpec("quote_text", "string", "Quoted support text", required=True),
            ],
            identity_policy=ArtifactIdentityPolicy(
                id_strategy="compound",
                id_fields=["claim_id", "source_slug", "quote_text"],
                subject_fields=["claim_id"],
            ),
            storage_policy=ArtifactStoragePolicy(
                storage_mode="truth_projection_row",
                truth_row_family="claim_evidence",
            ),
        ),
        ArtifactSpec(
            name="compiled_overview",
            pack=pack_name,
            layer="access",
            family="overview",
            description="Pack-owned compiled overview artifact for reading and export.",
            fields=[
                ArtifactFieldSpec("target_kind", "string", "Overview target kind", required=True),
                ArtifactFieldSpec("target_ref", "string", "Target object/topic/event identifier", required=True),
                ArtifactFieldSpec("summary_text", "string", "Compiled overview text", required=True),
            ],
            evidence_policy=ArtifactEvidencePolicy(
                requires_evidence=False,
                require_quote=False,
                require_source_slug=False,
                require_traceability_links=True,
            ),
            storage_policy=ArtifactStoragePolicy(
                storage_mode="compiled_markdown",
                canonical_path_template="60-Logs/derived/compiled-views/{target_kind}/{target_ref}.md",
                truth_row_family="compiled_summaries",
            ),
            lifecycle_policy=ArtifactLifecyclePolicy(
                mutable=True,
                review_required_on_create=False,
                review_required_on_update=False,
                projection_rebuild_policy="on_demand_or_refresh",
            ),
        ),
        ArtifactSpec(
            name="review_item",
            pack=pack_name,
            layer="governance",
            family="review_item",
            description="Review queue artifact emitted by maintenance and truth checks.",
            fields=[
                ArtifactFieldSpec("queue_name", "string", "Owning review queue", required=True),
                ArtifactFieldSpec("issue_type", "string", "Typed issue category", required=True),
                ArtifactFieldSpec("message", "string", "Operator-facing review summary", required=True),
            ],
            evidence_policy=ArtifactEvidencePolicy(
                requires_evidence=False,
                require_quote=False,
                require_source_slug=False,
                require_traceability_links=True,
            ),
            storage_policy=ArtifactStoragePolicy(
                storage_mode="review_queue_artifact",
                canonical_path_template="60-Logs/derived/review-queue/{queue_name}/{subject}.json",
                review_queue_name="review",
            ),
            lifecycle_policy=ArtifactLifecyclePolicy(
                mutable=True,
                review_required_on_create=True,
                review_required_on_update=True,
                projection_rebuild_policy="none",
            ),
        ),
    ]
