from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_external_pack_can_be_discovered_via_manifest(tmp_path, monkeypatch):
    from ovp_pipeline.plugins import discover_plugin_manifests, load_manifest_pack

    package_root = tmp_path / "fake_pack"
    package_root.mkdir()
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "plugin.py").write_text(
        """
from ovp_pipeline.packs.base import BaseDomainPack

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
    from ovp_pipeline.plugins import discover_plugin_manifests

    manifest = tmp_path / "broken.yaml"
    manifest.write_text("version: 0.1.0\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required field"):
        discover_plugin_manifests([manifest])


def test_incompatible_api_versions_fail_clearly(tmp_path, monkeypatch):
    from ovp_pipeline.plugins import discover_plugin_manifests, load_manifest_pack

    package_root = tmp_path / "bad_pack"
    package_root.mkdir()
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "plugin.py").write_text(
        """
from ovp_pipeline.packs.base import BaseDomainPack

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
    from ovp_pipeline.packs.base import BaseDomainPack
    from ovp_pipeline.plugins import discover_entrypoint_packs

    class FakeEntryPoint:
        name = "engineering-research"
        value = "fake.plugin:get_pack"
        group = "ovp.packs"

        def load(self):
            return lambda: BaseDomainPack(name="engineering-research", version="0.1.0", api_version=1)

    monkeypatch.setattr(
        "ovp_pipeline.plugins.metadata.entry_points",
        lambda: {"ovp.packs": [FakeEntryPoint()]},
    )

    packs = discover_entrypoint_packs()

    assert "engineering-research" in packs
    assert packs["engineering-research"].name == "engineering-research"


def test_load_pack_reads_manifest_paths_with_platform_separator(tmp_path, monkeypatch):
    from ovp_pipeline.packs.loader import load_pack

    package_root = tmp_path / "platform_pack"
    package_root.mkdir()
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "plugin.py").write_text(
        """
from ovp_pipeline.packs.base import BaseDomainPack

def get_pack():
    return BaseDomainPack(name="platform-pack", version="0.1.0", api_version=1)
""",
        encoding="utf-8",
    )
    first = tmp_path / "unused.yaml"
    first.write_text(
        """
name: unused-pack
version: 0.1.0
api_version: 1
entrypoints:
  pack: platform_pack.plugin:get_pack
""".strip(),
        encoding="utf-8",
    )
    second = tmp_path / "platform.yaml"
    second.write_text(
        """
name: platform-pack
version: 0.1.0
api_version: 1
entrypoints:
  pack: platform_pack.plugin:get_pack
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("OVP_PACK_MANIFESTS", f"{first}{os.pathsep}{second}")

    pack = load_pack("platform-pack")

    assert pack.name == "platform-pack"
