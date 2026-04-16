from __future__ import annotations


def test_resolve_stage_execution_contract_for_primary_pack():
    from openclaw_pipeline.execution_contract_registry import resolve_stage_execution_contract

    contract = resolve_stage_execution_contract(
        pack_name="research-tech",
        stage="articles",
        runtime_adapter="pipeline_step",
    )

    assert contract.stage == "articles"
    assert contract.action_kind is None
    assert contract.handler_spec.pack == "research-tech"
    assert contract.processor_contract.pack == "research-tech"
    assert contract.processor_contract.mode == "llm_structured"


def test_resolve_focused_action_execution_contract_for_compatibility_pack():
    from openclaw_pipeline.execution_contract_registry import (
        resolve_focused_action_execution_contract,
    )

    contract = resolve_focused_action_execution_contract(
        pack_name="default-knowledge",
        action_kind="deep_dive_workflow",
    )

    assert contract.stage is None
    assert contract.action_kind == "deep_dive_workflow"
    assert contract.handler_spec.pack == "research-tech"
    assert contract.processor_contract.pack == "research-tech"
    assert contract.handler_spec.safe_to_run is True


def test_list_effective_execution_contracts_falls_back_to_compatibility_base():
    from openclaw_pipeline.execution_contract_registry import list_effective_execution_contracts

    contracts = list_effective_execution_contracts(pack_name="default-knowledge")

    assert any(
        contract.stage == "articles"
        and contract.handler_spec.pack == "research-tech"
        and contract.processor_contract.pack == "research-tech"
        for contract in contracts
    )
    assert any(contract.action_kind == "deep_dive_workflow" for contract in contracts)


def test_compute_declared_contract_integrity_reports_missing_and_orphan_keys():
    from openclaw_pipeline.execution_contract_registry import compute_declared_contract_integrity
    from openclaw_pipeline.packs.base import BaseDomainPack, ProcessorContractSpec, StageHandlerSpec

    pack = BaseDomainPack(
        name="diagnostic-pack",
        version="0.1.0",
        api_version=1,
        _stage_handlers=[
            StageHandlerSpec(
                name="articles",
                pack="diagnostic-pack",
                handler_kind="profile_stage",
                runtime_adapter="pipeline_step",
                entrypoint="tests.fake:articles",
                stage="articles",
            )
        ],
        _processor_contracts=[
            ProcessorContractSpec(
                name="quality",
                pack="diagnostic-pack",
                stage="quality",
                mode="evaluation",
                inputs=("deep_dive",),
                outputs=("quality_report",),
                entrypoint="tests.fake:quality",
            )
        ],
    )

    payload = compute_declared_contract_integrity(pack_name=pack)

    assert payload["missing_processor_contracts"] == [
        {
            "stage": "articles",
            "action_kind": None,
            "runtime_adapters": ["pipeline_step"],
        }
    ]
    assert payload["orphan_processor_contracts"] == [
        {
            "stage": "quality",
            "action_kind": None,
            "mode": "evaluation",
            "entrypoint": "tests.fake:quality",
        }
    ]
