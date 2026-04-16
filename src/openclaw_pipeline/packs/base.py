from __future__ import annotations

from dataclasses import dataclass, field


ALLOWED_PACK_ROLES = {"domain", "primary", "compatibility"}


@dataclass(frozen=True)
class ObjectKindSpec:
    kind: str
    display_name: str
    description: str
    canonical: bool = True
    schema_ref: str | None = None


@dataclass(frozen=True)
class WorkflowProfile:
    name: str
    description: str
    stages: list[str]
    supports_autopilot: bool = False


@dataclass(frozen=True)
class StageHandlerSpec:
    name: str
    pack: str
    handler_kind: str
    runtime_adapter: str
    entrypoint: str
    stage: str | None = None
    action_kind: str | None = None
    description: str = ""
    target_mode: str = "batch"
    supports_autopilot: bool = False
    safe_to_run: bool = False
    requires_truth_refresh: bool = False
    requires_signal_resync: bool = False


@dataclass(frozen=True)
class TruthProjectionSpec:
    name: str
    pack: str
    entrypoint: str
    description: str = ""


@dataclass(frozen=True)
class ObservationSurfaceSpec:
    name: str
    pack: str
    surface_kind: str
    entrypoint: str
    description: str = ""


@dataclass(frozen=True)
class ProcessorContractSpec:
    name: str
    pack: str
    entrypoint: str
    stage: str | None = None
    action_kind: str | None = None
    mode: str = "rule_based"
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    quality_hooks: tuple[str, ...] = ()
    description: str = ""


@dataclass
class BaseDomainPack:
    name: str
    version: str
    api_version: int
    role: str = "domain"
    compatibility_base: str | None = None
    _object_kinds: list[ObjectKindSpec] = field(default_factory=list)
    _workflow_profiles: list[WorkflowProfile] = field(default_factory=list)
    _discoverable_object_kinds: list[str] = field(default_factory=list)
    _extraction_profiles: list[object] = field(default_factory=list)
    _operation_profiles: list[object] = field(default_factory=list)
    _wiki_views: list[object] = field(default_factory=list)
    _stage_handlers: list[StageHandlerSpec] = field(default_factory=list)
    _truth_projection: TruthProjectionSpec | None = None
    _observation_surfaces: list[ObservationSurfaceSpec] = field(default_factory=list)
    _processor_contracts: list[ProcessorContractSpec] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.role not in ALLOWED_PACK_ROLES:
            raise ValueError(f"Invalid pack role '{self.role}' for pack '{self.name}'")
        if self.compatibility_base is not None:
            if not str(self.compatibility_base).strip():
                raise ValueError(
                    f"Pack '{self.name}' has invalid compatibility_base={self.compatibility_base!r}"
                )
            if self.role != "compatibility":
                raise ValueError(
                    f"Pack '{self.name}' has compatibility_base={self.compatibility_base!r} "
                    f"but role is '{self.role}'"
                )
        if self._truth_projection is not None and self._truth_projection.pack != self.name:
            raise ValueError(
                f"Pack '{self.name}' declares truth projection for "
                f"'{self._truth_projection.pack}'"
            )
        for spec in self._stage_handlers:
            if spec.pack != self.name:
                raise ValueError(
                    f"Pack '{self.name}' declares stage handler for '{spec.pack}'"
                )
        for spec in self._observation_surfaces:
            if spec.pack != self.name:
                raise ValueError(
                    f"Pack '{self.name}' declares observation surface for '{spec.pack}'"
                )
        for spec in self._processor_contracts:
            if spec.pack != self.name:
                raise ValueError(
                    f"Pack '{self.name}' declares processor contract for '{spec.pack}'"
                )

    def object_kinds(self) -> list[ObjectKindSpec]:
        return list(self._object_kinds)

    def workflow_profiles(self) -> list[WorkflowProfile]:
        return list(self._workflow_profiles)

    def discoverable_object_kinds(self) -> list[str]:
        if self._discoverable_object_kinds:
            return list(self._discoverable_object_kinds)
        return [item.kind for item in self._object_kinds]

    def profile(self, name: str) -> WorkflowProfile:
        for profile in self._workflow_profiles:
            if profile.name == name:
                return profile
        raise ValueError(f"Unknown workflow profile '{name}' for pack '{self.name}'")

    def extraction_profiles(self) -> list[object]:
        return list(self._extraction_profiles)

    def extraction_profile(self, name: str) -> object:
        for profile in self._extraction_profiles:
            if getattr(profile, "name", None) == name:
                return profile
        raise ValueError(f"Unknown extraction profile '{name}' for pack '{self.name}'")

    def operation_profiles(self) -> list[object]:
        return list(self._operation_profiles)

    def operation_profile(self, name: str) -> object:
        for profile in self._operation_profiles:
            if getattr(profile, "name", None) == name:
                return profile
        raise ValueError(f"Unknown operation profile '{name}' for pack '{self.name}'")

    def wiki_views(self) -> list[object]:
        return list(self._wiki_views)

    def wiki_view(self, name: str) -> object:
        for view in self._wiki_views:
            if getattr(view, "name", None) == name:
                return view
        raise ValueError(f"Unknown wiki view '{name}' for pack '{self.name}'")

    def stage_handlers(self) -> list[StageHandlerSpec]:
        return list(self._stage_handlers)

    def truth_projection(self) -> TruthProjectionSpec | None:
        return self._truth_projection

    def observation_surfaces(self) -> list[ObservationSurfaceSpec]:
        return list(self._observation_surfaces)

    def processor_contracts(self) -> list[ProcessorContractSpec]:
        return list(self._processor_contracts)
