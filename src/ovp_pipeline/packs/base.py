from __future__ import annotations

from dataclasses import dataclass, field


ALLOWED_PACK_ROLES = {"domain", "primary", "compatibility"}


@dataclass(frozen=True)
class ObjectKindPropertySpec:
    """A property that objects of this kind may carry in frontmatter or truth_store."""

    name: str
    field_type: str = "text"
    description: str = ""
    required: bool = False


@dataclass(frozen=True)
class ObjectKindSpec:
    kind: str
    display_name: str
    description: str
    canonical: bool = True
    schema_ref: str | None = None
    properties: tuple[ObjectKindPropertySpec, ...] = ()
    reader_layout: str | None = None
    extraction_hint: str | None = None


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


@dataclass(frozen=True)
class SemanticRelationTypeSpec:
    name: str
    description: str
    source_object_kinds: tuple[str, ...] = ()
    target_object_kinds: tuple[str, ...] = ()
    directionality: str = "directed"
    evidence_required: bool = True
    review_required: bool = True

    def accepts_source_kind(self, kind: str) -> bool:
        """Check whether *kind* is a valid source for this relation type."""
        from ..object_kinds import normalize_kind

        if not self.source_object_kinds:
            return True
        return normalize_kind(kind) in self.source_object_kinds

    def accepts_target_kind(self, kind: str) -> bool:
        """Check whether *kind* is a valid target for this relation type."""
        from ..object_kinds import normalize_kind

        if not self.target_object_kinds:
            return True
        return normalize_kind(kind) in self.target_object_kinds

    def validate_pair(self, source_kind: str, target_kind: str) -> bool:
        """Return True if both source and target kinds are accepted."""
        return self.accepts_source_kind(source_kind) and self.accepts_target_kind(target_kind)


@dataclass(frozen=True)
class SemanticRelationContractSpec:
    name: str
    pack: str
    description: str = ""
    relation_types: list[SemanticRelationTypeSpec] = field(default_factory=list)
    source_contract_kind: str = "artifact_spec"
    source_contract_name: str = "semantic_relation_candidate"
    review_queue_name: str = "semantic-relations"
    write_policy: str = "review_required"


# ---------------------------------------------------------------------------
# Phase 34 — Policy Promotion contract additions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AutoPromoteRule:
    """Concept-promotion auto-lane criteria.

    ``legacy_or_rule=True`` is the bit-for-bit escape hatch used by
    ``default-knowledge``: ``promotion_policy.evaluate_concept`` short-circuits
    to the historical ``source_count >= 2 or evidence_count >= 3`` rule. Strict
    packs leave it ``False`` and rely on the structured fields.
    """

    require_independent_sources: int = 1
    require_evidence_kinds: tuple[str, ...] = ()
    require_no_open_contradiction: bool = False
    legacy_or_rule: bool = False


@dataclass(frozen=True)
class EscalateRule:
    """When auto-promote fails, escalate to the human review queue if any of
    these flags fire. All-False = never escalate (auto-only or reject)."""

    on_partial_evidence: bool = False
    on_disputed: bool = False
    on_unverified_evidence: bool = False


@dataclass(frozen=True)
class RejectRule:
    """Hard floor for rejection — anything below this is rejected outright."""

    min_evidence_floor: int = 0


@dataclass(frozen=True)
class PromotionPolicySpec:
    auto_promote: AutoPromoteRule = field(default_factory=AutoPromoteRule)
    escalate_to_workbench: EscalateRule = field(default_factory=EscalateRule)
    reject: RejectRule = field(default_factory=RejectRule)


@dataclass(frozen=True)
class WorkspaceZonesSpec:
    """Glob patterns identifying agent-owned vs accepted-state zones.

    ``agent_owned``: paths agents may write freely (drafts, briefings, inbox).
    ``accepted``: paths gated by promotion (Plan.md, Roadmap.md, Decisions.md).
    ``append_only``: declared exception within ``accepted`` (e.g.
    ``00-Polaris/Writing-Prompts.md``) — appends OK, overwrites refused.
    """

    agent_owned: tuple[str, ...] = ("**",)
    accepted: tuple[str, ...] = ()
    append_only: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceRequirementsSpec:
    """Phase 34 hook for Phase 33's lint EVIDENCE_INCOMPLETE.

    ``claim_must_have``/``relation_must_have`` enumerate evidence column names
    that must be non-empty before a row counts as complete (e.g. ``locator``,
    ``content_hash``). Permissive packs leave both empty so lint stays silent.
    """

    claim_must_have: tuple[str, ...] = ()
    relation_must_have: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReuseSignalsSpec:
    """Optional pack tuning for Phase 32's ``trusted_reuse_event`` derivation.

    Packs may add surfaces beyond the closed default vocabulary
    (``query|briefing|writing_prompt|compiled_view|export|truth_api|prompt``)
    or scope provenance-clean lookback differently.
    """

    extra_surfaces: tuple[str, ...] = ()
    provenance_clean_lookback_days: int = 30


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
    _semantic_relation_contracts: list[SemanticRelationContractSpec] = field(default_factory=list)
    # Phase 34 — all optional; absence = core defaults = current behavior.
    _promotion_policy: PromotionPolicySpec | None = None
    _workspace_zones: WorkspaceZonesSpec | None = None
    _evidence_requirements: EvidenceRequirementsSpec | None = None
    _reuse_signals: ReuseSignalsSpec | None = None
    # Phase 38 — EVOLVES subtype vocabulary; None falls back to v0.6 defaults.
    _evolves_relation_types: tuple[str, ...] | None = None

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
        for spec in self._semantic_relation_contracts:
            if spec.pack != self.name:
                raise ValueError(
                    f"Pack '{self.name}' declares semantic relation contract for '{spec.pack}'"
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

    def object_kind_spec(self, kind: str) -> ObjectKindSpec:
        """Look up a single ObjectKindSpec by kind string."""
        from ..object_kinds import normalize_kind

        normalized = normalize_kind(kind)
        for spec in self._object_kinds:
            if spec.kind == normalized:
                return spec
        raise ValueError(f"Unknown object kind '{kind}' for pack '{self.name}'")

    def valid_entity_types(self) -> frozenset[str]:
        """Return the set of entity_type values this pack recognizes.

        Only canonical kinds (those that can appear in Evergreen frontmatter's
        ``entity_type`` field) are included.
        """
        return frozenset(s.kind for s in self._object_kinds if s.canonical)

    def validate_entity_type(self, entity_type: str) -> bool:
        """Check whether an entity_type string is valid for this pack."""
        from ..object_kinds import normalize_kind

        return normalize_kind(entity_type) in self.valid_entity_types()

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

    def semantic_relation_contracts(self) -> list[SemanticRelationContractSpec]:
        return list(self._semantic_relation_contracts)

    def semantic_relation_contract(self, name: str) -> SemanticRelationContractSpec:
        for spec in self._semantic_relation_contracts:
            if spec.name == name:
                return spec
        raise ValueError(
            f"Unknown semantic relation contract '{name}' for pack '{self.name}'"
        )

    # ---- Phase 34 accessors --------------------------------------------------

    def promotion_policy(self) -> PromotionPolicySpec:
        return self._promotion_policy or PromotionPolicySpec()

    def workspace_zones(self) -> WorkspaceZonesSpec:
        return self._workspace_zones or WorkspaceZonesSpec()

    def evidence_requirements(self) -> EvidenceRequirementsSpec:
        return self._evidence_requirements or EvidenceRequirementsSpec()

    def reuse_signals(self) -> ReuseSignalsSpec:
        return self._reuse_signals or ReuseSignalsSpec()

    def evolves_relation_types(self) -> tuple[str, ...]:
        """Subtype vocabulary for ``relation_type == "evolves"`` candidates.

        The default mirrors Nowledge Mem v0.6's epistemic-evolution vocabulary
        (replaces / enriches / confirms / challenges). Packs override by
        setting ``_evolves_relation_types`` to a tuple of allowed subtypes.
        """
        if self._evolves_relation_types is None:
            return ("replaces", "enriches", "confirms", "challenges")
        return self._evolves_relation_types
