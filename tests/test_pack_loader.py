from __future__ import annotations

import pytest


def test_base_domain_pack_rejects_invalid_role():
    from openclaw_pipeline.packs.base import BaseDomainPack

    with pytest.raises(ValueError, match="Invalid pack role"):
        BaseDomainPack(name="broken", version="0.1.0", api_version=1, role="weird")


def test_base_domain_pack_rejects_incompatible_compatibility_base():
    from openclaw_pipeline.packs.base import BaseDomainPack

    with pytest.raises(ValueError, match="compatibility_base"):
        BaseDomainPack(
            name="broken",
            version="0.1.0",
            api_version=1,
            role="primary",
            compatibility_base="research-tech",
        )


def test_base_domain_pack_rejects_stage_handler_from_other_pack():
    from openclaw_pipeline.packs.base import BaseDomainPack, StageHandlerSpec

    with pytest.raises(ValueError, match="declares stage handler"):
        BaseDomainPack(
            name="broken",
            version="0.1.0",
            api_version=1,
            _stage_handlers=[
                StageHandlerSpec(
                    name="articles",
                    pack="other-pack",
                    handler_kind="profile_stage",
                    runtime_adapter="pipeline_step",
                    entrypoint="tests.fake:handler",
                    stage="articles",
                )
            ],
        )


def test_base_domain_pack_rejects_processor_contract_from_other_pack():
    from openclaw_pipeline.packs.base import BaseDomainPack, ProcessorContractSpec

    with pytest.raises(ValueError, match="declares processor contract"):
        BaseDomainPack(
            name="broken",
            version="0.1.0",
            api_version=1,
            _processor_contracts=[
                ProcessorContractSpec(
                    name="articles",
                    pack="other-pack",
                    stage="articles",
                    mode="llm_structured",
                    inputs=("source_note",),
                    outputs=("deep_dive",),
                    entrypoint="tests.fake:handler",
                )
            ],
        )


def test_load_default_pack_returns_pack_contract():
    from openclaw_pipeline.packs.base import BaseDomainPack
    from openclaw_pipeline.packs.loader import load_default_pack

    pack = load_default_pack()

    assert isinstance(pack, BaseDomainPack)
    assert pack.name == "default-knowledge"
    assert pack.version
    assert pack.object_kinds()
    assert pack.workflow_profiles()
    assert pack.role == "compatibility"
    assert pack.compatibility_base == "research-tech"


def test_load_pack_by_name_returns_default_knowledge():
    from openclaw_pipeline.packs.loader import load_pack

    pack = load_pack("default-knowledge")

    assert pack.name == "default-knowledge"


def test_load_pack_by_name_returns_research_tech():
    from openclaw_pipeline.packs.loader import load_pack

    pack = load_pack("research-tech")

    assert pack.name == "research-tech"


def test_load_primary_pack_returns_research_tech():
    from openclaw_pipeline.packs.loader import load_primary_pack

    pack = load_primary_pack()

    assert pack.name == "research-tech"
    assert pack.role == "primary"
    assert pack.compatibility_base is None


def test_list_builtin_packs_reports_roles():
    from openclaw_pipeline.packs.loader import list_builtin_packs

    packs = {pack.name: pack for pack in list_builtin_packs()}

    assert packs["research-tech"].role == "primary"
    assert packs["default-knowledge"].role == "compatibility"
    assert packs["default-knowledge"].compatibility_base == "research-tech"


def test_load_pack_rejects_unknown_pack():
    from openclaw_pipeline.packs.loader import load_pack

    with pytest.raises(ValueError):
        load_pack("unknown-pack")


def test_load_builtin_pack_rejects_unknown_pack_with_clear_error():
    from openclaw_pipeline.packs.loader import load_builtin_pack

    with pytest.raises(ValueError, match="Unknown builtin pack"):
        load_builtin_pack("unknown-pack")


def test_resolve_workflow_profile_rejects_profile_without_execution_contract(monkeypatch):
    from openclaw_pipeline.packs.base import BaseDomainPack, WorkflowProfile
    import openclaw_pipeline.packs.loader as loader

    broken_pack = BaseDomainPack(
        name="broken-pack",
        version="0.1.0",
        api_version=1,
        _workflow_profiles=[
            WorkflowProfile(
                name="full",
                description="Broken profile",
                stages=["ghost_stage"],
            )
        ],
    )

    monkeypatch.setattr(loader, "load_pack", lambda name: broken_pack)

    with pytest.raises(ValueError, match="missing execution contract"):
        loader.resolve_workflow_profile(
            pack_name="broken-pack",
            profile_name="full",
            default_profile="full",
        )
