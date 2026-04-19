from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OperationCheckSpec:
    name: str
    description: str


@dataclass(frozen=True)
class OperationProposalSpec:
    proposal_type: str
    queue_name: str
    description: str = ""


@dataclass(frozen=True)
class OperationProfileSpec:
    name: str
    pack: str
    scope: str
    triggers: list[str] = field(default_factory=list)
    checks: list[OperationCheckSpec] = field(default_factory=list)
    proposal_types: list[OperationProposalSpec] = field(default_factory=list)
    auto_fix_policy: str = "manual"
    review_required: bool = True
