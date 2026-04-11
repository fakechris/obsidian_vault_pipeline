from __future__ import annotations


def test_default_pack_exposes_first_wave_wiki_views():
    from openclaw_pipeline.packs.loader import load_default_pack

    pack = load_default_pack()
    views = {view.name: view for view in pack.wiki_views()}

    assert {
        "overview/domain",
        "overview/topic",
        "saved_answer/query",
        "event/dossier",
        "truth/contradictions",
    } <= set(views)

    assert views["overview/domain"].traceability_policy.include_sources is True
    assert views["saved_answer/query"].publish_target == "compiled_markdown"
