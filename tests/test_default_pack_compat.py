from __future__ import annotations

from argparse import Namespace


def test_no_pack_selection_matches_primary_full_profile():
    from openclaw_pipeline.packs.loader import load_primary_pack
    from openclaw_pipeline.unified_pipeline_enhanced import build_execution_plan

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
    from openclaw_pipeline.packs.loader import load_default_pack
    from openclaw_pipeline.unified_pipeline_enhanced import build_execution_plan

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
    from openclaw_pipeline.packs.loader import load_default_pack
    from openclaw_pipeline.unified_pipeline_enhanced import STEP_ALIASES

    default_pack = load_default_pack()

    assert STEP_ALIASES["evergreen"] == "absorb"
    assert "absorb" in default_pack.profile("full").stages


def test_explicit_research_tech_pack_profile_matches_full_profile():
    from openclaw_pipeline.packs.loader import load_pack
    from openclaw_pipeline.unified_pipeline_enhanced import build_execution_plan

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
