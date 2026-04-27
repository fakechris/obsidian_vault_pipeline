"""Phase 34 — workspace zone enforcement and draft → accepted promotion.

Two responsibilities:

1. ``enforce_zone_write`` — gate writes against ``WorkspaceZonesSpec``. Called
   at every accepted-zone write site (promote_candidates, auto_moc_updater,
   concept_registry mutations, cleanup/breakdown/refine, lint stub creation).
   ``mode='promotion'`` is the only mode that bypasses the gate; everything
   else raises :class:`ZoneViolation`.
2. ``promote(draft, target, *, ...)`` — copies an agent-owned draft into an
   accepted-state path under ``mode='promotion'``, writing provenance
   frontmatter and emitting a single audit event so the lint mtime check stays
   silent.

The zone glob match is fnmatch-style relative to the vault root. Append-only
files (e.g. ``00-Polaris/Writing-Prompts.md``) match the ``append_only`` glob
and bypass the gate when the writer asks for ``mode='append'``; overwrites
still raise.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable

from .packs.base import BaseDomainPack
from .packs.loader import DEFAULT_WORKFLOW_PACK_NAME, load_pack
from .state_lifecycle import State, write_state


WRITE_MODE_NORMAL = "write"
WRITE_MODE_APPEND = "append"
WRITE_MODE_PROMOTION = "promotion"

_VALID_MODES = frozenset({WRITE_MODE_NORMAL, WRITE_MODE_APPEND, WRITE_MODE_PROMOTION})


class ZoneViolation(RuntimeError):
    """Raised when an agent-owned writer touches an accepted-state path
    without the ``promotion`` mode."""


@dataclass(frozen=True)
class PromotionRecord:
    draft: Path
    target: Path
    approver: str
    pack: str
    bytes_written: int


def _relative_to_vault(path: Path, vault_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(vault_dir.resolve()))
    except (ValueError, OSError):
        return str(path)


def _matches_any(rel_path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch(rel_path, pattern) for pattern in patterns)


def _resolve_pack(pack: BaseDomainPack | str | None) -> BaseDomainPack:
    if isinstance(pack, BaseDomainPack):
        return pack
    return load_pack(pack or DEFAULT_WORKFLOW_PACK_NAME)


def is_accepted_zone(target_path: Path, *, pack: BaseDomainPack, vault_dir: Path) -> bool:
    rel = _relative_to_vault(target_path, vault_dir)
    return _matches_any(rel, pack.workspace_zones().accepted)


def is_append_only(target_path: Path, *, pack: BaseDomainPack, vault_dir: Path) -> bool:
    """Phase 36 helper — query feedback uses this to allow appending to
    ``00-Polaris/Writing-Prompts.md`` without tripping the zone gate."""
    rel = _relative_to_vault(target_path, vault_dir)
    return _matches_any(rel, pack.workspace_zones().append_only)


def enforce_zone_write(
    target_path: Path,
    *,
    pack: BaseDomainPack | str | None = None,
    vault_dir: Path,
    mode: str = WRITE_MODE_NORMAL,
) -> None:
    """Raise :class:`ZoneViolation` if ``target_path`` is in an accepted zone
    and ``mode`` is not ``'promotion'`` (or ``'append'`` for append-only paths).

    Permissive packs (``WorkspaceZonesSpec.accepted == ()``) always pass.
    """
    if mode not in _VALID_MODES:
        raise ValueError(f"Unknown write mode '{mode}'")

    resolved_pack = _resolve_pack(pack)
    zones = resolved_pack.workspace_zones()
    if not zones.accepted:
        return  # permissive pack — every path is agent-owned

    rel = _relative_to_vault(Path(target_path), Path(vault_dir))
    in_accepted = _matches_any(rel, zones.accepted)
    if not in_accepted:
        return

    if mode == WRITE_MODE_PROMOTION:
        return
    if mode == WRITE_MODE_APPEND and _matches_any(rel, zones.append_only):
        return

    raise ZoneViolation(
        f"Refusing {mode} on accepted-zone path '{rel}' for pack '{resolved_pack.name}'. "
        "Use mode='promotion' (with audit emission) or route through ovp-promote workspace."
    )


def promote(
    draft_path: Path,
    target_path: Path,
    *,
    approver: str,
    pack: BaseDomainPack | str | None = None,
    vault_dir: Path,
    dry_run: bool = False,
) -> PromotionRecord:
    """Copy ``draft_path`` to ``target_path`` under ``mode='promotion'``.

    Caller is responsible for emitting the matching audit event (typically via
    :func:`ovp_pipeline.promotion_audit.record_promotion`) so the Phase 34
    lint mtime check stays silent.
    """
    resolved_pack = _resolve_pack(pack)
    enforce_zone_write(
        target_path,
        pack=resolved_pack,
        vault_dir=vault_dir,
        mode=WRITE_MODE_PROMOTION,
    )

    if not draft_path.exists():
        raise FileNotFoundError(f"Draft not found: {draft_path}")

    body = draft_path.read_bytes()
    bytes_written = len(body)
    if not dry_run:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(body)
        write_state(
            target_path,
            State.ACCEPTED,
            generated_by="ovp-promote workspace",
            sources=[_relative_to_vault(draft_path, vault_dir)],
            promotion_target=_relative_to_vault(target_path, vault_dir),
        )
        bytes_written = target_path.stat().st_size

    return PromotionRecord(
        draft=draft_path,
        target=target_path,
        approver=approver,
        pack=resolved_pack.name,
        bytes_written=bytes_written,
    )
