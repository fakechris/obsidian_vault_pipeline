"""Digest configuration loader (M23 / BL-094).

Mirrors :mod:`ovp_pipeline.llm_profiles` at the structural level: read
``<vault>/.ovp/digest.yaml`` if present, fall back to bundled defaults
from ``src/ovp_pipeline/data/digest_template.yaml`` otherwise.  Loader
returns an immutable :class:`DigestConfig` so downstream callers can
treat it as a value object.

What this module owns
---------------------

* **Schema** — :class:`DigestConfig` (frozen dataclass) covering tz,
  cluster threshold, intake allowlist, mid-day regenerate button toggle,
  idempotency-gate toggle.
* **Timezone resolution** — explicit IANA name from config wins; empty
  string resolves via :func:`tzlocal.get_localzone` (soft import); final
  fallback is UTC with a one-time warning log.

What this module does NOT own
-----------------------------

* Window computation — :mod:`digest_inputs` builds
  ``[window_start, window_end]`` from the resolved timezone + audit
  history.  Config only supplies the *timezone*, not the window.
* Layer-specific collectors — each layer is its own function in
  :mod:`digest_inputs`.
* Action mutations — Stage 4 / Stage 5 of the M23 plan add their own
  audit-event emission helpers; this module is read-only.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import tzinfo
from pathlib import Path
from typing import Any, Final

import yaml

logger = logging.getLogger(__name__)


# Vault-relative path the loader reads.  Mirrors the llm_profiles
# convention so a single ``.ovp/`` directory holds every per-feature
# override.
_CONFIG_REL: Final[str] = ".ovp/digest.yaml"

# Bundled template — used when the operator hasn't created an override.
# Resolves relative to this file via ``importlib.resources`` so it
# survives ``pip install`` package layouts.
_TEMPLATE_REL: Final[str] = "data/digest_template.yaml"

# Conservative per-vault default for the synthesis-ready threshold.
# Re-evaluated once a vault accumulates enough graph_clusters rows
# to run a histogram.
_DEFAULT_CLUSTER_THRESHOLD: Final[int] = 5

# Default audit-event allowlist for Layer 0.
#
# M24.0 stop-gap (2026-05-14): pull from the single canonical
# ``event_evidence_registry`` so the M23 digest, ``/ops/today``, and
# the ``/digests`` calendar all classify the same way.  Before this,
# three independently-curated lists drifted and the same day showed
# 27 / 7 / many different intake counts.
#
# The registry lists only event types real producers emit (verified
# against the operator vault on 2026-05-14).  Operator overrides in
# ``<vault>/.ovp/digest.yaml`` still win — the registry only
# provides the **default**.
from .event_evidence_registry import event_types_for_category as _evt_for

_DEFAULT_INTAKE_EVENT_TYPES: Final[tuple[str, ...]] = _evt_for("intake")


@dataclass(frozen=True)
class DigestConfig:
    """Immutable per-vault digest configuration.

    Fields match the YAML template 1:1 so operators can read either
    artifact and understand the other.  ``intake_event_types`` is
    wrapped in :class:`types.MappingProxyType` semantics by storing it
    as a tuple — mutation is impossible at the language level.
    """

    tz: str = ""
    cluster_threshold: int = _DEFAULT_CLUSTER_THRESHOLD
    intake_event_types: tuple[str, ...] = _DEFAULT_INTAKE_EVENT_TYPES
    mid_day_regenerate_button: bool = True
    skip_unchanged: bool = True


def load_digest_config(vault_dir: Path | str | None) -> DigestConfig:
    """Return the digest config for ``vault_dir``.

    Resolution order:

    1. ``<vault>/.ovp/digest.yaml`` if it exists and parses cleanly.
    2. Bundled :data:`_TEMPLATE_REL` as the source of defaults.
    3. Hard-coded :class:`DigestConfig` defaults if neither file is
       readable (defensive — should never trigger in practice).

    Missing keys in the override fall through to template / default;
    each layer is merged independently so an operator who pins only
    ``tz`` keeps every other default.
    """
    template_data = _read_template_defaults()
    override_data = _read_override(vault_dir)
    merged: dict[str, Any] = {**template_data, **override_data}
    return _coerce_config(merged)


def resolve_timezone(cfg: DigestConfig) -> tzinfo:
    """Return a ``datetime.tzinfo`` for the configured timezone.

    Resolution order:

    1. Explicit ``cfg.tz`` IANA name → :class:`zoneinfo.ZoneInfo`.
    2. Empty ``cfg.tz`` → :func:`tzlocal.get_localzone` (soft import
       — :mod:`tzlocal` is recommended but not required).
    3. Soft-import fallback: ``datetime.now().astimezone().tzinfo``
       (system-local but anonymous).
    4. Last resort: :data:`datetime.timezone.utc` with a one-time
       warning so operators notice their tz isn't being honored.

    Returns a ``tzinfo`` — never raises.
    """
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if cfg.tz:
        try:
            return ZoneInfo(cfg.tz)
        except ZoneInfoNotFoundError:
            logger.warning(
                "digest_config: tz %r not found in IANA database; "
                "falling back to system locale",
                cfg.tz,
            )

    try:
        import tzlocal  # type: ignore[import-not-found]

        return tzlocal.get_localzone()
    except ImportError:
        # tzlocal not installed — try the stdlib path.
        local = datetime.now().astimezone().tzinfo
        if local is not None:
            return local
        logger.warning(
            "digest_config: no tzlocal and no system tzinfo; falling back to UTC"
        )
        return timezone.utc
    except Exception as exc:  # noqa: BLE001 — tzlocal can raise misc.
        logger.warning(
            "digest_config: tzlocal failed (%s); falling back to UTC", exc
        )
        return timezone.utc


# ---------------------------------------------------------------
# Internals
# ---------------------------------------------------------------


def _read_template_defaults() -> dict[str, Any]:
    """Load the bundled template under ``data/``.  Empty dict on any
    I/O failure — defensive, never raises."""
    try:
        from importlib.resources import files

        text = files("ovp_pipeline").joinpath(_TEMPLATE_REL).read_text(encoding="utf-8")
        parsed = yaml.safe_load(text) or {}
        if isinstance(parsed, dict):
            return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning("digest_config: template read failed: %s", exc)
    return {}


def _read_override(vault_dir: Path | str | None) -> dict[str, Any]:
    """Load ``<vault>/.ovp/digest.yaml`` if present.  Empty dict
    when missing / unreadable / not a mapping."""
    if vault_dir is None:
        return {}
    path = Path(vault_dir) / _CONFIG_REL
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(text) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning(
            "digest_config: override %s unreadable: %s; using defaults", path, exc
        )
        return {}
    if not isinstance(parsed, dict):
        logger.warning(
            "digest_config: override %s root is not a mapping; using defaults", path
        )
        return {}
    return parsed


def _coerce_config(data: Mapping[str, Any]) -> DigestConfig:
    """Build a :class:`DigestConfig` from a YAML-loaded mapping.

    Type-tolerant: a scalar where a list is expected falls back to the
    default; a non-bool where a bool is expected → coerced via truthiness
    (matches YAML's permissive semantics).  Every coercion logs a
    warning so operator typos surface.
    """
    tz = data.get("tz")
    tz_str = str(tz) if isinstance(tz, str) else ""

    cluster_threshold = _coerce_positive_int(
        data.get("cluster_threshold"),
        default=_DEFAULT_CLUSTER_THRESHOLD,
        field_name="cluster_threshold",
    )

    intake_raw = data.get("intake_event_types")
    if isinstance(intake_raw, (list, tuple)):
        intake_clean = tuple(
            str(item).strip() for item in intake_raw if isinstance(item, str) and item.strip()
        )
        if not intake_clean:
            logger.warning(
                "digest_config: intake_event_types resolved to empty list; using defaults"
            )
            intake_clean = _DEFAULT_INTAKE_EVENT_TYPES
    else:
        if intake_raw is not None:
            logger.warning(
                "digest_config: intake_event_types must be a list; using defaults"
            )
        intake_clean = _DEFAULT_INTAKE_EVENT_TYPES

    mid_day_button = bool(data.get("mid_day_regenerate_button", True))
    skip_unchanged = bool(data.get("skip_unchanged", True))

    return DigestConfig(
        tz=tz_str,
        cluster_threshold=cluster_threshold,
        intake_event_types=intake_clean,
        mid_day_regenerate_button=mid_day_button,
        skip_unchanged=skip_unchanged,
    )


def _coerce_positive_int(value: Any, *, default: int, field_name: str) -> int:
    """Return a positive int or fall back to ``default`` with a warning."""
    if isinstance(value, bool):
        # bool is a subclass of int — reject explicitly so True doesn't
        # become 1 silently for a numeric field.
        logger.warning(
            "digest_config: %s expected int, got bool %r; using default",
            field_name,
            value,
        )
        return default
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    if value is not None:
        logger.warning(
            "digest_config: %s expected positive int, got %r; using default",
            field_name,
            value,
        )
    return default


__all__ = ["DigestConfig", "load_digest_config", "resolve_timezone"]
