from __future__ import annotations

from openclaw_pipeline.concept_registry import ConceptEntry, ConceptRegistry, STATUS_ACTIVE


def test_object_record_contains_core_pack_fields():
    from openclaw_pipeline.object_registry import ObjectRecord

    record = ObjectRecord(
        id="agent-harness",
        kind="concept",
        pack="default-knowledge",
        title="Agent Harness",
        status=STATUS_ACTIVE,
    )

    assert record.id == "agent-harness"
    assert record.kind == "concept"
    assert record.pack == "default-knowledge"
    assert record.title == "Agent Harness"
    assert record.status == STATUS_ACTIVE


def test_concept_registry_projects_entries_into_object_registry(temp_vault):
    from openclaw_pipeline.object_registry import ObjectRegistry

    registry = ConceptRegistry(temp_vault)
    registry.add_entry(
        ConceptEntry(
            slug="agent-harness",
            title="Agent Harness",
            aliases=["Harness"],
            definition="A repeatable agent runtime harness.",
            area="AI",
            status=STATUS_ACTIVE,
        )
    )

    object_registry = ObjectRegistry.from_concept_registry(registry)
    records = object_registry.records()

    assert len(records) == 1
    assert records[0].id == "agent-harness"
    assert records[0].kind == "concept"
    assert records[0].pack == "default-knowledge"
    assert records[0].status == STATUS_ACTIVE


def test_concept_registry_exposes_object_records_without_changing_legacy_behavior(temp_vault):
    registry = ConceptRegistry(temp_vault)
    entry = ConceptEntry(
        slug="deep-research",
        title="Deep Research",
        aliases=["Research Loop"],
        definition="A structured research workflow.",
        area="AI",
        status=STATUS_ACTIVE,
    )
    registry.add_entry(entry)

    records = registry.to_object_records()

    assert registry.find_by_slug("deep-research") is entry
    assert len(registry.entries) == 1
    assert len(records) == 1
    assert records[0].id == "deep-research"
    assert records[0].title == "Deep Research"


def test_object_registry_can_project_entries_into_research_tech_pack(temp_vault):
    from openclaw_pipeline.object_registry import ObjectRegistry

    registry = ConceptRegistry(temp_vault)
    registry.add_entry(
        ConceptEntry(
            slug="workflow-graph",
            title="Workflow Graph",
            aliases=["Graph Flow"],
            definition="A workflow graph concept.",
            area="AI",
            status=STATUS_ACTIVE,
        )
    )

    object_registry = ObjectRegistry.from_concept_registry(registry, pack="research-tech")
    records = object_registry.records()

    assert len(records) == 1
    assert records[0].pack == "research-tech"


def test_concept_registry_to_object_records_accepts_explicit_pack(temp_vault):
    registry = ConceptRegistry(temp_vault)
    registry.add_entry(
        ConceptEntry(
            slug="agent-runtime",
            title="Agent Runtime",
            aliases=["Runtime"],
            definition="An agent runtime concept.",
            area="AI",
            status=STATUS_ACTIVE,
        )
    )

    records = registry.to_object_records(pack="research-tech")

    assert len(records) == 1
    assert records[0].pack == "research-tech"
