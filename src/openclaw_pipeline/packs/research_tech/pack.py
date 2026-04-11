from __future__ import annotations

from ..base import BaseDomainPack
from .extraction_profiles import RESEARCH_TECH_EXTRACTION_PROFILES
from .operation_profiles import RESEARCH_TECH_OPERATION_PROFILES
from .profiles import RESEARCH_TECH_WORKFLOW_PROFILES
from .schemas import RESEARCH_TECH_OBJECT_KINDS
from .wiki_views import RESEARCH_TECH_WIKI_VIEWS


def get_pack() -> BaseDomainPack:
    return BaseDomainPack(
        name="research-tech",
        version="0.1.0",
        api_version=1,
        role="primary",
        _object_kinds=list(RESEARCH_TECH_OBJECT_KINDS),
        _workflow_profiles=list(RESEARCH_TECH_WORKFLOW_PROFILES),
        _extraction_profiles=list(RESEARCH_TECH_EXTRACTION_PROFILES),
        _operation_profiles=list(RESEARCH_TECH_OPERATION_PROFILES),
        _wiki_views=list(RESEARCH_TECH_WIKI_VIEWS),
    )
