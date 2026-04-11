from __future__ import annotations


def test_research_tech_pack_exposes_current_technical_profiles():
    from openclaw_pipeline.packs.loader import load_pack

    pack = load_pack("research-tech")
    extraction_profiles = {profile.name: profile for profile in pack.extraction_profiles()}

    assert "tech/doc_structure" in extraction_profiles
    assert "tech/workflow_graph" in extraction_profiles
    assert "media/news_timeline" not in extraction_profiles


def test_default_knowledge_pack_remains_compatibility_surface():
    from openclaw_pipeline.packs.loader import load_pack

    pack = load_pack("default-knowledge")
    extraction_profiles = {profile.name: profile for profile in pack.extraction_profiles()}

    assert "tech/doc_structure" in extraction_profiles
    assert "media/news_timeline" in extraction_profiles
    assert all(profile.pack == "default-knowledge" for profile in extraction_profiles.values())
