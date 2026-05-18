"""Single source of truth for what each pipeline step produces.

Each step in the OVP pipeline returns a typed StepResult subclass declaring
exactly which fields it produces.  The dispatcher coerces every step's return
value through ``coerce_step_result`` at the boundary, so silent contract
violations (consumer reads ``result["x"]`` but no producer path emits it)
become loud errors instead of silent fallbacks.

Migration is staged: ``coerce_step_result`` accepts both raw ``dict`` and
typed ``StepResult`` during the transition window.  Once all step methods
return typed objects, the dispatcher will be flipped from warn-mode to
raise-mode (see PR #2 in the rollout plan).

Usage from a step method:

    from .step_contracts import EntityExtractStepResult

    def step_entity_extract(self, dry_run: bool = False) -> EntityExtractStepResult:
        ...
        return EntityExtractStepResult(
            success=True,
            produced=produced,
            total_entities=after_count,
            mentions_extracted=total_mentions,
        )

Usage from a consumer:

    absorb_result = self.step_results["absorb"]   # AbsorbStepResult
    absorb_files = absorb_result.processed_files  # typed access; KeyError if missing

Backward-compat: every StepResult also supports ``result["key"]`` and
``result.get("key", default)``, so existing dict-style consumers keep
working during migration.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field, fields, asdict, replace
from typing import Any


class StepContractError(Exception):
    """Raised when a step's return value violates its declared contract."""


class StepContractWarning(DeprecationWarning):
    """Emitted in warn-mode when a step's return value has unexpected fields.

    Subclassed from DeprecationWarning so it shows up by default in test
    output but doesn't fail in production.  PR #2 will flip these into
    StepContractError.
    """


# ---------------------------------------------------------------------------
# Base contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StepResult:
    """Common fields every step result carries.

    Per-step subclasses extend with their domain-specific fields.  Fields
    declared here are universal; do not duplicate them in subclasses.

    The dispatcher (``EnhancedPipeline.run_full_pipeline``) writes the
    ``output``, ``cache_hit``, ``stage_*`` and ``returncode`` fields after
    the step method returns; per-step contracts do not need to populate
    them.
    """

    success: bool
    skipped: bool = False
    blocked: bool = False
    reason: str | None = None
    error: str | None = None
    stdout: str = ""
    stderr: str = ""
    produced: int = 0  # primary "how many items did this step produce" count
    output: str = ""   # dispatcher-composed summary line (e.g. "Produced N items")
    returncode: int = 0
    # Detection / counting strategy — "filesystem" when _count_output_files
    # had to scan the FS to derive ``produced``, "step_emitted" when the
    # step itself returned authoritative numbers.  Set by the dispatcher.
    method: str | None = None
    # Stage-cache plumbing — set by _checkout_stage_artifact /
    # _write_stage_artifact / _count_output_files.
    cache_hit: bool | None = None
    stage_fingerprint: str | None = None
    stage_artifact: str | None = None
    input_digest: str | None = None
    algorithm_digest: str | None = None
    output_digest: str | None = None

    # ----- backward-compat dict-style access ---------------------------

    def __getitem__(self, key: str) -> Any:
        if key not in self._field_names():
            raise KeyError(key)
        return getattr(self, key)

    def __contains__(self, key: str) -> bool:
        # Match dict semantics: declared field is "in" the result regardless
        # of whether its current value is None.
        return key in self._field_names()

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._field_names():
            return getattr(self, key)
        return default

    def keys(self):
        return self._field_names()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def _field_names(cls) -> set[str]:
        return {f.name for f in fields(cls)}


# ---------------------------------------------------------------------------
# Per-step contracts (in BASE_PIPELINE_STEPS order)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PinboardStepResult(StepResult):
    new_bookmarks: int = 0
    days_processed: int = 0


@dataclass(frozen=True, slots=True)
class PinboardProcessStepResult(StepResult):
    files_processed: int = 0
    files_skipped: int = 0
    files_failed: int = 0


@dataclass(frozen=True, slots=True)
class ClippingsStepResult(StepResult):
    migrated: int = 0
    remaining: int = 0


@dataclass(frozen=True, slots=True)
class ArticlesStepResult(StepResult):
    total_interpretations: int = 0
    produced_files: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class QualityStepResult(StepResult):
    quality_checked: int = 0
    quality_qualified: int = 0
    quality_failed: int = 0
    quality_qualified_files: list[str] = field(default_factory=list)
    quality_results_json: str | None = None
    quality_score: float = 0.0
    # Stage artifact fingerprint set by _write_quality_stage_artifact.
    # Downstream stages (absorb's require_quality_artifact path) read this
    # to verify they're consuming a matching upstream artifact.
    quality_stage_fingerprint: str | None = None
    quality_stage_artifact: str | None = None


@dataclass(frozen=True, slots=True)
class FixLinksStepResult(StepResult):
    pass  # base only


@dataclass(frozen=True, slots=True)
class AbsorbStepResult(StepResult):
    processed_files: list[str] = field(default_factory=list)
    promoted_slugs: list[str] = field(default_factory=list)
    qualified_files: list[str] = field(default_factory=list)
    pending_qualified_files: list[str] = field(default_factory=list)
    item_cache_hits: int = 0
    item_cache_hit_files: list[str] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)
    results: list[dict] = field(default_factory=list)
    input_artifact: dict | None = None
    total_evergreen: int = 0  # post-run population (filled by dispatcher)
    # PR-A BL-029 quality→absorb fallback markers.  Set when the
    # pipeline quality artifact qualified 0 files (post-BL-029 the
    # quality stage only scans the removed deep-dive layer) but
    # absorb fell back to its own recent-target discovery against
    # 50-Inbox/03-Processed intake sources.  First-class fields so
    # the fallback is auditable in run reports / typed consumers,
    # not silently dropped by ``to_typed_step_result``.
    bl029_intake_fallback: bool = False
    fallback_reason: str | None = None
    fallback_intake_targets: int = 0


@dataclass(frozen=True, slots=True)
class EntityExtractStepResult(StepResult):
    total_entities: int = 0  # post-run registry size
    mentions_extracted: int = 0


@dataclass(frozen=True, slots=True)
class DedupStepResult(StepResult):
    clusters: int = 0
    archived: int = 0
    rewrites: int = 0
    proposal_id: str | None = None
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class NoteTypeNormalizeStepResult(StepResult):
    note_type_changed: int = 0
    note_type_skipped: int = 0


@dataclass(frozen=True, slots=True)
class RegistrySyncStepResult(StepResult):
    pass  # base only


@dataclass(frozen=True, slots=True)
class MocStepResult(StepResult):
    updated: bool = False
    changed_files: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RefineStepResult(StepResult):
    cleanup: dict | None = None
    breakdown: dict | None = None
    refine_log: str = ""
    updated: bool = False


@dataclass(frozen=True, slots=True)
class KnowledgeIndexStepResult(StepResult):
    db_path: str = ""
    updated: bool = False
    db_mtime: float = 0.0
    # PR4: which refresh path this step took and why.
    # refresh_mode: "audit_sync_only" | "full_rebuild" | ""
    refresh_mode: str = ""
    canonical_evidence_count: int = 0
    rebuild_watermark: str = ""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


STEP_CONTRACTS: dict[str, type[StepResult]] = {
    "pinboard": PinboardStepResult,
    "pinboard_process": PinboardProcessStepResult,
    "clippings": ClippingsStepResult,
    "articles": ArticlesStepResult,
    "quality": QualityStepResult,
    "fix_links": FixLinksStepResult,
    "absorb": AbsorbStepResult,
    "entity_extract": EntityExtractStepResult,
    "dedup": DedupStepResult,
    "note_type_normalize": NoteTypeNormalizeStepResult,
    "registry_sync": RegistrySyncStepResult,
    "moc": MocStepResult,
    "refine": RefineStepResult,
    "knowledge_index": KnowledgeIndexStepResult,
}


# ---------------------------------------------------------------------------
# Coercion at dispatcher boundary
# ---------------------------------------------------------------------------


def coerce_step_result(
    step: str,
    raw: dict[str, Any] | StepResult,
    *,
    strict: bool = False,
) -> StepResult:
    """Coerce a step's raw return value into its typed StepResult.

    Parameters
    ----------
    step : str
        Step name (must be in STEP_CONTRACTS).
    raw : dict | StepResult
        What the step method returned.  If already a StepResult of the right
        type, returned unchanged.
    strict : bool, default False
        When ``True``, extra fields raise ``StepContractError``.  When
        ``False`` (warn-mode), they emit a ``StepContractWarning`` and are
        dropped silently.  PR #2 of the contract rollout flips strict=True.

    Returns
    -------
    StepResult
        Instance of the step's declared subclass.

    Raises
    ------
    StepContractError
        If ``step`` has no registered contract, ``raw`` is the wrong type,
        ``strict=True`` and extra fields are present, or required positional
        args (notably ``success``) are missing from a dict raw.
    """
    contract_cls = STEP_CONTRACTS.get(step)
    if contract_cls is None:
        raise StepContractError(
            f"step={step!r} has no registered contract; add it to STEP_CONTRACTS"
        )

    if isinstance(raw, StepResult):
        if not isinstance(raw, contract_cls):
            raise StepContractError(
                f"step={step!r} returned {type(raw).__name__}, "
                f"expected {contract_cls.__name__}"
            )
        return raw

    if not isinstance(raw, dict):
        raise StepContractError(
            f"step={step!r} returned {type(raw).__name__}, "
            f"expected dict or {contract_cls.__name__}"
        )

    valid_fields = {f.name for f in fields(contract_cls)}
    extra = set(raw.keys()) - valid_fields
    if extra:
        msg = (
            f"step={step!r} returned extra fields not in "
            f"{contract_cls.__name__}: {sorted(extra)}"
        )
        if strict:
            raise StepContractError(msg)
        warnings.warn(msg, StepContractWarning, stacklevel=2)

    # Drop extras; let dataclass __init__ enforce required fields.
    kwargs = {k: v for k, v in raw.items() if k in valid_fields}
    if "success" not in kwargs:
        raise StepContractError(
            f"step={step!r} return missing required field 'success'"
        )
    try:
        return contract_cls(**kwargs)
    except TypeError as exc:
        raise StepContractError(
            f"step={step!r} return cannot be coerced to {contract_cls.__name__}: {exc}"
        ) from exc


def with_derived(result: StepResult, **derived: Any) -> StepResult:
    """Return a copy of ``result`` with derived fields filled in.

    Use from the dispatcher's ``_count_output_files`` path when computing
    post-run summary fields (e.g. ``total_evergreen`` for absorb) that the
    step itself can't know in advance.

    Raises
    ------
    StepContractError
        If any derived key is not declared on ``result``'s contract.
    """
    valid = {f.name for f in fields(type(result))}
    bad = set(derived.keys()) - valid
    if bad:
        raise StepContractError(
            f"with_derived: keys {sorted(bad)} not declared on "
            f"{type(result).__name__}; add them to the contract first"
        )
    return replace(result, **derived)


# ---------------------------------------------------------------------------
# Producer-side conversion helpers (used by step methods that historically
# returned free-form dicts).  Drops fields not declared on the contract;
# ensures ``success`` is set.
# ---------------------------------------------------------------------------


def to_typed_step_result(step: str, payload: dict[str, Any]) -> StepResult:
    """Convert any step's raw return dict to its typed StepResult."""
    cls = STEP_CONTRACTS[step]
    valid = {f.name for f in fields(cls)}
    kwargs = {k: v for k, v in payload.items() if k in valid}
    if "success" not in kwargs:
        kwargs["success"] = bool(payload.get("success", False))
    return cls(**kwargs)


def to_absorb_result(payload: dict[str, Any]) -> "AbsorbStepResult":
    """Convert an absorb payload dict into a typed AbsorbStepResult.

    Used by ``step_absorb`` and ``_run_absorb_workflow_direct`` so all 7
    absorb return paths produce the same typed shape.
    """
    return to_typed_step_result("absorb", payload)  # type: ignore[return-value]
