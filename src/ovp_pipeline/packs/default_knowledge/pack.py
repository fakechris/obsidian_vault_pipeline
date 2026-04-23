from __future__ import annotations

from ..base import (
    AutoPromoteRule,
    BaseDomainPack,
    EscalateRule,
    EvidenceRequirementsSpec,
    PromotionPolicySpec,
    RejectRule,
    WorkspaceZonesSpec,
)
from .extraction_profiles import DEFAULT_EXTRACTION_PROFILES
from .operation_profiles import DEFAULT_OPERATION_PROFILES
from .profiles import DEFAULT_KNOWLEDGE_AUTOPILOT_PROFILE, DEFAULT_KNOWLEDGE_FULL_PROFILE
from .schemas import DEFAULT_KNOWLEDGE_OBJECT_KINDS
from .wiki_views import DEFAULT_WIKI_VIEWS


# ``legacy_or_rule=True`` short-circuits ``promotion_policy.evaluate_concept``
# back to the historical ``source_count >= 2 or evidence_count >= 3`` rule —
# this is the bit-for-bit guarantee for default-knowledge.
DEFAULT_KNOWLEDGE_PROMOTION_POLICY = PromotionPolicySpec(
    auto_promote=AutoPromoteRule(
        require_independent_sources=1,
        require_evidence_kinds=(),
        require_no_open_contradiction=False,
        legacy_or_rule=True,
    ),
    escalate_to_workbench=EscalateRule(),
    reject=RejectRule(min_evidence_floor=0),
)

# Permissive workspace: every path is agent-owned, nothing accepted, nothing
# append-only — preserves the pre-Phase-34 free-write behavior.
DEFAULT_KNOWLEDGE_WORKSPACE_ZONES = WorkspaceZonesSpec(
    agent_owned=("**",),
    accepted=(),
    append_only=(),
)

# No evidence requirements — lint EVIDENCE_INCOMPLETE stays silent under the
# default pack until users opt into a stricter pack.
DEFAULT_KNOWLEDGE_EVIDENCE_REQUIREMENTS = EvidenceRequirementsSpec(
    claim_must_have=(),
    relation_must_have=(),
)


def get_pack() -> BaseDomainPack:
    return BaseDomainPack(
        name="default-knowledge",
        version="0.1.0",
        api_version=1,
        role="compatibility",
        compatibility_base="research-tech",
        _object_kinds=list(DEFAULT_KNOWLEDGE_OBJECT_KINDS),
        _workflow_profiles=[
            DEFAULT_KNOWLEDGE_FULL_PROFILE,
            DEFAULT_KNOWLEDGE_AUTOPILOT_PROFILE,
        ],
        _extraction_profiles=list(DEFAULT_EXTRACTION_PROFILES),
        _operation_profiles=list(DEFAULT_OPERATION_PROFILES),
        _wiki_views=list(DEFAULT_WIKI_VIEWS),
        _promotion_policy=DEFAULT_KNOWLEDGE_PROMOTION_POLICY,
        _workspace_zones=DEFAULT_KNOWLEDGE_WORKSPACE_ZONES,
        _evidence_requirements=DEFAULT_KNOWLEDGE_EVIDENCE_REQUIREMENTS,
    )
