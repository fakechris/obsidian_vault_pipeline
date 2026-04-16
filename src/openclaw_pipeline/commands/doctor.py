from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..observation_surface_registry import (
    UI_SHELL_SURFACE_KINDS,
    compute_declared_observation_surface_integrity,
    list_effective_observation_surfaces,
)
from ..execution_contract_registry import (
    compute_declared_contract_integrity,
    list_effective_execution_contracts,
)
from ..pack_resolution import iter_compatible_packs
from ..processor_contract_registry import list_effective_processor_contracts
from ..packs.loader import (
    DEFAULT_PACK_NAME,
    DEFAULT_WORKFLOW_PACK_NAME,
    PRIMARY_PACK_NAME,
    load_pack,
)
from ..runtime import VaultLayout, iter_markdown_files, resolve_vault_dir
from ..truth_projection_registry import resolve_truth_projection_builder

_SHARED_SHELL_ROUTES = [
    {"path": "/", "kind": "builtin"},
    {"path": "/objects", "kind": "builtin"},
    {"path": "/search", "kind": "builtin"},
    {"path": "/actions", "kind": "builtin"},
    {"path": "/signals", "kind": "surface", "surface_kind": "signals"},
    {"path": "/briefing", "kind": "surface", "surface_kind": "briefing"},
    {"path": "/production", "kind": "surface", "surface_kind": "production_chains"},
]

_RESEARCH_SHELL_ROUTES = [
    "/evolution",
    "/clusters",
    "/cluster",
    "/atlas",
    "/deep-dives",
    "/events",
    "/contradictions",
    "/summaries",
]

_SHARED_SHELL_MUTATIONS = [
    "/actions/enqueue",
    "/actions/run-next",
    "/actions/run-batch",
    "/actions/retry",
    "/actions/dismiss",
]

_RESEARCH_SHELL_MUTATIONS = [
    "/contradictions/resolve",
    "/summaries/rebuild",
    "/evolution/review",
]

_EMBEDDED_RESEARCH_CAPABILITIES = [
    {"screen": "dashboard", "capability": "research_overview"},
    {"screen": "object/page", "capability": "research_review_affordances"},
    {"screen": "overview/topic", "capability": "research_review_affordances"},
]

_TRUTH_PROJECTION_ROW_FAMILIES = [
    {"name": "objects", "storage_table": "objects", "family_kind": "core_truth"},
    {"name": "claims", "storage_table": "claims", "family_kind": "core_truth"},
    {"name": "claim_evidence", "storage_table": "claim_evidence", "family_kind": "core_truth"},
    {"name": "relations", "storage_table": "relations", "family_kind": "core_truth"},
    {
        "name": "compiled_summaries",
        "storage_table": "compiled_summaries",
        "family_kind": "core_truth",
    },
    {
        "name": "contradictions",
        "storage_table": "contradictions",
        "family_kind": "core_truth",
    },
    {"name": "graph_edges", "storage_table": "graph_edges", "family_kind": "graph_projection"},
    {
        "name": "graph_clusters",
        "storage_table": "graph_clusters",
        "family_kind": "graph_projection",
    },
]


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in [current.parent, *current.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return current.parents[3]


def _doc_stem(pack_name: str, suffix: str) -> str:
    normalized = pack_name.replace("-", "_").upper()
    return f"{normalized}_{suffix}"


def _count_markdown(directory: Path) -> int:
    if not directory.exists():
        return 0
    return sum(1 for _ in iter_markdown_files(directory))


def _docs_payload(repo_root: Path, *, pack_name: str) -> dict[str, object]:
    pack_slug = pack_name.replace("_", "-")
    docs_root = repo_root / "docs"
    pack_docs_root = docs_root / pack_slug
    recipes_root = docs_root / "recipes" / pack_slug

    skillpack = pack_docs_root / f"{_doc_stem(pack_name, 'SKILLPACK')}.md"
    verify = pack_docs_root / f"{_doc_stem(pack_name, 'VERIFY')}.md"
    recipe_files = sorted(recipes_root.glob("*.md")) if recipes_root.exists() else []
    return {
        "skillpack": {"path": str(skillpack), "exists": skillpack.exists()},
        "verify": {"path": str(verify), "exists": verify.exists()},
        "recipes": {
            "path": str(recipes_root),
            "exists": recipes_root.exists(),
            "count": len(recipe_files),
            "files": [item.name for item in recipe_files],
        },
    }


def _vault_payload(vault_dir: Path | None) -> dict[str, object] | None:
    if vault_dir is None:
        return None
    layout = VaultLayout.from_vault(vault_dir)
    return {
        "vault_dir": str(layout.vault_dir),
        "raw_count": _count_markdown(layout.raw_dir),
        "clippings_count": _count_markdown(layout.clippings_dir),
        "pinboard_count": _count_markdown(layout.pinboard_dir),
        "processing_count": _count_markdown(layout.processing_dir),
        "processed_count": _count_markdown(layout.processed_dir),
        "evergreen_count": _count_markdown(layout.evergreen_dir),
        "knowledge_db_exists": layout.knowledge_db.exists(),
    }


def _stage_handler_payload(spec: object) -> dict[str, object]:
    return {
        "name": getattr(spec, "name", ""),
        "pack": getattr(spec, "pack", ""),
        "handler_kind": getattr(spec, "handler_kind", ""),
        "runtime_adapter": getattr(spec, "runtime_adapter", ""),
        "stage": getattr(spec, "stage", None),
        "action_kind": getattr(spec, "action_kind", None),
        "target_mode": getattr(spec, "target_mode", ""),
        "supports_autopilot": bool(getattr(spec, "supports_autopilot", False)),
        "safe_to_run": bool(getattr(spec, "safe_to_run", False)),
        "requires_truth_refresh": bool(getattr(spec, "requires_truth_refresh", False)),
        "requires_signal_resync": bool(getattr(spec, "requires_signal_resync", False)),
        "entrypoint": getattr(spec, "entrypoint", ""),
        "description": getattr(spec, "description", ""),
    }


def _truth_projection_payload(spec: object | None) -> dict[str, object] | None:
    if spec is None:
        return None
    return {
        "name": getattr(spec, "name", ""),
        "pack": getattr(spec, "pack", ""),
        "entrypoint": getattr(spec, "entrypoint", ""),
        "description": getattr(spec, "description", ""),
    }


def _observation_surface_payload(spec: object) -> dict[str, object]:
    return {
        "name": getattr(spec, "name", ""),
        "pack": getattr(spec, "pack", ""),
        "surface_kind": getattr(spec, "surface_kind", ""),
        "entrypoint": getattr(spec, "entrypoint", ""),
        "description": getattr(spec, "description", ""),
    }


def _processor_contract_payload(spec: object) -> dict[str, object]:
    return {
        "name": getattr(spec, "name", ""),
        "pack": getattr(spec, "pack", ""),
        "stage": getattr(spec, "stage", None),
        "action_kind": getattr(spec, "action_kind", None),
        "mode": getattr(spec, "mode", ""),
        "inputs": list(getattr(spec, "inputs", ()) or ()),
        "outputs": list(getattr(spec, "outputs", ()) or ()),
        "quality_hooks": list(getattr(spec, "quality_hooks", ()) or ()),
        "entrypoint": getattr(spec, "entrypoint", ""),
        "description": getattr(spec, "description", ""),
    }


def _extraction_profile_payload(spec: object) -> dict[str, object]:
    projection_target = getattr(spec, "projection_target", None)
    grounding_policy = getattr(spec, "grounding_policy", None)
    merge_policy = getattr(spec, "merge_policy", None)
    return {
        "name": getattr(spec, "name", ""),
        "pack": getattr(spec, "pack", ""),
        "input_object_kinds": list(getattr(spec, "input_object_kinds", ()) or ()),
        "output_mode": getattr(spec, "output_mode", ""),
        "fields": [
            {
                "name": getattr(field, "name", ""),
                "field_type": getattr(field, "field_type", ""),
                "required": bool(getattr(field, "required", False)),
            }
            for field in list(getattr(spec, "fields", ()) or ())
        ],
        "relations": [
            {
                "name": getattr(relation, "name", ""),
                "source_field": getattr(relation, "source_field", ""),
                "target_field": getattr(relation, "target_field", ""),
            }
            for relation in list(getattr(spec, "relations", ()) or ())
        ],
        "identifier_fields": list(getattr(spec, "identifier_fields", ()) or ()),
        "display_fields": list(getattr(spec, "display_fields", ()) or ()),
        "projection_target": {
            "object_kind": getattr(projection_target, "object_kind", ""),
            "channel": getattr(projection_target, "channel", ""),
            "target_name": getattr(projection_target, "target_name", None),
        },
        "grounding_policy": {
            "require_quote": bool(getattr(grounding_policy, "require_quote", True)),
            "include_char_offsets": bool(getattr(grounding_policy, "include_char_offsets", True)),
            "include_section_title": bool(getattr(grounding_policy, "include_section_title", True)),
        },
        "merge_policy": {
            "strategy": getattr(merge_policy, "strategy", ""),
            "allow_partial_updates": bool(getattr(merge_policy, "allow_partial_updates", False)),
        },
        "notes": getattr(spec, "notes", ""),
        "status": "declared",
        "provider_pack": getattr(spec, "pack", ""),
    }


def _operation_profile_payload(spec: object) -> dict[str, object]:
    return {
        "name": getattr(spec, "name", ""),
        "pack": getattr(spec, "pack", ""),
        "scope": getattr(spec, "scope", ""),
        "triggers": list(getattr(spec, "triggers", ()) or ()),
        "checks": [
            {
                "name": getattr(check, "name", ""),
                "description": getattr(check, "description", ""),
            }
            for check in list(getattr(spec, "checks", ()) or ())
        ],
        "proposal_types": [
            {
                "proposal_type": getattr(proposal, "proposal_type", ""),
                "queue_name": getattr(proposal, "queue_name", ""),
            }
            for proposal in list(getattr(spec, "proposal_types", ()) or ())
        ],
        "auto_fix_policy": getattr(spec, "auto_fix_policy", ""),
        "review_required": bool(getattr(spec, "review_required", False)),
        "status": "declared",
        "provider_pack": getattr(spec, "pack", ""),
    }


def _execution_contract_payload(spec: object) -> dict[str, object]:
    handler_spec = getattr(spec, "handler_spec")
    processor_contract = getattr(spec, "processor_contract")
    return {
        "stage": getattr(spec, "stage", None),
        "action_kind": getattr(spec, "action_kind", None),
        "runtime_adapter": getattr(handler_spec, "runtime_adapter", ""),
        "handler_pack": getattr(handler_spec, "pack", ""),
        "processor_pack": getattr(processor_contract, "pack", ""),
        "target_mode": getattr(handler_spec, "target_mode", ""),
        "safe_to_run": bool(getattr(handler_spec, "safe_to_run", False)),
        "requires_truth_refresh": bool(getattr(handler_spec, "requires_truth_refresh", False)),
        "requires_signal_resync": bool(getattr(handler_spec, "requires_signal_resync", False)),
        "mode": getattr(processor_contract, "mode", ""),
        "inputs": list(getattr(processor_contract, "inputs", ()) or ()),
        "outputs": list(getattr(processor_contract, "outputs", ()) or ()),
        "quality_hooks": list(getattr(processor_contract, "quality_hooks", ()) or ()),
        "handler_entrypoint": getattr(handler_spec, "entrypoint", ""),
        "processor_entrypoint": getattr(processor_contract, "entrypoint", ""),
    }


def _wiki_view_payload(spec: object) -> dict[str, object]:
    builder = str(getattr(spec, "builder", "compiled_markdown"))
    required_args: list[str] = []
    if builder == "object_page":
        required_args.append("object_id")
    if builder == "cluster_crystal":
        required_args.append("cluster_id")
    return {
        "name": getattr(spec, "name", ""),
        "pack": getattr(spec, "pack", ""),
        "builder": builder,
        "publish_target": getattr(spec, "publish_target", ""),
        "purpose_path": getattr(spec, "purpose_path", ""),
        "schema_path": getattr(spec, "schema_path", ""),
        "input_sources": [
            {
                "source_kind": getattr(item, "source_kind", ""),
                "description": getattr(item, "description", ""),
            }
            for item in list(getattr(spec, "input_sources", ()) or ())
        ],
        "required_args": required_args,
        "traceability_policy": {
            "include_sources": bool(
                getattr(getattr(spec, "traceability_policy", None), "include_sources", True)
            ),
            "include_generated_from": bool(
                getattr(
                    getattr(spec, "traceability_policy", None),
                    "include_generated_from",
                    True,
                )
            ),
        },
        "status": "declared",
        "provider_pack": getattr(spec, "pack", ""),
    }


def _truth_projection_contract_payload(pack_name: str) -> dict[str, object]:
    pack = load_pack(pack_name)
    declared_builder = _truth_projection_payload(pack.truth_projection())
    effective_builder = _truth_projection_payload(resolve_truth_projection_builder(pack_name=pack_name))
    return {
        "declared_builder": declared_builder,
        "effective_builder": effective_builder,
        "projection_registry_table": "truth_projections",
        "row_families": [
            {
                **item,
                "pack_scoped": True,
            }
            for item in _TRUTH_PROJECTION_ROW_FAMILIES
        ],
    }


def _shell_payload(pack_name: str) -> dict[str, object]:
    shell_integrity = compute_declared_observation_surface_integrity(pack_name=pack_name)
    shell_support = {
        str(item["surface_kind"]): item
        for item in shell_integrity["shell_surface_support"]
    }
    compatible_packs = iter_compatible_packs(pack_name)
    research_provider = next((pack.name for pack in compatible_packs if pack.name == PRIMARY_PACK_NAME), "")

    shared_routes: list[dict[str, object]] = []
    for route in _SHARED_SHELL_ROUTES:
        if route["kind"] == "builtin":
            shared_routes.append(
                {
                    "path": route["path"],
                    "status": "always_available",
                    "provider_pack": pack_name,
                }
            )
            continue
        support = shell_support[str(route["surface_kind"])]
        shared_routes.append(
            {
                "path": route["path"],
                "surface_kind": route["surface_kind"],
                "status": support["status"],
                "provider_pack": support["provider_pack"],
            }
        )

    research_status = (
        "declared"
        if research_provider == pack_name and research_provider
        else "inherited"
        if research_provider
        else "hidden"
    )
    research_routes = [
        {
            "path": path,
            "status": research_status,
            "provider_pack": research_provider,
        }
        for path in _RESEARCH_SHELL_ROUTES
    ]
    shared_mutations = [
        {
            "path": path,
            "status": "always_available",
            "provider_pack": pack_name,
        }
        for path in _SHARED_SHELL_MUTATIONS
    ]
    research_mutations = [
        {
            "path": path,
            "status": research_status,
            "provider_pack": research_provider,
        }
        for path in _RESEARCH_SHELL_MUTATIONS
    ]
    embedded_research_capabilities = [
        {
            "screen": item["screen"],
            "capability": item["capability"],
            "status": research_status,
            "provider_pack": research_provider,
        }
        for item in _EMBEDDED_RESEARCH_CAPABILITIES
    ]
    return {
        "shared_routes": shared_routes,
        "research_routes": research_routes,
        "shared_mutations": shared_mutations,
        "research_mutations": research_mutations,
        "embedded_research_capabilities": embedded_research_capabilities,
    }


def _contracts_payload(pack_name: str) -> dict[str, object]:
    pack = load_pack(pack_name)
    compatible_packs = iter_compatible_packs(pack)
    declared_stage_handlers = [_stage_handler_payload(spec) for spec in pack.stage_handlers()]
    declared_truth_projection = _truth_projection_payload(pack.truth_projection())
    declared_surfaces = [_observation_surface_payload(spec) for spec in pack.observation_surfaces()]
    declared_processor_contracts = [_processor_contract_payload(spec) for spec in pack.processor_contracts()]
    declared_wiki_views = [_wiki_view_payload(spec) for spec in pack.wiki_views()]
    declared_extraction_profiles = [_extraction_profile_payload(spec) for spec in pack.extraction_profiles()]
    declared_operation_profiles = [_operation_profile_payload(spec) for spec in pack.operation_profiles()]

    effective_stage_handlers: list[dict[str, object]] = []
    seen_stage_keys: set[tuple[str, str, str]] = set()
    for compatible_pack in compatible_packs:
        for spec in compatible_pack.stage_handlers():
            key = (
                str(getattr(spec, "handler_kind", "")),
                str(getattr(spec, "runtime_adapter", "")),
                str(getattr(spec, "stage", "") or getattr(spec, "action_kind", "")),
            )
            if key in seen_stage_keys:
                continue
            seen_stage_keys.add(key)
            effective_stage_handlers.append(_stage_handler_payload(spec))

    effective_surfaces = [
        _observation_surface_payload(spec)
        for spec in list_effective_observation_surfaces(pack_name=pack_name)
    ]

    effective_processor_contracts = [
        _processor_contract_payload(spec)
        for spec in list_effective_processor_contracts(pack_name=pack_name)
    ]
    effective_execution_contracts = [
        _execution_contract_payload(spec)
        for spec in list_effective_execution_contracts(pack_name=pack_name)
    ]

    return {
        "declared": {
            "stage_handlers": declared_stage_handlers,
            "truth_projection": declared_truth_projection,
            "observation_surfaces": declared_surfaces,
            "processor_contracts": declared_processor_contracts,
            "wiki_views": declared_wiki_views,
            "extraction_profiles": declared_extraction_profiles,
            "operation_profiles": declared_operation_profiles,
        },
        "effective": {
            "stage_handlers": effective_stage_handlers,
            "truth_projection": _truth_projection_payload(
                resolve_truth_projection_builder(pack_name=pack_name)
            ),
            "observation_surfaces": effective_surfaces,
            "processor_contracts": effective_processor_contracts,
            "execution_contracts": effective_execution_contracts,
        },
        "contract_integrity": {
            **compute_declared_contract_integrity(pack_name=pack_name),
            "observation_surfaces": compute_declared_observation_surface_integrity(pack_name=pack_name),
        },
        "truth_projection_contract": _truth_projection_contract_payload(pack_name),
        "shell": _shell_payload(pack_name),
        "wiki_views": declared_wiki_views,
        "extraction_profiles": declared_extraction_profiles,
        "operation_profiles": declared_operation_profiles,
        "contract_notes": {
            "compatibility_behavior": (
                "Compatibility packs inherit stage handlers, truth projection, and observation "
                "surfaces from compatibility_base only when they do not declare their own contract."
            ),
            "processor_control_plane": (
                "Processor contracts declare mode, inputs, outputs, and quality hooks without "
                "requiring core runtime patches; execution contracts pair them with runtime "
                "handlers."
            ),
            "media_pack_guidance": (
                "External packs should implement stage handlers, processor contracts, a truth "
                "projection builder, and observation surfaces inside the pack rather than "
                "patching core runtime modules."
            ),
            "ui_shell_required_surfaces": (
                "The current shared UI shell assumes pack support for these observation surfaces: "
                + ", ".join(UI_SHELL_SURFACE_KINDS)
                + "."
            ),
            "research_shell_behavior": (
                "Research-specific routes stay hidden unless the current pack resolves through "
                "the research-tech compatibility chain."
            ),
            "embedded_research_behavior": (
                "Object/topic/dashboard screens only expose research review affordances when the "
                "current pack resolves through the research-tech compatibility chain."
            ),
            "mutation_shell_behavior": (
                "Action queue mutations remain available across the shared shell, while "
                "research review mutations resolve only through the research-tech "
                "compatibility chain."
            ),
            "wiki_view_behavior": (
                "Wiki view specs are pack-owned declarations. Compatibility packs should "
                "publish their own view specs even when they reuse shared builders or "
                "inherit research shell behavior elsewhere."
            ),
            "truth_projection_behavior": (
                "Truth projection builders emit pack-scoped row families into shared "
                "knowledge.db tables. Graph families may be empty, but builders should "
                "still preserve pack namespace and registry metadata."
            ),
            "profile_contract_behavior": (
                "Extraction and operation profiles are pack-owned declarations. They define "
                "record shapes, grounding rules, review queues, and proposal flows without "
                "requiring core runtime branches."
            ),
        },
    }


def _payload(pack_name: str, vault_dir: Path | None) -> dict[str, object]:
    repo_root = _repo_root()
    pack = load_pack(pack_name)
    return {
        "defaults": {
            "workflow_pack": DEFAULT_WORKFLOW_PACK_NAME,
            "compatibility_pack": DEFAULT_PACK_NAME,
            "primary_pack": PRIMARY_PACK_NAME,
        },
        "pack": {
            "name": pack.name,
            "role": getattr(pack, "role", "domain"),
            "compatibility_base": getattr(pack, "compatibility_base", None),
            "workflow_profiles": [profile.name for profile in pack.workflow_profiles()],
            "extraction_profiles": [profile.name for profile in pack.extraction_profiles()],
            "operation_profiles": [profile.name for profile in pack.operation_profiles()],
            "wiki_views": [view.name for view in pack.wiki_views()],
        },
        "storage": {
            "selected_engine": "sqlite",
            "pglite_migration": "defer",
            "reason": (
                "knowledge.db is currently a Python-native derived/truth-aware store; "
                "PGlite should only be revisited if browser/JS-native or remote-Postgres "
                "parity becomes a hard requirement."
            ),
        },
        "docs": _docs_payload(repo_root, pack_name=pack_name),
        "vault": _vault_payload(vault_dir),
        "contracts": _contracts_payload(pack_name),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect pack/runtime operational health and explain why SQLite remains the "
            "selected engine while PGlite migration is deferred."
        )
    )
    parser.add_argument("--vault-dir", type=Path, default=None, help="Optional vault directory for health checks")
    parser.add_argument(
        "--pack",
        default=PRIMARY_PACK_NAME,
        help=(
            f"Pack name to inspect (primary pack: {PRIMARY_PACK_NAME}; "
            f"compatibility pack: {DEFAULT_PACK_NAME})"
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    args = parser.parse_args(argv)

    payload = _payload(args.pack, resolve_vault_dir(args.vault_dir) if args.vault_dir else None)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    print(f"Pack: {payload['pack']['name']} [{payload['pack']['role']}]")
    print(f"Defaults: workflow={DEFAULT_WORKFLOW_PACK_NAME} compatibility={DEFAULT_PACK_NAME}")
    print("Storage: sqlite (PGlite migration deferred)")
    print(f"Skillpack doc: {payload['docs']['skillpack']['path']}")
    print(f"Verify doc: {payload['docs']['verify']['path']}")
    if payload["vault"]:
        vault = payload["vault"]
        print(
            "Vault: "
            f"raw={vault['raw_count']} clippings={vault['clippings_count']} "
            f"pinboard={vault['pinboard_count']} processing={vault['processing_count']} "
            f"processed={vault['processed_count']} "
            f"evergreen={vault['evergreen_count']} knowledge_db_exists={vault['knowledge_db_exists']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
