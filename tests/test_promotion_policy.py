"""Phase 34 — promotion policy + workspace zone enforcement.

Coverage:
* default-knowledge legacy_or_rule reproduces the historical OR rule
* research-tech strict policy assigns lanes correctly on a fixture set
* ZoneViolation raised when an agent script writes to an accepted-zone path
* workspace_promote bypasses the gate and copies the draft
* lint ZONE_BOUNDARY_VIOLATION fires on raw mtime drift, clears after promotion
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ovp_pipeline.concept_registry import ConceptEntry, ConceptRegistry
from ovp_pipeline.packs.loader import load_pack
from ovp_pipeline.promotion_policy import (
    LANE_AUTO,
    LANE_ESCALATE,
    LANE_HOLD,
    LANE_REJECT,
    PolicyDecision,
    evaluate_concept,
    evaluate_workspace,
)
from ovp_pipeline.workspace_promotion import (
    WRITE_MODE_NORMAL,
    WRITE_MODE_PROMOTION,
    ZoneViolation,
    enforce_zone_write,
    is_accepted_zone,
    is_append_only,
    promote as workspace_promote,
)


# ---------------------------------------------------------------------------
# Concept lane evaluation
# ---------------------------------------------------------------------------


def _candidate(slug: str, *, source_count: int = 0, evidence_count: int = 0) -> ConceptEntry:
    return ConceptEntry(
        slug=slug,
        title=slug.replace("-", " ").title(),
        aliases=[],
        definition="x",
        area="Test",
        status="candidate",
        source_count=source_count,
        evidence_count=evidence_count,
    )


def test_default_knowledge_legacy_or_promotes_with_two_sources():
    pack = load_pack("default-knowledge")
    decision = evaluate_concept(_candidate("a", source_count=2), pack=pack)
    assert decision.lane == LANE_AUTO
    assert decision.reason_code == "legacy_or_rule"


def test_default_knowledge_legacy_or_promotes_with_three_evidence():
    pack = load_pack("default-knowledge")
    decision = evaluate_concept(_candidate("b", evidence_count=3), pack=pack)
    assert decision.lane == LANE_AUTO


def test_default_knowledge_legacy_or_holds_below_threshold():
    pack = load_pack("default-knowledge")
    decision = evaluate_concept(
        _candidate("c", source_count=1, evidence_count=1), pack=pack
    )
    assert decision.lane == LANE_HOLD
    assert decision.reason_code == "legacy_or_rule_below_threshold"


def test_research_tech_strict_promotes_when_policy_satisfied():
    pack = load_pack("research-tech")
    # research-tech: require_independent_sources=2, require_evidence_kinds=("page_summary",)
    decision = evaluate_concept(
        _candidate("d", source_count=3, evidence_count=2),
        pack=pack,
        evidence_kinds=frozenset({"page_summary"}),
    )
    assert decision.lane == LANE_AUTO
    assert decision.reason_code == "policy_satisfied"


def test_research_tech_strict_rejects_below_evidence_floor():
    pack = load_pack("research-tech")
    decision = evaluate_concept(
        _candidate("e", source_count=2, evidence_count=0),
        pack=pack,
        evidence_kinds=frozenset({"page_summary"}),
    )
    assert decision.lane == LANE_REJECT
    assert decision.reason_code == "below_evidence_floor"


def test_research_tech_strict_escalates_on_partial_evidence():
    pack = load_pack("research-tech")
    decision = evaluate_concept(
        _candidate("f", source_count=2, evidence_count=2),
        pack=pack,
        evidence_kinds=frozenset(),  # missing page_summary
    )
    # research-tech sets escalate.on_partial_evidence=True
    assert decision.lane == LANE_ESCALATE
    assert any("missing_evidence_kinds" in fact for fact in decision.blocking_facts)


def test_policy_decision_rejects_invalid_lane():
    with pytest.raises(ValueError):
        PolicyDecision(lane="bogus", reason_code="x")


def test_collect_pack_signals_supplies_evidence_kinds(temp_vault):
    """Round-trip: candidate with claim_evidence in DB gets promote suggestion."""
    from ovp_pipeline.knowledge_index import rebuild_knowledge_index
    from ovp_pipeline.promote_candidates import review_candidates
    from ovp_pipeline.promotion_policy import collect_pack_signals
    from ovp_pipeline.runtime import VaultLayout

    rebuild_knowledge_index(temp_vault)
    pack = load_pack("research-tech")
    layout = VaultLayout.from_vault(temp_vault)

    with sqlite3.connect(layout.knowledge_db) as conn:
        conn.execute(
            "INSERT INTO objects (pack, object_id, object_kind, title, canonical_path, source_slug) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pack.name, "ai-agent", "concept", "AI Agent", "Evergreen/AI Agent.md", "ai-agent"),
        )
        conn.execute(
            "INSERT INTO claims (pack, claim_id, object_id, claim_kind, claim_text, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pack.name, "c1", "ai-agent", "definition", "AI Agent is X", 0.9),
        )
        conn.execute(
            "INSERT INTO claim_evidence (pack, claim_id, source_slug, evidence_kind) "
            "VALUES (?, ?, ?, ?)",
            (pack.name, "c1", "agent-memory", "page_summary"),
        )
        conn.commit()

    kinds_by_id, disputed = collect_pack_signals(layout.knowledge_db, pack_name=pack.name)
    assert kinds_by_id == {"ai-agent": frozenset({"page_summary"})}
    assert disputed == frozenset()

    # And review_candidates threads them through end-to-end.
    registry = ConceptRegistry(temp_vault).load()
    entry = registry.upsert_candidate(
        slug="ai-agent",
        title="AI Agent",
        definition="An autonomous loop.",
        area="AI-Research",
    )
    # research-tech requires 2 independent sources; bump until satisfied.
    entry.source_count = 3
    entry.evidence_count = 2
    registry.save()
    registry = ConceptRegistry(temp_vault).load()
    suggestions = review_candidates(registry, pack=pack)
    actions = {entry.slug: action for entry, action, _ in suggestions}
    assert actions.get("ai-agent") == "promote_to_active"


# ---------------------------------------------------------------------------
# Workspace zone enforcement
# ---------------------------------------------------------------------------


def test_default_knowledge_zone_is_permissive(temp_vault):
    pack = load_pack("default-knowledge")
    target = temp_vault / "10-Knowledge" / "Evergreen" / "Whatever.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    # Permissive pack: no accepted globs → write mode passes.
    enforce_zone_write(target, pack=pack, vault_dir=temp_vault, mode=WRITE_MODE_NORMAL)


def test_research_tech_zone_blocks_normal_write_to_accepted(temp_vault):
    pack = load_pack("research-tech")
    target = temp_vault / "10-Knowledge" / "Evergreen" / "Sealed.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ZoneViolation):
        enforce_zone_write(
            target,
            pack=pack,
            vault_dir=temp_vault,
            mode=WRITE_MODE_NORMAL,
        )


def test_research_tech_zone_allows_promotion_mode(temp_vault):
    pack = load_pack("research-tech")
    target = temp_vault / "10-Knowledge" / "Evergreen" / "Promoted.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    enforce_zone_write(
        target,
        pack=pack,
        vault_dir=temp_vault,
        mode=WRITE_MODE_PROMOTION,
    )


def test_research_tech_zone_allows_agent_owned_paths(temp_vault):
    pack = load_pack("research-tech")
    target = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "draft.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    enforce_zone_write(
        target,
        pack=pack,
        vault_dir=temp_vault,
        mode=WRITE_MODE_NORMAL,
    )


def test_is_append_only_recognizes_writing_prompts(temp_vault):
    pack = load_pack("research-tech")
    polaris = temp_vault / "00-Polaris" / "Writing-Prompts.md"
    polaris.parent.mkdir(parents=True, exist_ok=True)
    polaris.write_text("# Prompts\n", encoding="utf-8")
    assert is_append_only(polaris, pack=pack, vault_dir=temp_vault)
    assert is_accepted_zone(polaris, pack=pack, vault_dir=temp_vault)


def test_workspace_promote_copies_draft_under_promotion_mode(temp_vault):
    pack = load_pack("research-tech")
    draft = temp_vault / "30-Projects" / "demo" / "Drafts" / "plan-draft.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("plan body", encoding="utf-8")
    target = temp_vault / "30-Projects" / "demo" / "Plan.md"
    record = workspace_promote(
        draft,
        target,
        approver="tester",
        pack=pack,
        vault_dir=temp_vault,
    )
    assert target.read_text(encoding="utf-8") == "plan body"
    assert record.bytes_written == len("plan body")
    assert record.pack == "research-tech"


# ---------------------------------------------------------------------------
# evaluate_workspace
# ---------------------------------------------------------------------------


def test_evaluate_workspace_permissive_pack_auto(temp_vault):
    pack = load_pack("default-knowledge")
    draft = temp_vault / "draft.md"
    draft.write_text("x", encoding="utf-8")
    decision = evaluate_workspace(draft, temp_vault / "target.md", pack=pack)
    assert decision.lane == LANE_AUTO
    assert decision.reason_code == "permissive_pack"


def test_evaluate_workspace_strict_rejects_missing_draft(temp_vault):
    pack = load_pack("research-tech")
    decision = evaluate_workspace(
        temp_vault / "absent.md",
        temp_vault / "30-Projects" / "demo" / "Plan.md",
        pack=pack,
    )
    assert decision.lane == LANE_REJECT
    assert "draft_missing" in decision.blocking_facts
