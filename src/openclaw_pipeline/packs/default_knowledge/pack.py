from __future__ import annotations

from ..base import BaseDomainPack
from .extraction_profiles import DEFAULT_EXTRACTION_PROFILES
from .operation_profiles import DEFAULT_OPERATION_PROFILES
from .profiles import DEFAULT_KNOWLEDGE_AUTOPILOT_PROFILE, DEFAULT_KNOWLEDGE_FULL_PROFILE
from .schemas import DEFAULT_KNOWLEDGE_OBJECT_KINDS
from .wiki_views import DEFAULT_WIKI_VIEWS


def get_pack() -> BaseDomainPack:
    return BaseDomainPack(
        name="default-knowledge",
        version="0.1.0",
        api_version=1,
        _object_kinds=list(DEFAULT_KNOWLEDGE_OBJECT_KINDS),
        _workflow_profiles=[
            DEFAULT_KNOWLEDGE_FULL_PROFILE,
            DEFAULT_KNOWLEDGE_AUTOPILOT_PROFILE,
        ],
        _extraction_profiles=list(DEFAULT_EXTRACTION_PROFILES),
        _operation_profiles=list(DEFAULT_OPERATION_PROFILES),
        _wiki_views=list(DEFAULT_WIKI_VIEWS),
    )
