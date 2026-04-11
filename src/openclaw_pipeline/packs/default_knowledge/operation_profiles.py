from __future__ import annotations

from ...operations.specs import OperationCheckSpec, OperationProfileSpec, OperationProposalSpec


DEFAULT_OPERATION_PROFILES = [
    OperationProfileSpec(
        name="vault/frontmatter_audit",
        pack="default-knowledge",
        scope="vault",
        triggers=["manual", "pre-refine"],
        checks=[OperationCheckSpec(name="required-frontmatter", description="Ensure title and note metadata exist")],
        proposal_types=[OperationProposalSpec(proposal_type="frontmatter_fix", queue_name="frontmatter")],
        auto_fix_policy="manual",
        review_required=True,
    ),
    OperationProfileSpec(
        name="vault/review_queue",
        pack="default-knowledge",
        scope="vault",
        triggers=["manual"],
        checks=[OperationCheckSpec(name="queue-health", description="Inspect pending review items")],
        proposal_types=[OperationProposalSpec(proposal_type="queue_review", queue_name="review")],
        auto_fix_policy="manual",
        review_required=True,
    ),
    OperationProfileSpec(
        name="vault/bridge_recommendations",
        pack="default-knowledge",
        scope="vault",
        triggers=["manual", "post-absorb"],
        checks=[OperationCheckSpec(name="bridge-gaps", description="Suggest cross-note bridge candidates")],
        proposal_types=[OperationProposalSpec(proposal_type="bridge_note", queue_name="bridges")],
        auto_fix_policy="manual",
        review_required=True,
    ),
    OperationProfileSpec(
        name="truth/contradiction_review",
        pack="default-knowledge",
        scope="truth",
        triggers=["manual", "post-absorb"],
        checks=[OperationCheckSpec(name="contradiction-scan", description="Inspect truth-store claims for conflicts")],
        proposal_types=[OperationProposalSpec(proposal_type="truth_contradiction", queue_name="contradictions")],
        auto_fix_policy="manual",
        review_required=True,
    ),
    OperationProfileSpec(
        name="truth/stale_summary_review",
        pack="default-knowledge",
        scope="truth",
        triggers=["manual", "post-absorb"],
        checks=[OperationCheckSpec(name="stale-summary-scan", description="Inspect weak compiled summaries")],
        proposal_types=[OperationProposalSpec(proposal_type="stale_summary", queue_name="stale-summaries")],
        auto_fix_policy="manual",
        review_required=True,
    ),
]
