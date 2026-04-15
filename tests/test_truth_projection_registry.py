from __future__ import annotations


def test_compatibility_pack_falls_back_to_base_truth_projection_builder(monkeypatch):
    from openclaw_pipeline.packs.base import BaseDomainPack, TruthProjectionSpec
    import openclaw_pipeline.truth_projection_registry as registry_source

    compatibility_pack = BaseDomainPack(
        name="compat-pack",
        version="0.1.0",
        api_version=1,
        role="compatibility",
        compatibility_base="base-pack",
    )
    base_pack = BaseDomainPack(
        name="base-pack",
        version="0.1.0",
        api_version=1,
        role="primary",
        _truth_projection=TruthProjectionSpec(
            name="base-truth",
            pack="base-pack",
            entrypoint="tests.fake_truth_projection:build",
        ),
    )

    monkeypatch.setattr(
        registry_source,
        "iter_compatible_packs",
        lambda pack_name: [compatibility_pack, base_pack],
    )

    spec = registry_source.resolve_truth_projection_builder(pack_name="compat-pack")

    assert spec.pack == "base-pack"
    assert spec.entrypoint == "tests.fake_truth_projection:build"
