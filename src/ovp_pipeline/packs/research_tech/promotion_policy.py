"""research-tech promotion policy (Phase 34).

Strict reference implementation. ``default-knowledge`` opts into the legacy
OR rule via ``legacy_or_rule=True`` for bit-for-bit backward compat.
"""

from __future__ import annotations

from ..base import (
    AutoPromoteRule,
    EscalateRule,
    PromotionPolicySpec,
    RejectRule,
)


RESEARCH_TECH_PROMOTION_POLICY = PromotionPolicySpec(
    auto_promote=AutoPromoteRule(
        require_independent_sources=2,
        require_evidence_kinds=("page_summary",),
        require_no_open_contradiction=True,
        legacy_or_rule=False,
    ),
    escalate_to_workbench=EscalateRule(
        on_partial_evidence=True,
        on_disputed=True,
        on_unverified_evidence=True,
    ),
    reject=RejectRule(min_evidence_floor=1),
)
