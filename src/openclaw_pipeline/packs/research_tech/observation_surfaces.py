from __future__ import annotations

from pathlib import Path
from typing import Any

from ... import truth_api
from ..base import ObservationSurfaceSpec


def build_observation_surfaces(pack_name: str = "research-tech") -> list[ObservationSurfaceSpec]:
    return [
        ObservationSurfaceSpec(
            name="research-tech-signals",
            pack=pack_name,
            surface_kind="signals",
            entrypoint="openclaw_pipeline.packs.research_tech.observation_surfaces:build_signals",
            description="Research-tech signal builder",
        ),
        ObservationSurfaceSpec(
            name="research-tech-briefing",
            pack=pack_name,
            surface_kind="briefing",
            entrypoint="openclaw_pipeline.packs.research_tech.observation_surfaces:build_briefing",
            description="Research-tech briefing builder",
        ),
        ObservationSurfaceSpec(
            name="research-tech-production-chains",
            pack=pack_name,
            surface_kind="production_chains",
            entrypoint="openclaw_pipeline.packs.research_tech.observation_surfaces:build_production_chains",
            description="Research-tech production chain builder",
        ),
    ]


def build_signals(
    *,
    vault_dir: Path,
    pack_name: str | None = None,
    spec: ObservationSurfaceSpec | None = None,
) -> list[dict[str, Any]]:
    _ = pack_name, spec
    return truth_api._research_tech_build_signal_entries(vault_dir)


def build_briefing(
    *,
    vault_dir: Path,
    pack_name: str | None = None,
    spec: ObservationSurfaceSpec | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    _ = spec
    return truth_api._research_tech_build_briefing_snapshot(
        vault_dir,
        pack_name=pack_name,
        limit=limit,
    )


def build_production_chains(
    *,
    vault_dir: Path,
    pack_name: str | None = None,
    spec: ObservationSurfaceSpec | None = None,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    _ = pack_name, spec
    return truth_api._research_tech_list_production_chains(vault_dir, query=query, limit=limit)
