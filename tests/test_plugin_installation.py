from __future__ import annotations

import sys
from pathlib import Path

import pytest


def test_external_pack_can_be_discovered_via_manifest(tmp_path, monkeypatch):
    from openclaw_pipeline.plugins import discover_plugin_manifests, load_manifest_pack

    package_root = tmp_path / "fake_pack"
    package_root.mkdir()
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "plugin.py").write_text(
        """
from openclaw_pipeline.packs.base import BaseDomainPack

def get_pack():
    return BaseDomainPack(name="media-editorial", version="0.1.0", api_version=1)
""",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        """
name: media-editorial
version: 0.1.0
api_version: 1
entrypoints:
  pack: fake_pack.plugin:get_pack
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    manifests = discover_plugin_manifests([manifest])
    pack = load_manifest_pack(manifests["media-editorial"])

    assert "media-editorial" in manifests
    assert pack.name == "media-editorial"


def test_plugin_manifest_validation_fails_clearly_on_missing_fields(tmp_path):
    from openclaw_pipeline.plugins import discover_plugin_manifests

    manifest = tmp_path / "broken.yaml"
    manifest.write_text("version: 0.1.0\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required field"):
        discover_plugin_manifests([manifest])


def test_incompatible_api_versions_fail_clearly(tmp_path, monkeypatch):
    from openclaw_pipeline.plugins import discover_plugin_manifests, load_manifest_pack

    package_root = tmp_path / "bad_pack"
    package_root.mkdir()
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "plugin.py").write_text(
        """
from openclaw_pipeline.packs.base import BaseDomainPack

def get_pack():
    return BaseDomainPack(name="medical-evidence", version="0.1.0", api_version=2)
""",
        encoding="utf-8",
    )
    manifest = tmp_path / "medical.yaml"
    manifest.write_text(
        """
name: medical-evidence
version: 0.1.0
api_version: 2
entrypoints:
  pack: bad_pack.plugin:get_pack
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    manifests = discover_plugin_manifests([manifest])

    with pytest.raises(ValueError, match="api_version"):
        load_manifest_pack(manifests["medical-evidence"])


def test_entrypoint_discovery_can_load_external_pack(monkeypatch):
    from openclaw_pipeline.packs.base import BaseDomainPack
    from openclaw_pipeline.plugins import discover_entrypoint_packs

    class FakeEntryPoint:
        name = "engineering-research"
        value = "fake.plugin:get_pack"
        group = "openclaw_pipeline.packs"

        def load(self):
            return lambda: BaseDomainPack(name="engineering-research", version="0.1.0", api_version=1)

    monkeypatch.setattr(
        "openclaw_pipeline.plugins.metadata.entry_points",
        lambda: {"openclaw_pipeline.packs": [FakeEntryPoint()]},
    )

    packs = discover_entrypoint_packs()

    assert "engineering-research" in packs
    assert packs["engineering-research"].name == "engineering-research"
