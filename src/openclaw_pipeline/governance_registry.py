from __future__ import annotations

from .pack_resolution import coerce_pack, iter_compatible_packs
from .packs.base import BaseDomainPack, GovernanceSpec


def _matching_governance_specs(
    *,
    pack_name: str | BaseDomainPack | None,
    governance_name: str | None = None,
) -> list[tuple[str, GovernanceSpec]]:
    matches: list[tuple[str, GovernanceSpec]] = []
    for candidate in iter_compatible_packs(pack_name):
        for spec in candidate.governance_specs():
            if governance_name and spec.name != governance_name:
                continue
            matches.append((candidate.name, spec))
    return matches


def resolve_governance_spec(
    *,
    pack_name: str | BaseDomainPack | None,
    governance_name: str | None = None,
) -> GovernanceSpec:
    pack = coerce_pack(pack_name)
    declared = next(
        (
            spec
            for spec in pack.governance_specs()
            if governance_name is None or spec.name == governance_name
        ),
        None,
    )
    if declared is not None:
        return declared
    matches = _matching_governance_specs(pack_name=pack, governance_name=governance_name)
    if matches:
        return matches[0][1]
    if governance_name:
        raise ValueError(f"Unknown governance contract '{governance_name}' for pack '{pack.name}'")
    raise ValueError(f"Pack '{pack.name}' does not declare any governance contracts")


def list_effective_governance_specs(
    *,
    pack_name: str | BaseDomainPack | None,
) -> list[GovernanceSpec]:
    seen_names: set[str] = set()
    specs: list[GovernanceSpec] = []
    for _, spec in _matching_governance_specs(pack_name=pack_name):
        if spec.name in seen_names:
            continue
        seen_names.add(spec.name)
        specs.append(spec)
    return specs


def describe_governance_contract(
    *,
    pack_name: str | BaseDomainPack | None,
    governance_name: str | None = None,
) -> dict[str, object]:
    requested_pack = str(pack_name or "")
    try:
        pack = coerce_pack(pack_name)
    except ValueError:
        return {
            "requested_pack": requested_pack,
            "status": "missing",
            "provider_pack": "",
            "provider_name": governance_name or "",
            "description": "",
            "review_queue_count": 0,
            "signal_rule_count": 0,
            "resolver_rule_count": 0,
            "review_queue_names": [],
            "signal_rule_names": [],
            "resolver_rule_names": [],
        }
    declared = next(
        (
            spec
            for spec in pack.governance_specs()
            if governance_name is None or spec.name == governance_name
        ),
        None,
    )
    if declared is not None:
        status = "declared"
        spec = declared
    else:
        effective = next(
            (
                item
                for item in list_effective_governance_specs(pack_name=pack)
                if governance_name is None or item.name == governance_name
            ),
            None,
        )
        if effective is None:
            return {
                "requested_pack": pack.name,
                "status": "missing",
                "provider_pack": "",
                "provider_name": governance_name or "",
                "description": "",
                "review_queue_count": 0,
                "signal_rule_count": 0,
                "resolver_rule_count": 0,
                "review_queue_names": [],
                "signal_rule_names": [],
                "resolver_rule_names": [],
            }
        status = "inherited"
        spec = effective
    return {
        "requested_pack": pack.name,
        "status": status,
        "provider_pack": spec.pack,
        "provider_name": spec.name,
        "description": spec.description,
        "review_queue_count": len(spec.review_queues),
        "signal_rule_count": len(spec.signal_rules),
        "resolver_rule_count": len(spec.resolver_rules),
        "review_queue_names": [queue.name for queue in spec.review_queues],
        "signal_rule_names": [rule.signal_type for rule in spec.signal_rules],
        "resolver_rule_names": [rule.name for rule in spec.resolver_rules],
    }


def describe_signal_rule_contract(
    *,
    pack_name: str | BaseDomainPack | None,
    signal_type: str,
) -> dict[str, object]:
    normalized_signal_type = str(signal_type or "").strip()
    requested_pack = str(pack_name or "")
    if not normalized_signal_type:
        return {
            "requested_pack": requested_pack,
            "status": "missing",
            "provider_pack": "",
            "provider_name": "",
            "governance_name": "",
            "signal_type": "",
            "source_contract_kind": "",
            "source_contract_name": "",
            "resolver_rule": "",
            "auto_queue": False,
        }
    try:
        pack = coerce_pack(pack_name)
    except ValueError:
        return {
            "requested_pack": requested_pack,
            "status": "missing",
            "provider_pack": "",
            "provider_name": "",
            "governance_name": "",
            "signal_type": normalized_signal_type,
            "source_contract_kind": "",
            "source_contract_name": "",
            "resolver_rule": "",
            "auto_queue": False,
        }
    for candidate in iter_compatible_packs(pack):
        for spec in candidate.governance_specs():
            for rule in spec.signal_rules:
                if rule.signal_type != normalized_signal_type:
                    continue
                return {
                    "requested_pack": pack.name,
                    "status": "declared" if candidate.name == pack.name else "inherited",
                    "provider_pack": candidate.name,
                    "provider_name": spec.name,
                    "governance_name": spec.name,
                    "signal_type": rule.signal_type,
                    "source_contract_kind": rule.source_contract_kind,
                    "source_contract_name": rule.source_contract_name,
                    "resolver_rule": rule.resolver_rule or "",
                    "auto_queue": bool(rule.auto_queue),
                }
    return {
        "requested_pack": pack.name if pack is not None else requested_pack,
        "status": "missing",
        "provider_pack": "",
        "provider_name": "",
        "governance_name": "",
        "signal_type": normalized_signal_type,
        "source_contract_kind": "",
        "source_contract_name": "",
        "resolver_rule": "",
        "auto_queue": False,
    }


def describe_resolver_rule_contract(
    *,
    pack_name: str | BaseDomainPack | None,
    rule_name: str,
) -> dict[str, object]:
    normalized_rule_name = str(rule_name or "").strip()
    requested_pack = str(pack_name or "")
    if not normalized_rule_name:
        return {
            "requested_pack": requested_pack,
            "status": "missing",
            "provider_pack": "",
            "provider_name": "",
            "governance_name": "",
            "rule_name": "",
            "resolution_kind": "",
            "dispatch_mode": "",
            "target_name": "",
            "executable": False,
            "safe_to_run": False,
            "description": "",
        }
    try:
        pack = coerce_pack(pack_name)
    except ValueError:
        return {
            "requested_pack": requested_pack,
            "status": "missing",
            "provider_pack": "",
            "provider_name": "",
            "governance_name": "",
            "rule_name": normalized_rule_name,
            "resolution_kind": "",
            "dispatch_mode": "",
            "target_name": "",
            "executable": False,
            "safe_to_run": False,
            "description": "",
        }
    for candidate in iter_compatible_packs(pack):
        for spec in candidate.governance_specs():
            for rule in spec.resolver_rules:
                if rule.name != normalized_rule_name:
                    continue
                return {
                    "requested_pack": pack.name,
                    "status": "declared" if candidate.name == pack.name else "inherited",
                    "provider_pack": candidate.name,
                    "provider_name": spec.name,
                    "governance_name": spec.name,
                    "rule_name": rule.name,
                    "resolution_kind": rule.resolution_kind,
                    "dispatch_mode": rule.dispatch_mode,
                    "target_name": rule.target_name,
                    "executable": bool(rule.executable),
                    "safe_to_run": bool(rule.safe_to_run),
                    "description": rule.description,
                }
    return {
        "requested_pack": pack.name,
        "status": "missing",
        "provider_pack": "",
        "provider_name": "",
        "governance_name": "",
        "rule_name": normalized_rule_name,
        "resolution_kind": "",
        "dispatch_mode": "",
        "target_name": "",
        "executable": False,
        "safe_to_run": False,
        "description": "",
    }
