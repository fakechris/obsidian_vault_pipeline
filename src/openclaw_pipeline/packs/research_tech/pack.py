from __future__ import annotations

from .assembly_recipes import build_assembly_recipes
from ..base import BaseDomainPack, TruthProjectionSpec
from .artifacts import build_artifact_specs
from .extraction_profiles import RESEARCH_TECH_EXTRACTION_PROFILES
from .governance import build_governance_specs
from .handlers import build_stage_handlers
from .observation_surfaces import build_observation_surfaces
from .operation_profiles import RESEARCH_TECH_OPERATION_PROFILES
from .processor_contracts import build_processor_contracts
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
        _stage_handlers=build_stage_handlers(),
        _processor_contracts=build_processor_contracts(),
        _artifact_specs=build_artifact_specs(),
        _assembly_recipes=build_assembly_recipes(),
        _governance_specs=build_governance_specs(),
        _truth_projection=TruthProjectionSpec(
            name="research-tech-default",
            pack="research-tech",
            entrypoint="openclaw_pipeline.packs.research_tech.truth_projection:build_truth_projection",
            description="Default research-tech truth projection",
        ),
        _observation_surfaces=build_observation_surfaces(),
    )
