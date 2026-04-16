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


def test_execute_truth_projection_builder_namespaces_rows_to_requested_pack(temp_vault):
    from openclaw_pipeline.truth_projection_registry import execute_truth_projection_builder

    page_rows = [
        (
            "source-note",
            "Source Note",
            "evergreen",
            str(temp_vault / "10-Knowledge" / "Evergreen" / "Source.md"),
            "2026-04-15",
            "{}",
            "Source body.",
        )
    ]

    spec, projection = execute_truth_projection_builder(
        vault_dir=temp_vault,
        page_rows=page_rows,
        link_rows=[],
        pack_name="default-knowledge",
    )

    assert spec.pack == "research-tech"
    assert projection.objects[0][0] == "default-knowledge"
    assert projection.claims[0][0] == "default-knowledge"
    assert projection.compiled_summaries[0][0] == "default-knowledge"
