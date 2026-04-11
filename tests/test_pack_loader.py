from __future__ import annotations

import pytest


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
