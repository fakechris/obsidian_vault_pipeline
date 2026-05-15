from __future__ import annotations


def test_default_knowledge_pack_exposes_expected_object_kinds():
    from ovp_pipeline.packs.loader import load_default_pack

    pack = load_default_pack()
    kinds = {kind.kind for kind in pack.object_kinds()}

    assert {"concept", "entity", "evergreen", "document"} <= kinds


def test_default_knowledge_pack_registers_full_and_autopilot_profiles():
    from ovp_pipeline.packs.loader import load_default_pack

    pack = load_default_pack()
    profiles = {profile.name: profile for profile in pack.workflow_profiles()}

    assert "full" in profiles
    assert "autopilot" in profiles


def test_default_knowledge_full_profile_matches_current_stage_order():
    from ovp_pipeline.packs.loader import load_default_pack

    pack = load_default_pack()
    profile = pack.profile("full")

    assert profile.stages == [
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
        # M24.1: lifecycle projection step appended after
        # knowledge_index.  M25.6 dogfood pass caught that it was
        # missing from the workflow profile despite being in
        # BASE_PIPELINE_STEPS — fix in
        # ``packs/research_tech/shared.py``.
        "ops_state",
    ]
