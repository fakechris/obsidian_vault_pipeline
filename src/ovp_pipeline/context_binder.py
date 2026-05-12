"""Anchored inquiry context binder (M21a / BL-083).

Two-layer binder for the M21 inquiry handler:

* **Anchor layer** — built *once* per session.  Loads the artifact
  the operator is reading (note / object / crystal / standalone),
  plus the linked evergreens / cluster neighbours that flesh it
  out.  Stable across the whole inquiry session.
* **Retrieval layer** — rebuilt *per turn* from the user's current
  question.  Wraps :func:`ovp_pipeline.discovery.discover_related`
  (the same helper that powers ``ovp-query``) plus open
  contradictions + crystal scores.  Does NOT reimplement RAG.

The binder's job is **selection + budgeting**, not retrieval.  It
picks the top-N items from each retrieval source within a
per-turn budget, drops in priority order on overflow, and records
every drop in the manifest's ``omitted_*`` fields.

Token budgeting
---------------

The handler tells the binder its profile's input cap; the binder
allocates roughly 60% to the anchor layer and 40% to retrieval,
keeping the literal anchor body inviolate even under pressure.
Token estimates are computed via a 4-chars-per-token proxy — fast
and accurate enough to pick which items to drop.  BL-084's actual
LLM call may use ``tiktoken`` for the precise count.

Manifest as audit-only
----------------------

The :class:`ContextManifest` returned here is what gets serialised
into the assistant turn's inline HTML comment.  It is **not**
re-read by the binder on future turns — every turn rebuilds
context fresh from current vault state.  Operators editing the
markdown manifest don't change system behaviour, only what future
audits show.

Turn-history compression
------------------------

Rolling-window helpers (:func:`select_verbatim_window`,
:func:`should_rebuild_summary`) live here so BL-084 can plug in
its own LLM summary writer without re-deriving the policy.  The
LLM summary call itself is BL-084's responsibility.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ovp_pipeline.context_loader import load_llm_context
from ovp_pipeline.runtime import resolve_vault_dir, split_markdown_frontmatter

logger = logging.getLogger(__name__)


# ── Token-budget knobs (defaults match the M21 plan) ────────────


# Fraction of the per-request input cap allocated to the anchor
# layer before retrieval gets the remainder.  60% keeps the
# artifact-under-discussion in dominant context even when the user
# asks something with a lot of vault matches.
ANCHOR_BUDGET_FRACTION: float = 0.6

# Char-to-token proxy.  Real BPE depends on the tokenizer but
# Anthropic + OpenAI both average ~4 ASCII chars / token.  CJK is
# closer to 1.5 chars / token but we under-budget on purpose to
# leave headroom.
_CHARS_PER_TOKEN: int = 4

# Conservative safety margin pulled off the top of the input cap
# before splitting between anchor + retrieval.  Covers BL-075's
# USER + RULES prefix and the system prompt frame the handler
# adds before the manifest.
SYSTEM_FRAME_MARGIN_TOKENS: int = 1500

# Turn-history compression policy (plan reference: BL-083 section).
TURN_HISTORY_VERBATIM_K: int = 4
TURN_HISTORY_SUMMARY_MAX_TOKENS: int = 600

# Token estimates per selected item.  Named to keep the budget math
# explicit (CodeRabbit M — replace magic numbers).
_TOKENS_PER_LINKED_EVERGREEN: int = 200
_TOKENS_PER_RETRIEVAL_ROW: int = 300

# Compiled once — used by ``_select_anchor_evergreens`` to harvest
# ``[[wikilinks]]`` from the anchor body.
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]+)?\]\]")


# ── Anchor kinds ────────────────────────────────────────────────


ANCHOR_KINDS: frozenset[str] = frozenset({"note", "object", "crystal", "standalone"})


# ── Data model ──────────────────────────────────────────────────


@dataclass(frozen=True)
class AnchorContext:
    """Fixed-per-session context grounded in one artifact.

    For ``kind == "standalone"`` the operator opened ``/chat``
    without an anchor; ``included_anchor`` is empty and the
    retrieval layer carries the whole load.
    """

    kind: str
    ref: str
    included_anchor: str = ""
    included_evergreens: tuple[str, ...] = ()
    included_crystals: tuple[str, ...] = ()
    token_estimate: int = 0


@dataclass(frozen=True)
class RetrievalHit:
    """One vault hit selected for the retrieval layer.

    ``slug`` + ``kind`` go into the manifest; ``title`` + ``snippet``
    + ``path`` end up in the prompt body so the LLM sees actual
    evidence text, not just a list of links (codex review P2).
    """

    slug: str
    kind: str  # "object" | "crystal" | "contradiction"
    title: str = ""
    path: str = ""
    snippet: str = ""


@dataclass(frozen=True)
class RetrievalContext:
    """Per-turn context, rebuilt from the user's message."""

    query: str
    hits: tuple[RetrievalHit, ...] = ()
    token_estimate: int = 0

    @property
    def included_objects(self) -> tuple[str, ...]:
        return tuple(h.slug for h in self.hits if h.kind == "object")

    @property
    def included_crystals(self) -> tuple[str, ...]:
        return tuple(h.slug for h in self.hits if h.kind == "crystal")

    @property
    def included_contradictions(self) -> tuple[str, ...]:
        return tuple(h.slug for h in self.hits if h.kind == "contradiction")


@dataclass(frozen=True)
class ContextManifest:
    """Audit snapshot of what context backed an assistant turn.

    Serialised into the inline ``<!-- context-manifest ... -->``
    HTML comment via :func:`manifest_to_lines`.  Never re-read.
    """

    anchor: AnchorContext
    retrieval: RetrievalContext
    omitted_count: int = 0
    omitted_reason: str = ""
    token_estimate_total: int = 0
    context_built_at: str = ""


# ── Public API ─────────────────────────────────────────────────


def estimate_tokens(text: str) -> int:
    """Rough char-count proxy for token estimation.

    Deliberately under-estimates so the budget has headroom.
    Used for selection / budgeting only — the handler's real LLM
    call may use a tokenizer-accurate count.
    """
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def split_budget(
    profile_input_cap: int,
    *,
    turn_history_tokens: int = 0,
    margin_tokens: int = SYSTEM_FRAME_MARGIN_TOKENS,
) -> tuple[int, int]:
    """Return (anchor_budget, retrieval_budget) given the profile cap.

    Subtracts ``margin_tokens`` for the system prompt frame and
    ``turn_history_tokens`` for the rolling turn-history window,
    then splits the remainder ~60/40.  Returns ``(0, 0)`` when the
    cap is too tight to leave any room — the handler then refuses
    the request.
    """
    available = profile_input_cap - margin_tokens - max(0, turn_history_tokens)
    if available <= 0:
        return 0, 0
    anchor_budget = int(available * ANCHOR_BUDGET_FRACTION)
    retrieval_budget = available - anchor_budget
    return anchor_budget, retrieval_budget


def build_chat_context(
    vault_dir: Path | str,
    *,
    anchor_kind: str,
    anchor_ref: str,
    user_message: str,
    profile_input_cap: int,
    turn_history_tokens: int = 0,
) -> tuple[str, ContextManifest]:
    """Build (system_prompt_body, manifest) for one inquiry turn.

    Two-layer construction:

    1. Anchor context (fixed-per-session shape, but built here
       so the handler doesn't carry it through the binder
       boundary): the artifact body + linked evergreens / cluster
       neighbours.  See :func:`build_anchor_context`.
    2. Retrieval context (per turn): wraps ``discover_related``
       to pull FTS + semantic hits scoped to the user message.
       See :func:`build_retrieval_context`.

    ``system_prompt_body`` is what the handler concatenates after
    BL-075's USER + RULES prefix (loaded inline here when the
    handler hasn't already attached it; safe to dedup downstream).

    Raises :class:`ValueError` for unknown ``anchor_kind``.
    """
    if anchor_kind not in ANCHOR_KINDS:
        raise ValueError(
            f"unknown anchor kind {anchor_kind!r}; " f"expected one of {sorted(ANCHOR_KINDS)}"
        )

    vault = resolve_vault_dir(vault_dir)
    anchor_budget, retrieval_budget = split_budget(
        profile_input_cap,
        turn_history_tokens=turn_history_tokens,
    )

    anchor = build_anchor_context(
        vault,
        anchor_kind=anchor_kind,
        anchor_ref=anchor_ref,
        budget_tokens=anchor_budget,
    )
    retrieval = build_retrieval_context(
        vault,
        query=user_message,
        budget_tokens=retrieval_budget,
    )

    omitted_count, omitted_reason = _summarise_omissions(
        anchor,
        retrieval,
        anchor_budget,
        retrieval_budget,
    )
    total_tokens = anchor.token_estimate + retrieval.token_estimate

    manifest = ContextManifest(
        anchor=anchor,
        retrieval=retrieval,
        omitted_count=omitted_count,
        omitted_reason=omitted_reason,
        token_estimate_total=total_tokens,
        context_built_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )

    prefix = load_llm_context(vault)
    system_prompt_body = _render_system_prompt(
        prefix=prefix,
        anchor=anchor,
        retrieval=retrieval,
    )
    return system_prompt_body, manifest


def manifest_to_lines(m: ContextManifest) -> list[str]:
    """Serialise a manifest to the ``manifest_lines`` list that
    :func:`ovp_pipeline.chat_fileops.append_turn` accepts.

    Format matches the plan-doc example (``key: value`` lines that
    render inside ``<!-- context-manifest ... -->``).
    """
    lines: list[str] = []
    lines.append(f"context_built_at: {m.context_built_at}")
    lines.append(f"token_estimate: {m.token_estimate_total}")
    lines.append(f"anchor_kind: {m.anchor.kind}")
    if m.anchor.ref:
        lines.append(f"anchor_ref: {m.anchor.ref}")
    if m.anchor.included_evergreens:
        lines.append("included_evergreens:")
        for slug in m.anchor.included_evergreens:
            lines.append(f"  - {slug}")
    if m.anchor.included_crystals:
        lines.append("included_anchor_crystals:")
        for slug in m.anchor.included_crystals:
            lines.append(f"  - {slug}")
    if m.retrieval.included_objects:
        lines.append("retrieval_objects:")
        for slug in m.retrieval.included_objects:
            lines.append(f"  - {slug}")
    if m.retrieval.included_crystals:
        lines.append("retrieval_crystals:")
        for slug in m.retrieval.included_crystals:
            lines.append(f"  - {slug}")
    if m.retrieval.included_contradictions:
        lines.append("retrieval_contradictions:")
        for slug in m.retrieval.included_contradictions:
            lines.append(f"  - {slug}")
    if m.omitted_count > 0:
        lines.append("omitted_items:")
        lines.append(f"  count: {m.omitted_count}")
        lines.append(f"  reason: {m.omitted_reason}")
    return lines


# ── Anchor layer ───────────────────────────────────────────────


def build_anchor_context(
    vault_dir: Path | str,
    *,
    anchor_kind: str,
    anchor_ref: str,
    budget_tokens: int,
) -> AnchorContext:
    """Load the anchor artifact + linked evergreens within the budget.

    The literal anchor body is always preserved (it's *what the
    operator is reading*); cluster neighbours / evergreens are
    dropped first when the budget is tight.

    For ``standalone`` anchors, returns an empty context — the
    operator's USER + RULES prefix is enough.

    Loads are tolerant: a missing file produces an empty
    ``included_anchor`` rather than raising, so the handler can
    still answer (operator may have moved or renamed the file).
    """
    vault = resolve_vault_dir(vault_dir)
    if anchor_kind == "standalone" or budget_tokens <= 0:
        return AnchorContext(kind=anchor_kind, ref=anchor_ref)

    body = _load_anchor_body(vault, anchor_kind, anchor_ref)
    body_tokens = estimate_tokens(body)
    if body_tokens > budget_tokens:
        # Truncate but record — never drop the anchor entirely.
        body = _truncate_to_tokens(body, budget_tokens)
        body_tokens = estimate_tokens(body)

    remaining = max(0, budget_tokens - body_tokens)
    evergreens: tuple[str, ...] = ()
    crystals: tuple[str, ...] = ()
    if remaining > 0:
        evergreens = _select_anchor_evergreens(body, remaining)
        crystals = _select_anchor_crystals(
            vault,
            anchor_kind,
            anchor_ref,
            remaining,
        )

    return AnchorContext(
        kind=anchor_kind,
        ref=anchor_ref,
        included_anchor=body,
        included_evergreens=evergreens,
        included_crystals=crystals,
        token_estimate=body_tokens,
    )


def _resolve_anchor_path(vault: Path, ref: str) -> Path | None:
    """Resolve ``ref`` to an absolute path inside ``vault``, or ``None``.

    Codex review P1: ``vault / "/etc/passwd"`` resolves to
    ``/etc/passwd`` (absolute paths override the left operand) and
    ``vault / "../etc/passwd"`` escapes the vault root.  Since
    chat frontmatter stores ``anchor.path`` as an unchecked
    string, a crafted session could otherwise read arbitrary
    local files into the system prompt.  This helper:

    * rejects absolute refs
    * rejects refs whose resolved path doesn't sit under the
      resolved vault root
    * returns ``None`` on either failure so callers degrade
      cleanly to "no anchor body" rather than raising
    """
    if not ref:
        return None
    candidate_rel = Path(ref)
    if candidate_rel.is_absolute():
        logger.warning(
            "context_binder: rejecting absolute anchor ref %r",
            ref,
        )
        return None
    resolved_vault = vault.resolve()
    candidate = (resolved_vault / candidate_rel).resolve()
    try:
        candidate.relative_to(resolved_vault)
    except ValueError:
        logger.warning(
            "context_binder: rejecting out-of-vault anchor ref %r " "(resolved to %s)",
            ref,
            candidate,
        )
        return None
    return candidate


def _load_anchor_body(
    vault: Path,
    kind: str,
    ref: str,
) -> str:
    """Return the body of the anchor artifact.

    ``ref`` interpretation:
    * ``note`` / ``object`` — vault-relative path.
    * ``crystal`` — vault-relative path or crystal slug.

    Returns ``""`` when the artifact can't be loaded — the handler
    still answers from retrieval + USER + RULES.  Strips
    frontmatter before returning so the prompt budget reflects
    real content, not YAML metadata (CodeRabbit M).
    """
    candidate = _resolve_anchor_path(vault, ref)
    if candidate is None:
        return ""
    if not candidate.is_file():
        logger.debug(
            "context_binder: anchor %s (kind=%s) not found at %s",
            ref,
            kind,
            candidate,
        )
        return ""
    try:
        raw = candidate.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug(
            "context_binder: failed to read anchor %s: %s",
            candidate,
            exc,
        )
        return ""
    _, body = split_markdown_frontmatter(raw)
    return body


def _select_anchor_evergreens(
    body: str,
    budget_tokens: int,
) -> tuple[str, ...]:
    """Return slugs of evergreens the anchor body links to.

    First-version implementation: parses ``[[wikilink]]`` targets
    out of the already-loaded anchor body — discovers links
    without needing the DB.  The retrieval layer fills in
    semantically-related items the anchor doesn't explicitly link
    to.  CodeRabbit M: takes ``body`` directly to avoid the
    redundant disk read.
    """
    if budget_tokens <= 0 or not body:
        return ()
    found: list[str] = []
    seen: set[str] = set()
    for match in _WIKILINK_RE.finditer(body):
        slug = match.group(1).strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        found.append(slug)
        if len(found) * _TOKENS_PER_LINKED_EVERGREEN >= budget_tokens:
            break
    return tuple(found)


def _select_anchor_crystals(
    vault: Path,
    kind: str,
    ref: str,
    budget_tokens: int,
) -> tuple[str, ...]:
    """Crystal anchor neighbours.

    Stub: returns ``()`` for v1.  BL-085's projection adds the
    join over ``crystal_scores`` so the binder can surface the
    top community / contradiction crystals adjacent to the
    anchor.  Until that lands, the retrieval layer covers it.
    """
    return ()


# ── Retrieval layer ────────────────────────────────────────────


def build_retrieval_context(
    vault_dir: Path | str,
    *,
    query: str,
    budget_tokens: int,
    top_k: int = 10,
) -> RetrievalContext:
    """Pull FTS + semantic hits for the user message.

    Wraps :func:`ovp_pipeline.discovery.discover_related` — the
    same helper that powers ``ovp-query``.  Returns at most
    ``top_k`` slugs, trimmed further by ``budget_tokens``.

    Defensive on missing knowledge.db: returns an empty context
    rather than raising so the handler can still answer from the
    anchor + system frame.
    """
    if budget_tokens <= 0 or not query.strip():
        return RetrievalContext(query=query)

    try:
        from ovp_pipeline.discovery import discover_related

        rows = discover_related(
            resolve_vault_dir(vault_dir),
            query,
            engine="knowledge",
            limit=top_k,
        )
    except Exception as exc:
        # Don't take down the inquiry surface when the DB isn't
        # ready — degrade to anchor-only.
        logger.warning(
            "context_binder: discover_related failed (%s); retrieval layer empty",
            exc,
        )
        return RetrievalContext(query=query)

    hits: list[RetrievalHit] = []
    used_tokens = 0
    for row in rows:
        slug = str(row.get("slug") or "").strip()
        path = str(row.get("path") or "").strip()
        if not slug and not path:
            continue
        if not slug:
            slug = path
        if used_tokens + _TOKENS_PER_RETRIEVAL_ROW > budget_tokens:
            break
        # Codex review P2: ``row['kind']`` is the *retrieval mode*
        # (``lexical`` / ``semantic`` / ``fts``), not the object
        # kind.  Use ``object_kind`` for classification — that's
        # the annotation discover_related adds for crystals.
        object_kind = str(row.get("object_kind") or "")
        if "contradiction" in object_kind:
            hit_kind = "contradiction"
        elif "crystal" in object_kind:
            hit_kind = "crystal"
        else:
            hit_kind = "object"
        snippet = str(row.get("snippet") or row.get("excerpt") or "").strip()
        title = str(row.get("title") or slug).strip()
        hits.append(
            RetrievalHit(
                slug=slug,
                kind=hit_kind,
                title=title,
                path=path,
                snippet=snippet,
            )
        )
        used_tokens += _TOKENS_PER_RETRIEVAL_ROW

    return RetrievalContext(
        query=query,
        hits=tuple(hits),
        token_estimate=used_tokens,
    )


# ── Turn-history compression policy ────────────────────────────


@dataclass(frozen=True)
class TurnPair:
    """One user/assistant pair from the turn history."""

    user_body: str
    assistant_body: str
    turn_number: int


def select_verbatim_window(
    pairs: Iterable[TurnPair],
    k: int = TURN_HISTORY_VERBATIM_K,
) -> tuple[list[TurnPair], list[TurnPair]]:
    """Split turn history into (older, recent) at the K boundary.

    The most-recent ``k`` pairs go into the verbatim window
    (kept as-is in the prompt); earlier pairs need a summary.
    Empty history returns ``([], [])``.
    """
    pair_list = list(pairs)
    if not pair_list:
        return [], []
    pair_list.sort(key=lambda p: p.turn_number)
    if len(pair_list) <= k:
        return [], pair_list
    return pair_list[:-k], pair_list[-k:]


def should_rebuild_summary(
    cached_summary_through_turn: int,
    older_pairs: list[TurnPair],
) -> bool:
    """Return True when the summary cache needs a refresh.

    The cache stores the last-turn-number it summarised through;
    when the verbatim window has slid (i.e. there are older pairs
    whose turn_number exceeds the cached value), the summary needs
    a new pass.  BL-084 owns the actual LLM call.
    """
    if not older_pairs:
        return False
    newest_older = max(p.turn_number for p in older_pairs)
    return newest_older > cached_summary_through_turn


# ── Internal: prompt rendering + omission summary ──────────────


def _render_system_prompt(
    *,
    prefix: str,
    anchor: AnchorContext,
    retrieval: RetrievalContext,
) -> str:
    """Combine USER + RULES + anchor + retrieval into one prompt body.

    Shape:

    * BL-075 prefix (USER profile + autonomous-action rules)
    * Anchor section (only when non-empty)
    * Retrieval section (only when non-empty)

    Empty sections are skipped so a standalone inquiry doesn't
    waste tokens on placeholder headings.
    """
    parts: list[str] = []
    if prefix:
        parts.append(prefix.rstrip())
    if anchor.included_anchor:
        parts.append(f"# Anchor — {anchor.kind}: {anchor.ref}\n\n{anchor.included_anchor.rstrip()}")
        if anchor.included_evergreens:
            parts.append(
                "## Linked evergreens\n\n"
                + "\n".join(f"- [[{slug}]]" for slug in anchor.included_evergreens)
            )
    if retrieval.hits:
        # Codex review P2: emit the snippet / title / path text the
        # retrieval stack returned — not just a list of [[slugs]] —
        # so the LLM has actual evidence to reason over.
        blocks: list[str] = []
        for hit in retrieval.hits:
            header = f"### {hit.kind}: {hit.title or hit.slug}"
            ref_line = f"slug: [[{hit.slug}]]"
            if hit.path:
                ref_line += f"   ·   path: {hit.path}"
            body_line = hit.snippet or "(no snippet)"
            blocks.append(f"{header}\n\n{ref_line}\n\n{body_line}")
        parts.append("# Retrieval — vault hits for this turn\n\n" + "\n\n".join(blocks))
    return "\n\n".join(parts) + ("\n" if parts else "")


def _summarise_omissions(
    anchor: AnchorContext,
    retrieval: RetrievalContext,
    anchor_budget: int,
    retrieval_budget: int,
) -> tuple[int, str]:
    """Crude estimate of how many items were dropped due to budget.

    The selectors return only what fit; this function compares the
    realised counts against soft targets and surfaces a reason.
    """
    omitted = 0
    reasons: list[str] = []
    # Anchor pressure: if the body was actually trimmed.  Strict
    # ``>`` instead of ``>=`` (CodeRabbit) so an anchor that fits
    # exactly doesn't read as truncated.
    if anchor.token_estimate > 0 and anchor.token_estimate > anchor_budget:
        omitted += 1
        reasons.append("anchor_truncated_to_budget")
    # Retrieval pressure: if we filled near the budget, treat the
    # tail as omitted.
    if retrieval.token_estimate > 0 and retrieval.token_estimate >= retrieval_budget * 0.8:
        omitted += 1
        reasons.append("retrieval_budget_filled")
    if not reasons:
        return 0, ""
    return omitted, ",".join(reasons)


def _truncate_to_tokens(text: str, budget_tokens: int) -> str:
    """Truncate ``text`` to roughly ``budget_tokens`` worth of chars.

    Trims on a paragraph boundary when possible so a half-sentence
    isn't passed to the LLM.
    """
    if budget_tokens <= 0:
        return ""
    max_chars = max(1, budget_tokens * _CHARS_PER_TOKEN)
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_para = truncated.rfind("\n\n")
    if last_para > max_chars // 2:
        truncated = truncated[:last_para]
    return truncated.rstrip() + "\n\n[truncated — anchor body exceeded budget]\n"


__all__ = [
    "ANCHOR_BUDGET_FRACTION",
    "ANCHOR_KINDS",
    "AnchorContext",
    "ContextManifest",
    "RetrievalContext",
    "RetrievalHit",
    "SYSTEM_FRAME_MARGIN_TOKENS",
    "TURN_HISTORY_SUMMARY_MAX_TOKENS",
    "TURN_HISTORY_VERBATIM_K",
    "TurnPair",
    "build_anchor_context",
    "build_chat_context",
    "build_retrieval_context",
    "estimate_tokens",
    "manifest_to_lines",
    "select_verbatim_window",
    "should_rebuild_summary",
    "split_budget",
]
