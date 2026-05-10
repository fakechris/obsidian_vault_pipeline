"""BL-062: Absorb Pass 1 router.

The current ``auto_evergreen_extractor`` issues one big LLM call per
source that emits CandidateUnits without knowing what's already in
the vault.  This module is the foundation of the routing pass that
fixes that:

1. ``build_evergreen_index`` — read the canonical store and produce
   a compact ``[{slug, title, summary, key_claims, entity_type}, ...]``
   list.  This is what the router prompt sees alongside the source.
2. ``RouterDecision`` — the structured output the LLM router emits.
   Says "this source UPDATES existing evergreens X, Y / CREATES new
   evergreens A, B" with per-entry evidence segments so Pass 2
   knows where to read.
3. ``parse_router_response`` — strict JSON parser for the
   ``v2_router`` prompt's output shape.

This PR (BL-062 PR#1) lands the data + parser only.  ``route_source``
that actually issues the LLM call lives in PR#2.  Wiring into
``auto_evergreen_extractor.extract_concepts`` lands in PR#3 behind a
feature flag, then the default flips.

Why a Pass 1 router at all
--------------------------

* **Write-time dedup over after-the-fact dedup.**  Today
  ``concept_dedup`` runs after extraction, picks one canonical, and
  archives near-dups.  That necessarily loses information (different
  source_anchors, different specifics, different angles get
  collapsed).  Routing decides up front which existing slug a source
  should accumulate into, so source_anchors stack on the same
  evergreen instead of being merged away.
* **Cross-source consistency.**  Five articles on the same concept
  today produce five candidates that may or may not collapse
  depending on dedup thresholds.  Routing makes them all update the
  same slug, deterministically.
* **Wikilink resolution.**  Pass 2 receives the index entries for
  every routed target so it can write ``[[real-existing-slug]]``
  instead of fabricating new slugs that don't exist.

See ``BACKLOG.md`` BL-062 for the full motivation, and
``docs/canonical-write-ownership.md`` for how this fits the M18
trust-aware-compiler hardening.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .runtime import VaultLayout, resolve_vault_dir

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Compact evergreen index
# ---------------------------------------------------------------------------

# Defaults tuned for typical context windows: 200-char summaries +
# 3 short claims per object keep a 1000-evergreen vault around 100 KB
# of context, well under the 200K-token budget Claude allows.
DEFAULT_MAX_SUMMARY_CHARS = 200
DEFAULT_MAX_CLAIMS_PER_OBJECT = 3
DEFAULT_MAX_CLAIM_CHARS = 160


@dataclass(frozen=True)
class IndexEntry:
    """One row of the compact index the router prompt consumes.

    Each entry is the smallest projection the router needs to decide
    "is this source updating this evergreen?":

    * ``slug`` / ``title`` — the canonical handle the router emits
    * ``entity_type`` — so the router can match unit_type vocabulary
    * ``summary`` — 1-line gloss; from ``compiled_summaries`` when
      one exists, falls back to title-only
    * ``key_claims`` — top N claim texts (truncated) to give the
      router enough signal to distinguish near-dup concepts
    """

    slug: str
    title: str
    entity_type: str
    summary: str
    key_claims: tuple[str, ...] = ()


def _truncate(text: str, max_chars: int) -> str:
    """Cut to ``max_chars`` keeping word boundaries when easy.  Adds
    ``…`` when truncated.  Whitespace-collapses first."""
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    # Cut to max_chars - 1 then trim back to the last word boundary
    # if there's a space within the last 20% of the cap.  Avoids
    # awkward mid-word cuts.
    cut = cleaned[: max_chars - 1]
    boundary_window = int(max_chars * 0.2)
    space = cut.rfind(" ", max_chars - 1 - boundary_window)
    if space > 0:
        cut = cut[:space]
    return cut + "…"


def build_evergreen_index(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    max_summary_chars: int = DEFAULT_MAX_SUMMARY_CHARS,
    max_claims_per_object: int = DEFAULT_MAX_CLAIMS_PER_OBJECT,
    max_claim_chars: int = DEFAULT_MAX_CLAIM_CHARS,
) -> list[IndexEntry]:
    """Return the compact index the Pass 1 router consumes.

    Reads ``objects`` (slug + title + entity_type) joined with
    ``compiled_summaries`` (1-line gloss when present) and the
    top N claims per object from ``claims`` (lexicographic sort
    on ``claim_id`` for determinism — an upgrade to scoring by
    confidence is a follow-up).

    Returns a list ordered by slug for deterministic prompt
    rendering.  When ``pack_name`` is given, only that pack's
    objects appear.  When ``None``, all packs in the truth store
    are surfaced (matches today's `auto_evergreen_extractor`
    cross-pack search).

    Best-effort empty list when the DB is missing — callers
    (router, debug CLIs) should treat an empty index as
    "everything is a create."
    """
    resolved = resolve_vault_dir(vault_dir)
    layout = VaultLayout.from_vault(resolved)
    if not layout.knowledge_db.exists():
        return []

    # SQL: fetch objects + their summary + top N claim texts in one
    # round-trip per pack.  Keep the projection narrow — extra
    # columns just bloat the router prompt.
    with sqlite3.connect(layout.knowledge_db) as conn:
        # ``object_kind`` is the canonical type column; ``entity_type``
        # frontmatter convention maps onto it.
        if pack_name:
            object_rows = conn.execute(
                """
                SELECT pack, object_id, object_kind, title
                FROM objects
                WHERE pack = ?
                ORDER BY object_id
                """,
                (pack_name,),
            ).fetchall()
        else:
            object_rows = conn.execute(
                """
                SELECT pack, object_id, object_kind, title
                FROM objects
                ORDER BY pack, object_id
                """
            ).fetchall()

        # Pre-load every (pack, object_id) → summary in one shot.  N
        # SELECTs vs one IN-clause: the index is small (<10K objects
        # typical), one fetchall is cheaper than per-row queries.
        summary_rows = conn.execute(
            "SELECT pack, object_id, summary_text FROM compiled_summaries"
        ).fetchall()
        summaries: dict[tuple[str, str], str] = {
            (pack, oid): summary_text or ""
            for (pack, oid, summary_text) in summary_rows
        }

        # Top N claims per object, ordered by claim_id for
        # determinism.  ``ROW_NUMBER() OVER PARTITION`` is the natural
        # SQLite window-function pattern — the per-object truncation
        # happens in SQL, not Python, so we don't pull every claim.
        claims_rows = conn.execute(
            """
            SELECT pack, object_id, claim_text
            FROM (
              SELECT pack, object_id, claim_text,
                ROW_NUMBER() OVER (
                  PARTITION BY pack, object_id
                  ORDER BY claim_id
                ) AS rn
              FROM claims
            )
            WHERE rn <= ?
            ORDER BY pack, object_id, rn
            """,
            (max_claims_per_object,),
        ).fetchall()

    claims_by_object: dict[tuple[str, str], list[str]] = {}
    for (pack, oid, claim_text) in claims_rows:
        if not claim_text:
            continue
        claims_by_object.setdefault((pack, oid), []).append(
            _truncate(str(claim_text), max_claim_chars)
        )

    entries: list[IndexEntry] = []
    for (pack, object_id, object_kind, title) in object_rows:
        summary = _truncate(summaries.get((pack, object_id), ""), max_summary_chars)
        # Fall back to title when no compiled summary exists yet.
        if not summary:
            summary = _truncate(str(title or object_id), max_summary_chars)
        key_claims = tuple(claims_by_object.get((pack, object_id), ()))
        entries.append(IndexEntry(
            slug=str(object_id),
            title=str(title or object_id),
            entity_type=str(object_kind or "concept"),
            summary=summary,
            key_claims=key_claims,
        ))
    return entries


def render_index_for_prompt(entries: Iterable[IndexEntry]) -> str:
    """Render the index entries as a compact markdown block for the
    router prompt.  Format mirrors the existing
    ``related_block`` rendering in
    ``auto_evergreen_extractor`` so the LLM sees a familiar shape.

    One entry per line; key_claims rendered as a sub-bullet list
    indented by 2 spaces.  Empty index → empty string.
    """
    lines: list[str] = []
    for entry in entries:
        head = (
            f"- `{entry.slug}` ({entry.entity_type}) — "
            f"{entry.title}"
        )
        if entry.summary and entry.summary != entry.title:
            head += f" — {entry.summary}"
        lines.append(head)
        for claim in entry.key_claims:
            lines.append(f"  - {claim}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# RouterDecision + response parser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UpdateTarget:
    """A routing decision saying the source UPDATES an existing slug."""

    slug: str
    rationale: str
    evidence_segments: tuple[str, ...] = ()


@dataclass(frozen=True)
class CreateTarget:
    """A routing decision saying the source CREATES a new evergreen.

    ``title`` is human-readable; the slug is derived downstream by
    the same identity rules ``auto_evergreen_extractor`` already
    applies (``canonicalize_note_id``).
    """

    title: str
    kind: str
    rationale: str
    evidence_segments: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouterDecision:
    """Pass 1 router output for one source.

    A clean decision has at least one ``update`` or ``create`` (or a
    ``skip_reason`` if the source carries nothing for the vault).
    The dataclass is frozen so downstream callers can compare
    decisions across re-runs deterministically.
    """

    source_value_summary: str
    updates: tuple[UpdateTarget, ...] = ()
    creates: tuple[CreateTarget, ...] = ()
    skip_reason: str = ""

    @property
    def is_skip(self) -> bool:
        return bool(self.skip_reason) and not self.updates and not self.creates


class RouterResponseError(ValueError):
    """Raised when the LLM router returns a malformed payload.  The
    caller (``route_source`` in PR#2) catches this, emits an
    ``absorb_route_decision`` audit row with ``status='parse_error'``,
    and falls back to the legacy v2 monolithic extractor."""


def _coerce_string_list(value: Any) -> tuple[str, ...]:
    """Tolerant list-of-strings coercion for ``evidence_segments``.

    The router prompt asks for a list of strings.  Be liberal in
    what we accept (LLMs occasionally emit a single string instead
    of a list) so a perfectly-good decision doesn't get rejected
    over a wrapping issue.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        # Single-string convenience.
        stripped = value.strip()
        return (stripped,) if stripped else ()
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            stripped = item.strip()
            if stripped:
                out.append(stripped)
        return tuple(out)
    raise RouterResponseError(
        f"evidence_segments must be a list of strings, got {type(value).__name__}"
    )


def parse_router_response(response_text: str) -> RouterDecision:
    """Parse the v2_router prompt's output into a ``RouterDecision``.

    Strict on shape — wrapper keys must be present, ``slug`` /
    ``title`` / ``rationale`` must be non-empty strings.  Tolerant on
    quirks an LLM occasionally introduces (extra whitespace, a
    single-string instead of a list for ``evidence_segments``,
    missing ``skip_reason`` field).

    Raises ``RouterResponseError`` for shapes the parser cannot
    recover.  Caller is expected to catch + audit.
    """
    text = (response_text or "").strip()
    if not text:
        raise RouterResponseError("empty router response")

    # The prompt explicitly forbids markdown wrappers, but LLMs
    # sometimes emit ```json ... ``` anyway.  Strip the fence if
    # present; reject anything else that's clearly not a JSON object.
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl == -1:
            raise RouterResponseError("router response is markdown-wrapped but has no body")
        text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[: -len("```")].rstrip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RouterResponseError(f"router response is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise RouterResponseError(
            f"router response must be a JSON object at the top level, got {type(payload).__name__}"
        )

    summary = str(payload.get("source_value_summary") or "").strip()
    skip_reason = str(payload.get("skip_reason") or "").strip()

    raw_updates = payload.get("updates") or []
    if not isinstance(raw_updates, list):
        raise RouterResponseError("'updates' must be a list")
    updates: list[UpdateTarget] = []
    for idx, entry in enumerate(raw_updates):
        if not isinstance(entry, dict):
            raise RouterResponseError(f"updates[{idx}] must be an object")
        slug = str(entry.get("slug") or "").strip()
        rationale = str(entry.get("rationale") or "").strip()
        if not slug:
            raise RouterResponseError(f"updates[{idx}].slug is empty")
        if not rationale:
            # Soften: use a placeholder rather than reject — the
            # rationale is for the human reviewer, not for routing
            # correctness.  Logged so prompt drift is visible.
            logger.warning(
                "router emitted updates[%d] for slug=%s without rationale",
                idx, slug,
            )
            rationale = "(no rationale provided by router)"
        updates.append(UpdateTarget(
            slug=slug,
            rationale=rationale,
            evidence_segments=_coerce_string_list(entry.get("evidence_segments")),
        ))

    raw_creates = payload.get("creates") or []
    if not isinstance(raw_creates, list):
        raise RouterResponseError("'creates' must be a list")
    creates: list[CreateTarget] = []
    for idx, entry in enumerate(raw_creates):
        if not isinstance(entry, dict):
            raise RouterResponseError(f"creates[{idx}] must be an object")
        title = str(entry.get("title") or "").strip()
        kind = str(entry.get("kind") or "concept").strip().lower()
        rationale = str(entry.get("rationale") or "").strip()
        if not title:
            raise RouterResponseError(f"creates[{idx}].title is empty")
        if not rationale:
            logger.warning(
                "router emitted creates[%d] for title=%s without rationale",
                idx, title,
            )
            rationale = "(no rationale provided by router)"
        creates.append(CreateTarget(
            title=title,
            kind=kind,
            rationale=rationale,
            evidence_segments=_coerce_string_list(entry.get("evidence_segments")),
        ))

    decision = RouterDecision(
        source_value_summary=summary,
        updates=tuple(updates),
        creates=tuple(creates),
        skip_reason=skip_reason,
    )

    # Sanity: a well-formed response either skips or routes.  All-empty
    # is a router malfunction worth surfacing.
    if not decision.updates and not decision.creates and not decision.skip_reason:
        raise RouterResponseError(
            "router decision is empty (no updates, no creates, no skip_reason)"
        )

    return decision


# ---------------------------------------------------------------------------
# Audit event constants (consumed by route_source in PR#2)
# ---------------------------------------------------------------------------

# Event type written to ``audit_events`` whenever the router runs
# (success, skip, or parse error).  Pass 2 reads recent rows of this
# type to know what to extract; downstream surfaces (``/ops/today``,
# audit log) already render audit_events generically.
ABSORB_ROUTE_DECISION_EVENT = "absorb_route_decision"

# Status values stored in the audit row's payload.  Free-form for
# forward-compat but please add new values here when introducing them.
ROUTE_STATUS_OK = "ok"
ROUTE_STATUS_SKIP = "skip"
ROUTE_STATUS_PARSE_ERROR = "parse_error"
