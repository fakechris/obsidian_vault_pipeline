from __future__ import annotations


def test_artifact_registry_lists_declared_specs_for_primary_pack():
    from openclaw_pipeline.artifact_registry import list_effective_artifact_specs

    specs = list_effective_artifact_specs(pack_name="research-tech")

    assert {spec.name for spec in specs} >= {
        "canonical_object",
        "canonical_claim",
        "claim_evidence",
        "compiled_overview",
        "review_item",
    }
    assert all(spec.pack == "research-tech" for spec in specs)


def test_artifact_registry_inherits_specs_for_compatibility_pack():
    from openclaw_pipeline.artifact_registry import list_effective_artifact_specs

    specs = list_effective_artifact_specs(pack_name="default-knowledge")

    assert {spec.family for spec in specs} >= {
        "object",
        "claim",
        "evidence",
        "overview",
        "review_item",
    }
    assert all(spec.pack == "research-tech" for spec in specs)
