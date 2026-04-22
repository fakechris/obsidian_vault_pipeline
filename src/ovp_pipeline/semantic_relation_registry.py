from __future__ import annotations

from .pack_resolution import coerce_pack, iter_compatible_packs
from .packs.base import BaseDomainPack, SemanticRelationContractSpec


def resolve_semantic_relation_contract(
    *,
    pack_name: str | BaseDomainPack | None,
    contract_name: str,
) -> SemanticRelationContractSpec:
    pack = coerce_pack(pack_name)
    declared = next(
        (
            spec
            for spec in pack.semantic_relation_contracts()
            if spec.name == contract_name
        ),
        None,
    )
    if declared is not None:
        return declared
    effective = next(
        (
            spec
            for spec in list_effective_semantic_relation_contracts(pack_name=pack)
            if spec.name == contract_name
        ),
        None,
    )
    if effective is not None:
        return effective
    raise ValueError(
        f"Unknown semantic relation contract '{contract_name}' for pack '{pack.name}'"
    )


def list_effective_semantic_relation_contracts(
    *,
    pack_name: str | BaseDomainPack | None,
) -> list[SemanticRelationContractSpec]:
    seen_names: set[str] = set()
    specs: list[SemanticRelationContractSpec] = []
    for candidate in iter_compatible_packs(pack_name):
        for spec in candidate.semantic_relation_contracts():
            if spec.name in seen_names:
                continue
            seen_names.add(spec.name)
            specs.append(spec)
    return specs


def describe_semantic_relation_contract(
    *,
    pack_name: str | BaseDomainPack | None,
    contract_name: str,
) -> dict[str, object]:
    requested_pack = str(pack_name or "")
    try:
        pack = coerce_pack(pack_name)
    except ValueError:
        return {
            "requested_pack": requested_pack,
            "status": "missing",
            "provider_pack": "",
            "provider_name": contract_name,
            "relation_type_count": 0,
            "review_queue_name": "",
            "write_policy": "",
        }

    declared = next(
        (
            spec
            for spec in pack.semantic_relation_contracts()
            if spec.name == contract_name
        ),
        None,
    )
    if declared is not None:
        status = "declared"
        spec = declared
    else:
        effective = next(
            (
                spec
                for spec in list_effective_semantic_relation_contracts(pack_name=pack)
                if spec.name == contract_name
            ),
            None,
        )
        if effective is None:
            return {
                "requested_pack": pack.name,
                "status": "missing",
                "provider_pack": "",
                "provider_name": contract_name,
                "relation_type_count": 0,
                "review_queue_name": "",
                "write_policy": "",
            }
        status = "inherited"
        spec = effective

    return {
        "requested_pack": pack.name,
        "status": status,
        "provider_pack": spec.pack,
        "provider_name": spec.name,
        "relation_type_count": len(spec.relation_types),
        "review_queue_name": spec.review_queue_name,
        "write_policy": spec.write_policy,
    }
