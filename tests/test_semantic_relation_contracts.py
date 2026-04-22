from __future__ import annotations


def test_research_tech_declares_review_gated_semantic_relation_contract():
    from ovp_pipeline.packs.loader import load_pack

    pack = load_pack("research-tech")

    contract = pack.semantic_relation_contract("research_semantic_relations")

    assert contract.pack == "research-tech"
    assert contract.source_contract_kind == "artifact_spec"
    assert contract.source_contract_name == "semantic_relation_candidate"
    assert contract.review_queue_name == "semantic-relations"
    assert contract.write_policy == "review_required"
    assert {relation.name for relation in contract.relation_types} >= {
        "supports",
        "challenges",
        "extends",
        "replaces",
        "uses",
    }
    assert all(relation.evidence_required for relation in contract.relation_types)
    assert all(relation.review_required for relation in contract.relation_types)


def test_research_tech_semantic_relation_candidate_is_review_artifact():
    from ovp_pipeline.packs.loader import load_pack

    pack = load_pack("research-tech")

    artifact = pack.artifact_spec("semantic_relation_candidate")

    assert artifact.layer == "governance"
    assert artifact.family == "semantic_relation_candidate"
    assert artifact.evidence_policy.requires_evidence is True
    assert artifact.evidence_policy.require_quote is True
    assert artifact.storage_policy.storage_mode == "review_queue_artifact"
    assert artifact.storage_policy.review_queue_name == "semantic-relations"
    assert artifact.lifecycle_policy.review_required_on_create is True
    assert artifact.lifecycle_policy.review_required_on_update is True


def test_doctor_reports_semantic_relation_contracts(capsys):
    import json

    from ovp_pipeline.commands.doctor import main

    exit_code = main(["--pack", "research-tech", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    declared = payload["contracts"]["declared"]["semantic_relation_contracts"]
    effective = payload["contracts"]["effective"]["semantic_relation_contracts"]
    assert any(
        item["name"] == "research_semantic_relations"
        and item["provider_pack"] == "research-tech"
        and item["status"] == "declared"
        and item["review_queue_name"] == "semantic-relations"
        and item["write_policy"] == "review_required"
        and {relation["name"] for relation in item["relation_types"]} >= {"supports", "challenges"}
        for item in declared
    )
    assert any(
        item["name"] == "research_semantic_relations"
        and item["provider_pack"] == "research-tech"
        and item["status"] == "declared"
        for item in effective
    )
