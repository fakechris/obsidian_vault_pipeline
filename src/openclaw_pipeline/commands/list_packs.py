from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ..packs.loader import list_builtin_packs
from ..plugins import discover_plugin_manifests


def _serialize_pack(pack: object, *, source: str) -> dict[str, object]:
    profile_names = [profile.name for profile in pack.workflow_profiles()]
    return {
        "name": pack.name,
        "role": getattr(pack, "role", "domain"),
        "compatibility_base": getattr(pack, "compatibility_base", None),
        "version": pack.version,
        "api_version": pack.api_version,
        "profiles": profile_names,
        "source": source,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List built-in and externally discoverable domain packs."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON instead of a human-readable list",
    )
    args = parser.parse_args(argv)

    builtin = [_serialize_pack(pack, source="builtin") for pack in list_builtin_packs()]

    manifest_env = os.environ.get("OPENCLAW_PACK_MANIFESTS", "")
    manifest_paths = [Path(item) for item in manifest_env.split(":") if item]
    manifests = []
    if manifest_paths:
        for manifest in discover_plugin_manifests(manifest_paths).values():
            manifests.append(
                {
                    "name": manifest.name,
                    "role": "external",
                    "compatibility_base": None,
                    "version": manifest.version,
                    "api_version": manifest.api_version,
                    "profiles": [],
                    "source": "manifest",
                    "manifest_path": str(manifest.manifest_path),
                }
            )

    payload = {"builtin": builtin, "external": manifests}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    for item in builtin:
        line = f"{item['name']} [{item['role']}]"
        if item["compatibility_base"]:
            line += f" -> {item['compatibility_base']}"
        line += f" profiles={','.join(item['profiles'])}"
        print(line)
    for item in manifests:
        print(f"{item['name']} [external] manifest={item['manifest_path']}")
    return 0
