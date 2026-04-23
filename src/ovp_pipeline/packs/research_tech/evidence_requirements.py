"""research-tech evidence requirements (Phase 34).

Re-keys Phase 33's hard-coded ``strict_packs={"research-tech"}`` lint check
to a pack-declared contract. Each entry is a column name that must be
non-empty for a row to count as 'complete'.
"""

from __future__ import annotations

from ..base import EvidenceRequirementsSpec


RESEARCH_TECH_EVIDENCE_REQUIREMENTS = EvidenceRequirementsSpec(
    claim_must_have=("locator", "content_hash"),
    relation_must_have=("evidence_source_slug", "content_hash"),
)
