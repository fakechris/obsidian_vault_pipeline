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


def test_load_pack_by_name_returns_default_knowledge():
    from openclaw_pipeline.packs.loader import load_pack

    pack = load_pack("default-knowledge")

    assert pack.name == "default-knowledge"


def test_load_pack_rejects_unknown_pack():
    from openclaw_pipeline.packs.loader import load_pack

    with pytest.raises(ValueError):
        load_pack("unknown-pack")

