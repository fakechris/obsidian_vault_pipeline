from __future__ import annotations

from ..base import GovernanceSpec, ResolverRuleSpec, ReviewQueueSpec, SignalRuleSpec


def build_governance_specs(pack_name: str = "research-tech") -> list[GovernanceSpec]:
    return [
        GovernanceSpec(
            name="research_governance",
            pack=pack_name,
            description=(
                "Declares the review queues, signal semantics, and resolver rules that "
                "bind research-tech maintenance and follow-up work together."
            ),
            review_queues=[
                ReviewQueueSpec(
                    name="frontmatter",
                    description="Vault hygiene proposals for missing or incomplete note metadata.",
                    operation_profiles=["vault/frontmatter_audit"],
                    proposal_types=["frontmatter_fix"],
                    review_mode="manual_review",
                ),
                ReviewQueueSpec(
                    name="review",
                    description="Extraction outputs that need operator curation before wider use.",
                    operation_profiles=["vault/review_queue"],
                    proposal_types=["queue_review"],
                    review_mode="manual_review",
                ),
                ReviewQueueSpec(
                    name="bridges",
                    description="Suggested cross-note bridges that still need human synthesis.",
                    operation_profiles=["vault/bridge_recommendations"],
                    proposal_types=["bridge_note"],
                    review_mode="manual_review",
                ),
                ReviewQueueSpec(
                    name="contradictions",
                    description="Open truth contradictions awaiting adjudication.",
                    operation_profiles=["truth/contradiction_review"],
                    proposal_types=["truth_contradiction"],
                    review_mode="adjudication",
                ),
                ReviewQueueSpec(
                    name="stale-summaries",
                    description="Weak compiled summaries queued for rebuild-focused review.",
                    operation_profiles=["truth/stale_summary_review"],
                    proposal_types=["stale_summary"],
                    review_mode="rebuild_review",
                ),
            ],
            signal_rules=[
                SignalRuleSpec(
                    signal_type="contradiction_open",
                    description="An active contradiction should route into contradiction review.",
                    resolver_rule="review_contradiction",
                ),
                SignalRuleSpec(
                    signal_type="stale_summary",
                    description="A weak compiled summary should route into summary rebuild review.",
                    resolver_rule="rebuild_summary",
                ),
                SignalRuleSpec(
                    signal_type="production_gap",
                    description="A broken production chain should open an inspection surface first.",
                    resolver_rule="inspect_production_gap",
                ),
                SignalRuleSpec(
                    signal_type="source_needs_deep_dive",
                    description="Processed source notes without deep dives should queue focused deep-dive work.",
                    resolver_rule="deep_dive_workflow",
                    auto_queue=True,
                ),
                SignalRuleSpec(
                    signal_type="deep_dive_needs_objects",
                    description="Deep dives without evergreen objects should queue focused object extraction.",
                    resolver_rule="object_extraction_workflow",
                    auto_queue=True,
                ),
                SignalRuleSpec(
                    signal_type="contradiction_reviewed",
                    description="Resolved contradiction events should point operators to the resolved review surface.",
                    resolver_rule="review_resolution",
                ),
                SignalRuleSpec(
                    signal_type="summary_rebuilt",
                    description="Rebuilt summary events should point operators to the refreshed summary surface.",
                    resolver_rule="review_rebuilt_summary",
                ),
            ],
            resolver_rules=[
                ResolverRuleSpec(
                    name="review_contradiction",
                    description="Run the contradiction review affordance from the UI shell.",
                    resolution_kind="review_mutation",
                    target_name="review_contradiction",
                    dispatch_mode="direct",
                    executable=True,
                ),
                ResolverRuleSpec(
                    name="rebuild_summary",
                    description="Run the stale summary rebuild affordance from the UI shell.",
                    resolution_kind="review_mutation",
                    target_name="rebuild_summary",
                    dispatch_mode="direct",
                    executable=True,
                ),
                ResolverRuleSpec(
                    name="inspect_production_gap",
                    description="Navigate to the production gap inspection surface.",
                    resolution_kind="navigation",
                    target_name="inspect_production_gap",
                    dispatch_mode="navigate",
                ),
                ResolverRuleSpec(
                    name="deep_dive_workflow",
                    description="Queue the focused deep-dive workflow for a processed source note.",
                    resolution_kind="focused_action",
                    target_name="deep_dive_workflow",
                    dispatch_mode="queue_only",
                    safe_to_run=True,
                ),
                ResolverRuleSpec(
                    name="object_extraction_workflow",
                    description="Queue the focused object extraction workflow for a deep dive.",
                    resolution_kind="focused_action",
                    target_name="object_extraction_workflow",
                    dispatch_mode="queue_only",
                    safe_to_run=True,
                ),
                ResolverRuleSpec(
                    name="review_resolution",
                    description="Navigate to resolved contradiction history for follow-up inspection.",
                    resolution_kind="navigation",
                    target_name="review_resolution",
                    dispatch_mode="navigate",
                ),
                ResolverRuleSpec(
                    name="review_rebuilt_summary",
                    description="Navigate to rebuilt summaries for follow-up inspection.",
                    resolution_kind="navigation",
                    target_name="review_rebuilt_summary",
                    dispatch_mode="navigate",
                ),
            ],
        )
    ]
