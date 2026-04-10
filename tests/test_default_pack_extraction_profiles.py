from __future__ import annotations


def test_default_pack_registers_first_wave_extraction_profiles():
    from openclaw_pipeline.packs.loader import load_default_pack

    pack = load_default_pack()
    profiles = {profile.name: profile for profile in pack.extraction_profiles()}

    assert {
        "media/news_timeline",
        "media/commentary_sentiment",
        "tech/doc_structure",
        "tech/workflow_graph",
    } <= set(profiles)

    assert profiles["tech/doc_structure"].identifier_fields == ["section_title"]
    assert profiles["tech/workflow_graph"].projection_target.channel == "extraction"
    assert profiles["media/news_timeline"].grounding_policy.require_quote is True
    assert profiles["media/commentary_sentiment"].projection_target.object_kind == "document"
