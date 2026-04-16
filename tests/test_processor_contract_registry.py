from __future__ import annotations


def test_resolve_processor_contract_by_stage_for_primary_pack():
    from openclaw_pipeline.processor_contract_registry import resolve_processor_contract

    spec = resolve_processor_contract(pack_name="research-tech", stage="articles")

    assert spec.pack == "research-tech"
    assert spec.stage == "articles"
    assert spec.mode == "llm_structured"


def test_resolve_processor_contract_by_action_for_compatibility_pack():
    from openclaw_pipeline.processor_contract_registry import resolve_processor_contract

    spec = resolve_processor_contract(
        pack_name="default-knowledge",
        action_kind="deep_dive_workflow",
    )

    assert spec.pack == "research-tech"
    assert spec.action_kind == "deep_dive_workflow"


def test_list_effective_processor_contracts_falls_back_to_compatibility_base():
    from openclaw_pipeline.processor_contract_registry import list_effective_processor_contracts

    specs = list_effective_processor_contracts(pack_name="default-knowledge")

    assert any(spec.stage == "articles" and spec.pack == "research-tech" for spec in specs)
    assert any(spec.action_kind == "deep_dive_workflow" for spec in specs)


def test_research_tech_processor_contracts_cover_all_declared_handler_keys():
    from openclaw_pipeline.packs.loader import load_pack

    pack = load_pack("research-tech")
    handler_keys = {
        (str(spec.stage or ""), str(spec.action_kind or ""))
        for spec in pack.stage_handlers()
    }
    contract_keys = {
        (str(spec.stage or ""), str(spec.action_kind or ""))
        for spec in pack.processor_contracts()
    }

    assert handler_keys <= contract_keys
