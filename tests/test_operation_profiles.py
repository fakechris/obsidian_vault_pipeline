from __future__ import annotations


def test_default_pack_exposes_first_wave_operation_profiles():
    from openclaw_pipeline.packs.loader import load_default_pack

    pack = load_default_pack()
    profiles = {profile.name: profile for profile in pack.operation_profiles()}

    assert {
        "vault/frontmatter_audit",
        "vault/review_queue",
        "vault/bridge_recommendations",
        "truth/contradiction_review",
        "truth/stale_summary_review",
    } <= set(profiles)

    assert profiles["vault/frontmatter_audit"].review_required is True
    assert profiles["vault/frontmatter_audit"].proposal_types[0].queue_name == "frontmatter"
    assert profiles["vault/review_queue"].scope == "vault"
    assert profiles["truth/contradiction_review"].scope == "truth"
