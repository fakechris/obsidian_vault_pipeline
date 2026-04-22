from __future__ import annotations

from argparse import Namespace


def test_no_pack_selection_matches_primary_full_profile():
    from ovp_pipeline.packs.loader import load_primary_pack
    from ovp_pipeline.unified_pipeline_enhanced import build_execution_plan

    args = Namespace(
        full=True,
        with_refine=False,
        pinboard_new=False,
        pinboard_history=None,
        pinboard_days=None,
        step=None,
        from_step=None,
        pack=None,
        profile=None,
    )

    plan = build_execution_plan(args)
    primary_pack = load_primary_pack()
    full_profile = primary_pack.profile("full")

    assert plan["pack"] == "research-tech"
    assert plan["profile"] == "full"
    assert plan["steps"] == full_profile.stages


def test_explicit_default_pack_profile_matches_default_full_profile():
    from ovp_pipeline.packs.loader import load_default_pack
    from ovp_pipeline.unified_pipeline_enhanced import build_execution_plan

    args = Namespace(
        full=True,
        with_refine=False,
        pinboard_new=False,
        pinboard_history=None,
        pinboard_days=None,
        step=None,
        from_step=None,
        pack="default-knowledge",
        profile="full",
    )

    plan = build_execution_plan(args)
    default_pack = load_default_pack()

    assert plan["pack"] == "default-knowledge"
    assert plan["profile"] == "full"
    assert plan["steps"] == default_pack.profile("full").stages


def test_step_aliases_remain_compatible_with_default_pack():
    from ovp_pipeline.packs.loader import load_default_pack
    from ovp_pipeline.unified_pipeline_enhanced import STEP_ALIASES

    default_pack = load_default_pack()

    assert STEP_ALIASES["evergreen"] == "absorb"
    assert "absorb" in default_pack.profile("full").stages


def test_explicit_research_tech_pack_profile_matches_full_profile():
    from ovp_pipeline.packs.loader import load_pack
    from ovp_pipeline.unified_pipeline_enhanced import build_execution_plan

    args = Namespace(
        full=True,
        with_refine=False,
        pinboard_new=False,
        pinboard_history=None,
        pinboard_days=None,
        step=None,
        from_step=None,
        pack="research-tech",
        profile="full",
    )

    plan = build_execution_plan(args)
    pack = load_pack("research-tech")

    assert plan["pack"] == "research-tech"
    assert plan["profile"] == "full"
    assert plan["steps"] == pack.profile("full").stages


def test_default_knowledge_legacy_or_rule_byte_for_byte_compat():
    """Phase 34 §5.12 guarantee: default-knowledge `legacy_or_rule=True` must
    reproduce the historical `source_count >= 2 or evidence_count >= 3` rule
    across the full lane decision matrix (auto vs hold)."""
    from ovp_pipeline.concept_registry import ConceptEntry
    from ovp_pipeline.packs.loader import load_pack
    from ovp_pipeline.promotion_policy import LANE_AUTO, LANE_HOLD, evaluate_concept

    pack = load_pack("default-knowledge")
    for source_count in range(0, 5):
        for evidence_count in range(0, 5):
            entry = ConceptEntry(
                slug=f"slug-{source_count}-{evidence_count}",
                title="t",
                aliases=[],
                definition="d",
                area="a",
                status="candidate",
                source_count=source_count,
                evidence_count=evidence_count,
            )
            decision = evaluate_concept(entry, pack=pack)
            historical = source_count >= 2 or evidence_count >= 3
            expected_lane = LANE_AUTO if historical else LANE_HOLD
            assert decision.lane == expected_lane, (
                f"legacy_or_rule diverged for "
                f"source={source_count} evidence={evidence_count}: "
                f"got {decision.lane}, expected {expected_lane}"
            )
