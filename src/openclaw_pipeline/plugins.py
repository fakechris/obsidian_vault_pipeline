from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module, metadata
from pathlib import Path
from typing import Iterable

import yaml

from .packs.base import BaseDomainPack


PACK_API_VERSION = 1
ENTRYPOINT_GROUP = "openclaw_pipeline.packs"


@dataclass(frozen=True)
class PluginManifest:
    name: str
    version: str
    api_version: int
    pack_entrypoint: str
    manifest_path: Path


def _validate_manifest_data(data: dict[str, object], manifest_path: Path) -> PluginManifest:
    for field_name in ("name", "version", "api_version", "entrypoints"):
        if field_name not in data:
            raise ValueError(f"Plugin manifest {manifest_path} missing required field: {field_name}")

    entrypoints = data["entrypoints"]
    if not isinstance(entrypoints, dict) or "pack" not in entrypoints:
        raise ValueError(f"Plugin manifest {manifest_path} missing required field: entrypoints.pack")

    return PluginManifest(
        name=str(data["name"]),
        version=str(data["version"]),
        api_version=int(data["api_version"]),
        pack_entrypoint=str(entrypoints["pack"]),
        manifest_path=manifest_path,
    )


def discover_plugin_manifests(manifest_paths: Iterable[Path]) -> dict[str, PluginManifest]:
    manifests: dict[str, PluginManifest] = {}
    for manifest_path in manifest_paths:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        manifest = _validate_manifest_data(raw, Path(manifest_path))
        manifests[manifest.name] = manifest
    return manifests


def _load_pack_entrypoint(entrypoint: str) -> BaseDomainPack:
    module_name, attr_name = entrypoint.split(":", 1)
    target = getattr(import_module(module_name), attr_name)
    pack = target() if callable(target) else target
    if not isinstance(pack, BaseDomainPack):
        raise TypeError(f"Pack entrypoint {entrypoint} did not return BaseDomainPack")
    if pack.api_version != PACK_API_VERSION:
        raise ValueError(
            f"Pack {pack.name} api_version {pack.api_version} is incompatible with core api_version {PACK_API_VERSION}"
        )
    return pack


def load_manifest_pack(manifest: PluginManifest) -> BaseDomainPack:
    if manifest.api_version != PACK_API_VERSION:
        raise ValueError(
            f"Plugin manifest {manifest.manifest_path} api_version {manifest.api_version} "
            f"is incompatible with core api_version {PACK_API_VERSION}"
        )
    return _load_pack_entrypoint(manifest.pack_entrypoint)


def discover_entrypoint_packs() -> dict[str, BaseDomainPack]:
    discovered: dict[str, BaseDomainPack] = {}
    entry_points = metadata.entry_points()
    if hasattr(entry_points, "select"):
        candidates = entry_points.select(group=ENTRYPOINT_GROUP)
    else:
        candidates = entry_points.get(ENTRYPOINT_GROUP, [])

    for entry_point in candidates:
        loaded = entry_point.load()
        pack = loaded() if callable(loaded) else loaded
        if not isinstance(pack, BaseDomainPack):
            raise TypeError(f"Entry point {entry_point.value} did not return BaseDomainPack")
        if pack.api_version != PACK_API_VERSION:
            raise ValueError(
                f"Pack {pack.name} api_version {pack.api_version} is incompatible with core api_version {PACK_API_VERSION}"
            )
        discovered[pack.name] = pack

    return discovered
