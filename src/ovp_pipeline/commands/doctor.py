from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path

from ..artifact_registry import list_effective_artifact_specs
from ..assembly_recipe_registry import resolve_assembly_source_contract
from ..assembly_recipe_registry import list_effective_assembly_recipes
from ..governance_registry import describe_governance_contract, list_effective_governance_specs
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
from ..semantic_relation_registry import list_effective_semantic_relation_contracts
import sqlite3
from ..packs.loader import (
    DEFAULT_PACK_NAME,
    DEFAULT_WORKFLOW_PACK_NAME,
    PRIMARY_PACK_NAME,
    load_pack,
)
from ..runtime import VaultLayout, iter_markdown_files, resolve_vault_dir
from ..truth_api import get_operational_runtime_state
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
    "/graph",
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


def _object_kind_payload(spec: object, *, discoverable: set[str]) -> dict[str, object]:
    return {
        "kind": getattr(spec, "kind", ""),
        "display_name": getattr(spec, "display_name", ""),
        "description": getattr(spec, "description", ""),
        "canonical": bool(getattr(spec, "canonical", False)),
        "schema_ref": getattr(spec, "schema_ref", None),
        "discoverable": getattr(spec, "kind", "") in discoverable,
        "status": "declared",
        "provider_pack": "",
    }


def _workflow_profile_payload(spec: object) -> dict[str, object]:
    return {
        "name": getattr(spec, "name", ""),
        "description": getattr(spec, "description", ""),
        "stages": list(getattr(spec, "stages", ()) or ()),
        "supports_autopilot": bool(getattr(spec, "supports_autopilot", False)),
        "status": "declared",
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


def _artifact_spec_payload(spec: object, *, status: str = "declared", provider_pack: str | None = None) -> dict[str, object]:
    identity_policy = getattr(spec, "identity_policy", None)
    evidence_policy = getattr(spec, "evidence_policy", None)
    storage_policy = getattr(spec, "storage_policy", None)
    lifecycle_policy = getattr(spec, "lifecycle_policy", None)
    resolved_provider_pack = provider_pack if provider_pack is not None else getattr(spec, "pack", "")
    return {
        "name": getattr(spec, "name", ""),
        "pack": getattr(spec, "pack", ""),
        "provider_pack": resolved_provider_pack,
        "status": status,
        "layer": getattr(spec, "layer", ""),
        "family": getattr(spec, "family", ""),
        "object_kind": getattr(spec, "object_kind", None),
        "description": getattr(spec, "description", ""),
        "fields": [
            {
                "name": getattr(field, "name", ""),
                "field_type": getattr(field, "field_type", ""),
                "required": bool(getattr(field, "required", False)),
            }
            for field in list(getattr(spec, "fields", ()) or ())
        ],
        "identity_policy": {
            "id_strategy": getattr(identity_policy, "id_strategy", ""),
            "id_fields": list(getattr(identity_policy, "id_fields", ()) or ()),
            "subject_fields": list(getattr(identity_policy, "subject_fields", ()) or ()),
        },
        "evidence_policy": {
            "requires_evidence": bool(getattr(evidence_policy, "requires_evidence", True)),
            "require_quote": bool(getattr(evidence_policy, "require_quote", True)),
            "require_source_slug": bool(getattr(evidence_policy, "require_source_slug", True)),
            "require_traceability_links": bool(
                getattr(evidence_policy, "require_traceability_links", True)
            ),
        },
        "storage_policy": {
            "storage_mode": getattr(storage_policy, "storage_mode", ""),
            "canonical_path_template": getattr(storage_policy, "canonical_path_template", None),
            "truth_row_family": getattr(storage_policy, "truth_row_family", None),
            "review_queue_name": getattr(storage_policy, "review_queue_name", None),
        },
        "lifecycle_policy": {
            "mutable": bool(getattr(lifecycle_policy, "mutable", True)),
            "review_required_on_create": bool(
                getattr(lifecycle_policy, "review_required_on_create", False)
            ),
            "review_required_on_update": bool(
                getattr(lifecycle_policy, "review_required_on_update", False)
            ),
            "projection_rebuild_policy": getattr(
                lifecycle_policy,
                "projection_rebuild_policy",
                "",
            ),
        },
    }


def _assembly_recipe_payload(
    spec: object,
    *,
    status: str = "declared",
    provider_pack: str | None = None,
    requested_pack: str | None = None,
) -> dict[str, object]:
    audience = getattr(spec, "audience", None)
    freshness_policy = getattr(spec, "freshness_policy", None)
    output = getattr(spec, "output", None)
    resolved_provider_pack = provider_pack if provider_pack is not None else getattr(spec, "pack", "")
    requested_scope = requested_pack if requested_pack is not None else getattr(spec, "pack", None)
    try:
        source_contract = resolve_assembly_source_contract(pack_name=requested_scope, recipe=spec)
    except ValueError:
        source_contract = {
            "source_provider_pack": "",
            "source_provider_name": "",
            "source_status": "missing",
        }
    return {
        "name": getattr(spec, "name", ""),
        "pack": getattr(spec, "pack", ""),
        "provider_pack": resolved_provider_pack,
        "status": status,
        "recipe_kind": getattr(spec, "recipe_kind", ""),
        "description": getattr(spec, "description", ""),
        "source_contract_kind": getattr(spec, "source_contract_kind", ""),
        "source_contract_name": getattr(spec, "source_contract_name", ""),
        **source_contract,
        "inputs": [
            {
                "source_kind": getattr(item, "source_kind", ""),
                "description": getattr(item, "description", ""),
                "required": bool(getattr(item, "required", False)),
            }
            for item in list(getattr(spec, "inputs", ()) or ())
        ],
        "audience": {
            "audience": getattr(audience, "audience", ""),
            "interaction_mode": getattr(audience, "interaction_mode", ""),
        },
        "freshness_policy": {
            "cache_mode": getattr(freshness_policy, "cache_mode", ""),
            "invalidation_signals": list(getattr(freshness_policy, "invalidation_signals", ()) or ()),
        },
        "output": {
            "output_mode": getattr(output, "output_mode", ""),
            "publish_target": getattr(output, "publish_target", ""),
        },
    }


def _governance_spec_payload(
    spec: object,
    *,
    status: str = "declared",
    provider_pack: str | None = None,
) -> dict[str, object]:
    resolved_provider_pack = provider_pack if provider_pack is not None else getattr(spec, "pack", "")
    return {
        "name": getattr(spec, "name", ""),
        "pack": getattr(spec, "pack", ""),
        "provider_pack": resolved_provider_pack,
        "status": status,
        "description": getattr(spec, "description", ""),
        "review_queues": [
            {
                "name": getattr(queue, "name", ""),
                "description": getattr(queue, "description", ""),
                "operation_profiles": list(getattr(queue, "operation_profiles", ()) or ()),
                "proposal_types": list(getattr(queue, "proposal_types", ()) or ()),
                "review_mode": getattr(queue, "review_mode", ""),
            }
            for queue in list(getattr(spec, "review_queues", ()) or ())
        ],
        "signal_rules": [
            {
                "signal_type": getattr(rule, "signal_type", ""),
                "description": getattr(rule, "description", ""),
                "source_contract_kind": getattr(rule, "source_contract_kind", ""),
                "source_contract_name": getattr(rule, "source_contract_name", ""),
                "resolver_rule": getattr(rule, "resolver_rule", None),
                "auto_queue": bool(getattr(rule, "auto_queue", False)),
            }
            for rule in list(getattr(spec, "signal_rules", ()) or ())
        ],
        "resolver_rules": [
            {
                "name": getattr(rule, "name", ""),
                "description": getattr(rule, "description", ""),
                "resolution_kind": getattr(rule, "resolution_kind", ""),
                "target_name": getattr(rule, "target_name", ""),
                "dispatch_mode": getattr(rule, "dispatch_mode", ""),
                "executable": bool(getattr(rule, "executable", False)),
                "safe_to_run": bool(getattr(rule, "safe_to_run", False)),
            }
            for rule in list(getattr(spec, "resolver_rules", ()) or ())
        ],
    }


def _semantic_relation_contract_payload(
    spec: object,
    *,
    status: str = "declared",
    provider_pack: str | None = None,
) -> dict[str, object]:
    resolved_provider_pack = provider_pack if provider_pack is not None else getattr(spec, "pack", "")
    return {
        "name": getattr(spec, "name", ""),
        "pack": getattr(spec, "pack", ""),
        "provider_pack": resolved_provider_pack,
        "status": status,
        "description": getattr(spec, "description", ""),
        "source_contract_kind": getattr(spec, "source_contract_kind", ""),
        "source_contract_name": getattr(spec, "source_contract_name", ""),
        "review_queue_name": getattr(spec, "review_queue_name", ""),
        "write_policy": getattr(spec, "write_policy", ""),
        "relation_types": [
            {
                "name": getattr(relation, "name", ""),
                "description": getattr(relation, "description", ""),
                "source_object_kinds": list(
                    getattr(relation, "source_object_kinds", ()) or ()
                ),
                "target_object_kinds": list(
                    getattr(relation, "target_object_kinds", ()) or ()
                ),
                "directionality": getattr(relation, "directionality", ""),
                "evidence_required": bool(getattr(relation, "evidence_required", True)),
                "review_required": bool(getattr(relation, "review_required", True)),
            }
            for relation in getattr(spec, "relation_types", [])
        ],
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
        "governance_contract": describe_governance_contract(pack_name=pack_name),
        "shared_routes": shared_routes,
        "research_routes": research_routes,
        "shared_mutations": shared_mutations,
        "research_mutations": research_mutations,
        "embedded_research_capabilities": embedded_research_capabilities,
    }


def _contracts_payload(pack_name: str) -> dict[str, object]:
    pack = load_pack(pack_name)
    compatible_packs = iter_compatible_packs(pack)
    discoverable_object_kinds = set(pack.discoverable_object_kinds())
    declared_workflow_profiles = [_workflow_profile_payload(spec) for spec in pack.workflow_profiles()]
    declared_object_kinds = [
        {
            **_object_kind_payload(spec, discoverable=discoverable_object_kinds),
            "provider_pack": pack.name,
        }
        for spec in pack.object_kinds()
    ]
    declared_stage_handlers = [_stage_handler_payload(spec) for spec in pack.stage_handlers()]
    declared_truth_projection = _truth_projection_payload(pack.truth_projection())
    declared_surfaces = [_observation_surface_payload(spec) for spec in pack.observation_surfaces()]
    declared_processor_contracts = [_processor_contract_payload(spec) for spec in pack.processor_contracts()]
    declared_artifact_specs = [
        _artifact_spec_payload(spec, provider_pack=pack.name) for spec in pack.artifact_specs()
    ]
    declared_assembly_recipes = [
        _assembly_recipe_payload(spec, provider_pack=pack.name, requested_pack=pack.name)
        for spec in pack.assembly_recipes()
    ]
    declared_governance_specs = [
        _governance_spec_payload(spec, provider_pack=pack.name) for spec in pack.governance_specs()
    ]
    declared_semantic_relation_contracts = [
        _semantic_relation_contract_payload(spec, provider_pack=pack.name)
        for spec in pack.semantic_relation_contracts()
    ]
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
    declared_artifact_names = {spec.name for spec in pack.artifact_specs()}
    effective_artifact_specs = [
        _artifact_spec_payload(
            spec,
            status="declared" if spec.name in declared_artifact_names else "inherited",
            provider_pack=spec.pack,
        )
        for spec in list_effective_artifact_specs(pack_name=pack_name)
    ]
    declared_assembly_recipe_names = {spec.name for spec in pack.assembly_recipes()}
    effective_assembly_recipes = [
        _assembly_recipe_payload(
            spec,
            status="declared" if spec.name in declared_assembly_recipe_names else "inherited",
            provider_pack=spec.pack,
            requested_pack=pack.name,
        )
        for spec in list_effective_assembly_recipes(pack_name=pack_name)
    ]
    declared_governance_names = {spec.name for spec in pack.governance_specs()}
    effective_governance_specs = [
        _governance_spec_payload(
            spec,
            status="declared" if spec.name in declared_governance_names else "inherited",
            provider_pack=spec.pack,
        )
        for spec in list_effective_governance_specs(pack_name=pack_name)
    ]
    declared_semantic_relation_names = {
        spec.name for spec in pack.semantic_relation_contracts()
    }
    effective_semantic_relation_contracts = [
        _semantic_relation_contract_payload(
            spec,
            status="declared" if spec.name in declared_semantic_relation_names else "inherited",
            provider_pack=spec.pack,
        )
        for spec in list_effective_semantic_relation_contracts(pack_name=pack_name)
    ]
    effective_execution_contracts = [
        _execution_contract_payload(spec)
        for spec in list_effective_execution_contracts(pack_name=pack_name)
    ]

    return {
        "declared": {
            "workflow_profiles": declared_workflow_profiles,
            "object_kinds": declared_object_kinds,
            "stage_handlers": declared_stage_handlers,
            "truth_projection": declared_truth_projection,
            "observation_surfaces": declared_surfaces,
            "processor_contracts": declared_processor_contracts,
            "artifact_specs": declared_artifact_specs,
            "assembly_recipes": declared_assembly_recipes,
            "governance_specs": declared_governance_specs,
            "semantic_relation_contracts": declared_semantic_relation_contracts,
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
            "artifact_specs": effective_artifact_specs,
            "assembly_recipes": effective_assembly_recipes,
            "governance_specs": effective_governance_specs,
            "semantic_relation_contracts": effective_semantic_relation_contracts,
            "execution_contracts": effective_execution_contracts,
        },
        "workflow_profiles": declared_workflow_profiles,
        "contract_integrity": {
            **compute_declared_contract_integrity(pack_name=pack_name),
            "observation_surfaces": compute_declared_observation_surface_integrity(pack_name=pack_name),
        },
        "truth_projection_contract": _truth_projection_contract_payload(pack_name),
        "shell": _shell_payload(pack_name),
        "object_kinds": declared_object_kinds,
        "artifact_specs": declared_artifact_specs,
        "assembly_recipes": declared_assembly_recipes,
        "governance_specs": declared_governance_specs,
        "semantic_relation_contracts": declared_semantic_relation_contracts,
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
            "object_kind_behavior": (
                "Object kinds are pack-owned domain vocabulary. Packs should declare which "
                "kinds are canonical and discoverable instead of teaching core a new object "
                "taxonomy."
            ),
            "workflow_profile_behavior": (
                "Workflow profiles are pack-owned execution plans. They should declare stage "
                "order and autopilot support explicitly instead of depending on hidden runtime "
                "defaults."
            ),
            "artifact_contract_behavior": (
                "Artifact specs are pack-owned declarations for canonical, access, and "
                "governance artifact families. They should make persistent knowledge shapes "
                "explicit instead of relying on truth-store rows alone as the architecture."
            ),
            "assembly_recipe_behavior": (
                "Assembly recipes are pack-owned declarations for operator and reader-facing "
                "compiled products. They should connect observation surfaces and wiki views "
                "to explicit access-layer products instead of leaving those products implicit."
            ),
            "governance_contract_behavior": (
                "Governance specs are pack-owned declarations for review queues, signal "
                "semantics, and resolver rules explicit. They should make follow-up routing explicit "
                "instead of leaving queue ownership and action reachability scattered across "
                "runtime modules."
            ),
            "semantic_relation_contract_behavior": (
                "Semantic relation contracts are pack-owned vocabulary and promotion gates. "
                "They should declare evidence, review queues, and write policy before any "
                "extractor can promote relation candidates into canonical graph truth."
            ),
        },
    }


def _reuse_payload(vault_dir: Path | None, *, pack_name: str) -> dict[str, object]:
    """Phase 32 reuse-event health snapshot for the requested pack.

    Reads ``60-Logs/knowledge.db`` directly so it stays read-only and never
    triggers a rebuild from the doctor command.
    """
    if vault_dir is None:
        return {
            "events_total": 0,
            "trusted_events_total": 0,
            "trusted_share": 0.0,
            "never_reused_count": 0,
            "knowledge_db_exists": False,
        }
    db_path = VaultLayout.from_vault(vault_dir).knowledge_db
    if not db_path.exists():
        return {
            "events_total": 0,
            "trusted_events_total": 0,
            "trusted_share": 0.0,
            "never_reused_count": 0,
            "knowledge_db_exists": False,
        }
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(trusted),0) FROM reuse_events WHERE pack = ?",
                (pack_name,),
            ).fetchone()
            never_rows = conn.execute(
                """
                SELECT objects.canonical_path
                FROM objects
                LEFT JOIN reuse_events
                       ON reuse_events.pack = objects.pack
                      AND reuse_events.object_id = objects.object_id
                WHERE objects.pack = ?
                GROUP BY objects.object_id
                HAVING COUNT(reuse_events.event_id) = 0
                """,
                (pack_name,),
            ).fetchall()
    except sqlite3.OperationalError:
        return {
            "events_total": 0,
            "trusted_events_total": 0,
            "trusted_share": 0.0,
            "never_reused_count": 0,
            "knowledge_db_exists": True,
            "schema_stale": True,
        }
    events_total = int(row[0])
    trusted_events_total = int(row[1])
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    never_count = 0
    for (canonical_path,) in never_rows:
        if not canonical_path:
            continue
        raw = Path(str(canonical_path))
        path = raw if raw.is_absolute() else vault_dir / raw
        if not path.exists():
            never_count += 1
            continue
        mtime_dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if mtime_dt <= cutoff:
            never_count += 1
    return {
        "events_total": events_total,
        "trusted_events_total": trusted_events_total,
        "trusted_share": (trusted_events_total / events_total) if events_total else 0.0,
        "never_reused_count": never_count,
        "knowledge_db_exists": True,
    }


def _promotion_health_payload(vault_dir: Path | None, *, pack_name: str) -> dict[str, object]:
    """Phase 34 — concept lane rates + unreviewed canonical mutation count.

    Reads ``audit_events`` (event_type='promotion' / 'zone_violation') from the
    knowledge.db. Since the lint check already records mtime-vs-audit
    violations as ``zone_violation`` events on each scan, the doctor count is
    a simple SQL aggregation.
    """
    from ..promotion_policy import (
        LANE_AUTO,
        LANE_ESCALATE,
        LANE_HOLD,
        LANE_REJECT,
        collect_pack_signals,
        evaluate_concept,
    )

    if vault_dir is None:
        return {
            "lane_counts": {},
            "unreviewed_canonical_mutations": 0,
            "knowledge_db_exists": False,
        }
    db_path = VaultLayout.from_vault(vault_dir).knowledge_db

    # Lane counts come from a fresh policy evaluation across current candidates.
    # The DB feeds evidence_kinds + open-contradiction signals into the strict
    # path; without them, research-tech would escalate every candidate.
    lane_counts: dict[str, int] = {LANE_AUTO: 0, LANE_ESCALATE: 0, LANE_HOLD: 0, LANE_REJECT: 0}
    try:
        from ..concept_registry import ConceptRegistry

        pack = load_pack(pack_name)
        registry = ConceptRegistry(vault_dir).load()
        kinds_by_id, disputed_ids = collect_pack_signals(
            db_path,
            pack_name=pack.name,
            candidates_dir=vault_dir / "10-Knowledge" / "Evergreen" / "_Candidates",
        )
        for entry in registry.candidates:
            decision = evaluate_concept(
                entry,
                pack=pack,
                registry=registry,
                evidence_kinds=kinds_by_id.get(entry.slug, frozenset()),
                has_open_contradiction=entry.slug in disputed_ids,
            )
            lane_counts[decision.lane] = lane_counts.get(decision.lane, 0) + 1
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "lane evaluation failed, returning zero counts: %s", exc
        )

    unreviewed_mutations = 0
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    """
                    SELECT COUNT(*) FROM audit_events
                    WHERE event_type = 'zone_violation'
                    """
                ).fetchone()
                unreviewed_mutations = int(row[0]) if row else 0
        except sqlite3.OperationalError:
            pass

    return {
        "lane_counts": lane_counts,
        "unreviewed_canonical_mutations": unreviewed_mutations,
        "knowledge_db_exists": db_path.exists(),
    }


def _candidate_consistency_payload(vault_dir: Path | None, *, pack_name: str) -> dict[str, object]:
    """Read-only consistency check for registry candidates and review queues."""
    if vault_dir is None:
        return {
            "ok": True,
            "registry_candidates": 0,
            "candidate_files": 0,
            "review_queue_files": 0,
            "missing_candidate_files": [],
            "orphan_candidate_files": [],
            "missing_review_queue_files": [],
            "orphan_review_queue_files": [],
            "misplaced_review_queue_files": [],
            "evaluation_error": "",
        }

    from ..concept_registry import ConceptRegistry
    from ..promotion_policy import (
        LANE_ESCALATE,
        LANE_REJECT,
        collect_pack_signals,
        evaluate_concept,
    )

    layout = VaultLayout.from_vault(vault_dir)
    candidate_dir = layout.evergreen_dir / "_Candidates"
    concepts_queue_dir = layout.review_queue_dir / "concepts"
    rejected_queue_dir = layout.review_queue_dir / "rejected-concepts"

    registry = ConceptRegistry(vault_dir).load()
    candidates = list(registry.candidates)
    registry_slugs = {entry.slug for entry in candidates}
    candidate_file_slugs = {
        path.stem for path in candidate_dir.glob("*.md")
    } if candidate_dir.exists() else set()
    concepts_queue_slugs = {
        path.stem for path in concepts_queue_dir.glob("*.json")
    } if concepts_queue_dir.exists() else set()
    rejected_queue_slugs = {
        path.stem for path in rejected_queue_dir.glob("*.json")
    } if rejected_queue_dir.exists() else set()

    expected_concepts_queue: set[str] = set()
    expected_rejected_queue: set[str] = set()
    evaluation_error = ""
    try:
        pack = load_pack(pack_name)
        kinds_by_id, disputed_ids = collect_pack_signals(
            layout.knowledge_db,
            pack_name=pack.name,
            candidates_dir=candidate_dir,
        )
        for entry in candidates:
            decision = evaluate_concept(
                entry,
                pack=pack,
                registry=registry,
                evidence_kinds=kinds_by_id.get(entry.slug, frozenset()),
                has_open_contradiction=entry.slug in disputed_ids,
            )
            if decision.lane == LANE_ESCALATE:
                expected_concepts_queue.add(entry.slug)
            elif decision.lane == LANE_REJECT:
                expected_rejected_queue.add(entry.slug)
    except (sqlite3.OperationalError, ValueError, KeyError, OSError) as exc:
        evaluation_error = f"{type(exc).__name__}: {exc}"
        expected_concepts_queue = set()
        expected_rejected_queue = set()

    misplaced_in_rejected = expected_rejected_queue & concepts_queue_slugs
    misplaced_in_concepts = expected_concepts_queue & rejected_queue_slugs
    misplaced_review_queue_files = sorted(
        [
            *(f"{slug}:concepts->rejected-concepts" for slug in misplaced_in_rejected),
            *(f"{slug}:rejected-concepts->concepts" for slug in misplaced_in_concepts),
        ]
    )
    missing_candidate_files = sorted(registry_slugs - candidate_file_slugs)
    orphan_candidate_files = sorted(candidate_file_slugs - registry_slugs)
    missing_review_queue_files = sorted(
        ((expected_concepts_queue - concepts_queue_slugs) - misplaced_in_concepts)
        | ((expected_rejected_queue - rejected_queue_slugs) - misplaced_in_rejected)
    )
    orphan_review_queue_files = sorted(
        (concepts_queue_slugs | rejected_queue_slugs) - registry_slugs
    )
    ok = not (
        missing_candidate_files
        or orphan_candidate_files
        or missing_review_queue_files
        or orphan_review_queue_files
        or misplaced_review_queue_files
        or evaluation_error
    )
    return {
        "ok": ok,
        "registry_candidates": len(registry_slugs),
        "candidate_files": len(candidate_file_slugs),
        "review_queue_files": len(concepts_queue_slugs) + len(rejected_queue_slugs),
        "missing_candidate_files": missing_candidate_files,
        "orphan_candidate_files": orphan_candidate_files,
        "missing_review_queue_files": missing_review_queue_files,
        "orphan_review_queue_files": orphan_review_queue_files,
        "misplaced_review_queue_files": misplaced_review_queue_files,
        "evaluation_error": evaluation_error,
    }


def _feedback_payload(vault_dir: Path | None, *, pack_name: str) -> dict[str, object]:
    """Phase 36 — query→candidate yield + query→reuse ratio.

    Reads ``feedback_yield`` events from ``60-Logs/pipeline.jsonl`` (no DB
    dependency, so the doctor stays read-only). Counts candidates produced and
    open questions logged. Reuse share is computed against the existing reuse
    payload which already counts ``trusted_reuse_event`` events.
    """
    if vault_dir is None:
        return {
            "candidate_yield": 0,
            "open_questions": 0,
            "writing_prompts": 0,
            "proposed_relations": 0,
            "events_total": 0,
        }
    log = vault_dir / "60-Logs" / "pipeline.jsonl"
    if not log.exists():
        return {
            "candidate_yield": 0,
            "open_questions": 0,
            "writing_prompts": 0,
            "proposed_relations": 0,
            "events_total": 0,
        }
    counts = {
        "candidate_concept": 0,
        "open_question": 0,
        "writing_prompt": 0,
        "proposed_relation": 0,
    }
    total = 0
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event_type") != "feedback_yield":
            continue
        if pack_name and row.get("pack") and row["pack"] != pack_name:
            continue
        total += 1
        stream = row.get("stream", "")
        if stream in counts:
            counts[stream] += 1
    return {
        "candidate_yield": counts["candidate_concept"],
        "open_questions": counts["open_question"],
        "writing_prompts": counts["writing_prompt"],
        "proposed_relations": counts["proposed_relation"],
        "events_total": total,
    }


def _relations_health_payload(vault_dir: Path | None, *, pack_name: str) -> dict[str, object]:
    """Phase 35 — semantic relation extraction + promotion snapshot.

    Counts unpromoted candidates in the review queue, archived rejections, and
    promoted ``relations`` rows. Extraction-rate / promotion-rate / contradiction-rate
    are derived in user-facing print, not stored, to keep the payload minimal.
    """
    if vault_dir is None:
        return {
            "candidates_in_queue": 0,
            "rejected_archived": 0,
            "relations_total": 0,
            "knowledge_db_exists": False,
        }
    layout = VaultLayout.from_vault(vault_dir)
    queue_dir = layout.review_queue_dir / "semantic-relations"
    rejected_dir = layout.derived_dir / "rejected-relations"
    candidates_in_queue = (
        sum(1 for _ in queue_dir.glob("*.json")) if queue_dir.exists() else 0
    )
    rejected_archived = (
        sum(1 for _ in rejected_dir.glob("*.json")) if rejected_dir.exists() else 0
    )
    relations_total = 0
    db_path = layout.knowledge_db
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM relations WHERE pack = ?",
                    (pack_name,),
                ).fetchone()
                relations_total = int(row[0]) if row else 0
        except sqlite3.OperationalError:
            pass
    return {
        "candidates_in_queue": candidates_in_queue,
        "rejected_archived": rejected_archived,
        "relations_total": relations_total,
        "knowledge_db_exists": db_path.exists(),
    }


def _evidence_health_payload(vault_dir: Path | None, *, pack_name: str) -> dict[str, object]:
    """Phase 33 Evidence Health snapshot — per-status counts + top-10 stale paths.

    Reads ``60-Logs/knowledge.db`` directly so the doctor stays read-only and
    never triggers a rebuild. Counts cover both ``claim_evidence`` and
    ``relations`` since Phase 35 promotes relations through the same pipeline.
    """
    if vault_dir is None:
        return {
            "claim_evidence": {},
            "relations": {},
            "top_stale_sources": [],
            "knowledge_db_exists": False,
        }
    db_path = VaultLayout.from_vault(vault_dir).knowledge_db
    if not db_path.exists():
        return {
            "claim_evidence": {},
            "relations": {},
            "top_stale_sources": [],
            "knowledge_db_exists": False,
        }
    try:
        with sqlite3.connect(db_path) as conn:
            claim_status_rows = conn.execute(
                """
                SELECT status, COUNT(*)
                FROM claim_evidence
                WHERE pack = ?
                GROUP BY status
                """,
                (pack_name,),
            ).fetchall()
            relation_status_rows = conn.execute(
                """
                SELECT status, COUNT(*)
                FROM relations
                WHERE pack = ?
                GROUP BY status
                """,
                (pack_name,),
            ).fetchall()
            stale_rows = conn.execute(
                """
                SELECT source_slug, COUNT(*) AS stale_count
                FROM claim_evidence
                WHERE pack = ?
                  AND status IN ('stale', 'broken')
                GROUP BY source_slug
                ORDER BY stale_count DESC, source_slug
                LIMIT 10
                """,
                (pack_name,),
            ).fetchall()
    except sqlite3.OperationalError:
        return {
            "claim_evidence": {},
            "relations": {},
            "top_stale_sources": [],
            "knowledge_db_exists": True,
            "schema_stale": True,
        }
    return {
        "claim_evidence": {str(status or ""): int(count) for status, count in claim_status_rows},
        "relations": {str(status or ""): int(count) for status, count in relation_status_rows},
        "top_stale_sources": [
            {"source_slug": str(slug), "stale_count": int(count)}
            for slug, count in stale_rows
        ],
        "knowledge_db_exists": True,
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
            "artifact_specs": [spec.name for spec in pack.artifact_specs()],
            "assembly_recipes": [spec.name for spec in pack.assembly_recipes()],
            "governance_specs": [spec.name for spec in pack.governance_specs()],
            "semantic_relation_contracts": [
                spec.name for spec in pack.semantic_relation_contracts()
            ],
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
        "reuse": _reuse_payload(vault_dir, pack_name=pack_name),
        "evidence_health": _evidence_health_payload(vault_dir, pack_name=pack_name),
        "promotion_health": _promotion_health_payload(vault_dir, pack_name=pack_name),
        "candidate_consistency": _candidate_consistency_payload(vault_dir, pack_name=pack_name),
        "relations_health": _relations_health_payload(vault_dir, pack_name=pack_name),
        "feedback": _feedback_payload(vault_dir, pack_name=pack_name),
        "runtime_state": get_operational_runtime_state(vault_dir) if vault_dir else None,
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
    reuse = payload.get("reuse") or {}
    if reuse.get("knowledge_db_exists"):
        events = int(reuse.get("events_total", 0))
        trusted = int(reuse.get("trusted_events_total", 0))
        share = float(reuse.get("trusted_share", 0.0))
        never = int(reuse.get("never_reused_count", 0))
        print(
            "Reuse: "
            f"events={events} trusted={trusted} trusted_share={share:.0%} "
            f"never_reused={never}"
        )
    evidence_health = payload.get("evidence_health") or {}
    if evidence_health.get("knowledge_db_exists"):
        claim_status = evidence_health.get("claim_evidence") or {}
        relation_status = evidence_health.get("relations") or {}
        claim_summary = " ".join(
            f"{key}={value}" for key, value in sorted(claim_status.items())
        ) or "(empty)"
        relation_summary = " ".join(
            f"{key}={value}" for key, value in sorted(relation_status.items())
        ) or "(empty)"
        print(f"Evidence Health (claim_evidence): {claim_summary}")
        print(f"Evidence Health (relations): {relation_summary}")
        for row in (evidence_health.get("top_stale_sources") or [])[:5]:
            print(f"  stale: {row['source_slug']} (rows={row['stale_count']})")
    promotion_health = payload.get("promotion_health") or {}
    if promotion_health:
        lanes = promotion_health.get("lane_counts") or {}
        unreviewed = promotion_health.get("unreviewed_canonical_mutations", 0)
        lane_summary = " ".join(
            f"{key}={value}" for key, value in sorted(lanes.items())
        ) or "(no candidates)"
        print(f"Promotion lanes: {lane_summary}")
        print(f"Unreviewed canonical mutations: {unreviewed}")
    candidate_consistency = payload.get("candidate_consistency") or {}
    if candidate_consistency:
        print(
            "Candidate consistency: "
            f"ok={candidate_consistency.get('ok')} "
            f"registry={candidate_consistency.get('registry_candidates', 0)} "
            f"files={candidate_consistency.get('candidate_files', 0)} "
            f"queue={candidate_consistency.get('review_queue_files', 0)}"
        )
    relations_health = payload.get("relations_health") or {}
    if relations_health:
        queued = int(relations_health.get("candidates_in_queue", 0))
        rejected = int(relations_health.get("rejected_archived", 0))
        promoted = int(relations_health.get("relations_total", 0))
        print(
            f"Relations: queued={queued} rejected_archived={rejected} promoted={promoted}"
        )
    feedback = payload.get("feedback") or {}
    if feedback.get("events_total", 0):
        print(
            "Feedback yield: "
            f"candidates={feedback['candidate_yield']} "
            f"open_questions={feedback['open_questions']} "
            f"writing_prompts={feedback['writing_prompts']} "
            f"proposed_relations={feedback['proposed_relations']}"
        )
    runtime_state = payload.get("runtime_state") or {}
    if runtime_state:
        metrics = runtime_state.get("metrics") or {}
        print(
            "Runtime state: "
            f"status={runtime_state.get('status', 'unknown')} "
            f"open_repair_markers={metrics.get('open_projection_repair_markers', 0)} "
            f"expired_repair_leases={metrics.get('expired_projection_repair_leases', 0)} "
            f"queued_actions={metrics.get('queued_actions', 0)} "
            f"stale_running_actions={metrics.get('stale_running_actions', 0)} "
            f"failed_actions={metrics.get('failed_actions', 0)} "
            f"pipeline_events={metrics.get('pipeline_events', 0)} "
            f"reuse_surfaces={metrics.get('reuse_surfaces', 0)}"
        )
        for item in (runtime_state.get("attention") or [])[:5]:
            if isinstance(item, dict):
                print(f"  attention: {item.get('message', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
