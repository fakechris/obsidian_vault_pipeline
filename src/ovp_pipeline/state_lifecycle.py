"""
Single source of truth for the ``state:`` frontmatter field (Phase 32 prelude).

Phase 32 only *reads* state. Phase 34 starts *writing* it. The file lives here
so the closed vocabulary, the read/write helpers, and the legacy-path inference
rule are defined exactly once and the field never has to be retrofitted.

Closed vocabulary
-----------------

* ``candidate``  Pre-canonical, awaiting review (concept candidates, candidate
                  evergreen pages, suggested relations).
* ``draft``      Agent-owned working copy in a workspace zone (e.g. a project's
                  ``Drafts/`` subfolder). Free to mutate without review.
* ``suggested``  Agent-produced output stationed in an inbox or suggestion
                  queue, awaiting human approval before promotion.
* ``derived``    Materialized from canonical truth (deep dives, briefings,
                  compiled views). Re-buildable; mutating manually is allowed
                  but flagged as a smell.
* ``accepted``   Project / area artifact a human has signed off on
                  (Plan.md, Roadmap.md). Only mutated through promotion.
* ``canonical``  Knowledge-base truth. Single owner per object. Mutated only
                  via the promotion pipeline.

Module shape mirrors the Phase 37 forward-compat hazard list: ``State`` is a
closed string enum so MCP tools / Workbench Pulse can use the string values
directly without a lookup table.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping

import yaml


class State(str, Enum):
    """Closed vocabulary for the ``state:`` frontmatter field."""

    CANDIDATE = "candidate"
    DRAFT = "draft"
    SUGGESTED = "suggested"
    DERIVED = "derived"
    ACCEPTED = "accepted"
    CANONICAL = "canonical"


_VALID_VALUES = {item.value for item in State}


def read_state(meta: Mapping[str, object] | None) -> State | None:
    """Return the parsed ``state:`` value from a frontmatter mapping, if valid.

    Tolerates absence (returns ``None``) so legacy notes without ``state:``
    don't break callers; use :func:`infer_state_from_path` to backfill.
    Unknown values also return ``None`` rather than raising — the lint layer
    is the right place to flag malformed values, not this hot-path read.
    """
    if not meta:
        return None
    raw = meta.get("state")
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in _VALID_VALUES:
        return State(text)
    return None


@dataclass(frozen=True)
class _ProvenanceBlock:
    state: State
    generated_by: str
    sources: tuple[str, ...]
    reuse_event_ids: tuple[str, ...]
    promotion_target: str

    def to_frontmatter(self) -> dict[str, object]:
        block: dict[str, object] = {"state": self.state.value}
        if self.generated_by:
            block["generated_by"] = self.generated_by
        if self.sources:
            block["sources"] = list(self.sources)
        if self.reuse_event_ids:
            block["reuse_event_ids"] = list(self.reuse_event_ids)
        if self.promotion_target:
            block["promotion_target"] = self.promotion_target
        return block


def write_state(
    path: Path,
    state: State,
    *,
    generated_by: str,
    sources: Iterable[str],
    reuse_event_ids: Iterable[str] | None = None,
    promotion_target: str | None = None,
) -> None:
    """Update or insert the ``state:`` provenance block in ``path``.

    Preserves existing frontmatter keys; only the §7a provenance fields
    (``state``, ``generated_by``, ``sources``, ``reuse_event_ids``,
    ``promotion_target``) are rewritten. If ``path`` has no frontmatter a
    fresh fenced block is prepended.
    """
    block = _ProvenanceBlock(
        state=state,
        generated_by=generated_by,
        sources=tuple(str(item) for item in sources),
        reuse_event_ids=tuple(str(item) for item in (reuse_event_ids or [])),
        promotion_target=str(promotion_target or ""),
    )

    text = path.read_text(encoding="utf-8") if path.exists() else ""
    meta, body = _split_frontmatter(text)
    meta.update(block.to_frontmatter())
    serialized = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    rewritten = f"---\n{serialized}\n---\n{body}" if body else f"---\n{serialized}\n---\n"
    path.write_text(rewritten, encoding="utf-8")


def _split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[end + 5 :]
    try:
        meta = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, body


def infer_state_from_path(vault_dir: Path, path: Path) -> State:
    """Backfill rule for legacy notes that have no ``state:`` field.

    Mapping is intentionally coarse — the goal is to give the lint layer a
    safe default, not to replace explicit ``state:`` declarations. Callers
    that need richer behavior should invoke :func:`read_state` first and
    fall back to this only on ``None``.
    """
    try:
        rel = path.resolve().relative_to(Path(vault_dir).resolve())
    except ValueError:
        return State.DRAFT
    parts = rel.parts
    if not parts:
        return State.DRAFT
    head = parts[0]
    if head == "10-Knowledge":
        return State.CANONICAL
    if head == "20-Areas":
        return State.DERIVED
    if head == "30-Projects":
        return State.ACCEPTED
    if head == "50-Inbox":
        return State.SUGGESTED
    if head == "70-Archive":
        return State.ACCEPTED
    return State.DRAFT
