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


@dataclass(frozen=True)
class ArtifactFieldSpec:
    name: str
    field_type: str
    description: str
    required: bool = False


@dataclass(frozen=True)
class ArtifactIdentityPolicy:
    id_strategy: str = "deterministic"
    id_fields: list[str] = field(default_factory=list)
    subject_fields: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ArtifactEvidencePolicy:
    requires_evidence: bool = True
    require_quote: bool = True
    require_source_slug: bool = True
    require_traceability_links: bool = True


@dataclass(frozen=True)
class ArtifactStoragePolicy:
    storage_mode: str
    canonical_path_template: str | None = None
    truth_row_family: str | None = None
    review_queue_name: str | None = None


@dataclass(frozen=True)
class ArtifactLifecyclePolicy:
    mutable: bool = True
    review_required_on_create: bool = False
    review_required_on_update: bool = False
    projection_rebuild_policy: str = "on_derived_refresh"


@dataclass(frozen=True)
class ArtifactSpec:
    name: str
    pack: str
    layer: str
    family: str
    object_kind: str | None = None
    description: str = ""
    fields: list[ArtifactFieldSpec] = field(default_factory=list)
    identity_policy: ArtifactIdentityPolicy = field(default_factory=ArtifactIdentityPolicy)
    evidence_policy: ArtifactEvidencePolicy = field(default_factory=ArtifactEvidencePolicy)
    storage_policy: ArtifactStoragePolicy = field(
        default_factory=lambda: ArtifactStoragePolicy(storage_mode="markdown_note")
    )
    lifecycle_policy: ArtifactLifecyclePolicy = field(default_factory=ArtifactLifecyclePolicy)


@dataclass(frozen=True)
class AssemblyInputSpec:
    source_kind: str
    description: str
    required: bool = True


@dataclass(frozen=True)
class AssemblyAudienceSpec:
    audience: str
    interaction_mode: str = "read_only"


@dataclass(frozen=True)
class AssemblyFreshnessPolicy:
    cache_mode: str = "on_demand"
    invalidation_signals: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AssemblyOutputSpec:
    output_mode: str
    publish_target: str


@dataclass(frozen=True)
class AssemblyRecipeSpec:
    name: str
    pack: str
    recipe_kind: str
    description: str
    source_contract_kind: str
    source_contract_name: str
    inputs: list[AssemblyInputSpec] = field(default_factory=list)
    audience: AssemblyAudienceSpec = field(
        default_factory=lambda: AssemblyAudienceSpec(audience="operator")
    )
    freshness_policy: AssemblyFreshnessPolicy = field(default_factory=AssemblyFreshnessPolicy)
    output: AssemblyOutputSpec = field(
        default_factory=lambda: AssemblyOutputSpec(
            output_mode="markdown",
            publish_target="compiled_markdown",
        )
    )


@dataclass(frozen=True)
class ReviewQueueSpec:
    name: str
    description: str
    operation_profiles: list[str] = field(default_factory=list)
    proposal_types: list[str] = field(default_factory=list)
    review_mode: str = "human_review"


@dataclass(frozen=True)
class SignalRuleSpec:
    signal_type: str
    description: str
    source_contract_kind: str = "observation_surface"
    source_contract_name: str = "signals"
    resolver_rule: str | None = None
    auto_queue: bool = False


@dataclass(frozen=True)
class ResolverRuleSpec:
    name: str
    description: str
    resolution_kind: str
    target_name: str
    dispatch_mode: str = "navigate"
    executable: bool = False
    safe_to_run: bool = False


@dataclass(frozen=True)
class GovernanceSpec:
    name: str
    pack: str
    description: str = ""
    review_queues: list[ReviewQueueSpec] = field(default_factory=list)
    signal_rules: list[SignalRuleSpec] = field(default_factory=list)
    resolver_rules: list[ResolverRuleSpec] = field(default_factory=list)


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
    _artifact_specs: list[ArtifactSpec] = field(default_factory=list)
    _assembly_recipes: list[AssemblyRecipeSpec] = field(default_factory=list)
    _governance_specs: list[GovernanceSpec] = field(default_factory=list)

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
        for spec in self._artifact_specs:
            if spec.pack != self.name:
                raise ValueError(
                    f"Pack '{self.name}' declares artifact spec for '{spec.pack}'"
                )
        for spec in self._assembly_recipes:
            if spec.pack != self.name:
                raise ValueError(
                    f"Pack '{self.name}' declares assembly recipe for '{spec.pack}'"
                )
        for spec in self._governance_specs:
            if spec.pack != self.name:
                raise ValueError(
                    f"Pack '{self.name}' declares governance spec for '{spec.pack}'"
                )
        for spec in self._extraction_profiles:
            if getattr(spec, "pack", self.name) != self.name:
                raise ValueError(
                    f"Pack '{self.name}' declares extraction profile for '{getattr(spec, 'pack', None)}'"
                )
        for spec in self._operation_profiles:
            if getattr(spec, "pack", self.name) != self.name:
                raise ValueError(
                    f"Pack '{self.name}' declares operation profile for '{getattr(spec, 'pack', None)}'"
                )
        for spec in self._wiki_views:
            if getattr(spec, "pack", self.name) != self.name:
                raise ValueError(
                    f"Pack '{self.name}' declares wiki view for '{getattr(spec, 'pack', None)}'"
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

    def artifact_specs(self) -> list[ArtifactSpec]:
        return list(self._artifact_specs)

    def artifact_spec(self, name: str) -> ArtifactSpec:
        for spec in self._artifact_specs:
            if spec.name == name:
                return spec
        raise ValueError(f"Unknown artifact spec '{name}' for pack '{self.name}'")

    def assembly_recipes(self) -> list[AssemblyRecipeSpec]:
        return list(self._assembly_recipes)

    def assembly_recipe(self, name: str) -> AssemblyRecipeSpec:
        for spec in self._assembly_recipes:
            if spec.name == name:
                return spec
        raise ValueError(f"Unknown assembly recipe '{name}' for pack '{self.name}'")

    def governance_specs(self) -> list[GovernanceSpec]:
        return list(self._governance_specs)

    def governance_spec(self, name: str) -> GovernanceSpec:
        for spec in self._governance_specs:
            if spec.name == name:
                return spec
        raise ValueError(f"Unknown governance spec '{name}' for pack '{self.name}'")
