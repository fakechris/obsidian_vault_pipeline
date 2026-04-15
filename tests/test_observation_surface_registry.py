from __future__ import annotations


def test_compatibility_pack_falls_back_to_base_observation_surface_builder(monkeypatch):
    from openclaw_pipeline.observation_surface_registry import resolve_observation_surface_builder
    from openclaw_pipeline.packs.base import BaseDomainPack, ObservationSurfaceSpec
    import openclaw_pipeline.observation_surface_registry as registry_source

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
        _observation_surfaces=[
            ObservationSurfaceSpec(
                name="base-signals",
                pack="base-pack",
                surface_kind="signals",
                entrypoint="tests.fake_observation_surface:build",
            )
        ],
    )

    monkeypatch.setattr(
        registry_source,
        "iter_compatible_packs",
        lambda pack_name: [compatibility_pack, base_pack],
    )

    spec = resolve_observation_surface_builder(pack_name="compat-pack", surface_kind="signals")

    assert spec.pack == "base-pack"
    assert spec.surface_kind == "signals"
