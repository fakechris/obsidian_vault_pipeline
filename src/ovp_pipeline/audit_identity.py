"""Unified audit-event identity normalization (M24 correctness fix).

Background
----------

Historically the lifecycle kernel only indexed audit evidence by
three payload keys: ``slug``, ``object_id``, ``cluster_id``.  But
the dominant real-vault producers never used those keys:

* ``evergreen_auto_promoted`` (10k+ rows on the operator vault)
  carries ``concept`` + ``mutation.slug`` / ``mutation.target_slug``
  + a ``source`` deep-dive filename + a ``path``.
* ``source_*`` / ``absorb_route_decision`` carry only ``source``
  (a full file path).
* ``article_processed`` / ``candidates_upserted`` /
  ``article_intake_only`` carry only ``file`` (a bare filename).

Net effect: ~13k kernel-relevant events were *identity-less* to
the kernel, so Received / Extracted / Accepted were systematically
under-counted and the bulk of attribution fell through to the
``objects``-table projection path rather than the audit evidence.

This module is the single source of truth for "given an audit
payload, what object / source / cluster identities does it carry".
``knowledge_index._infer_audit_slug`` (writes the
``audit_events.slug`` column) and ``ops_lifecycle._build_audit_index``
(builds the in-memory inverted index) both call into here so the
two cannot drift apart again.

Hard boundary
-------------

Source-class identities (``source`` / ``file`` / ``path``) MUST
NOT be mixed into the object index, and object-class identities
(``concept`` / ``mutation.*``) MUST NOT be mixed into the source
slug column.  Source lifecycle and object lifecycle are distinct
ledgers; cross-contaminating them would make one pollute the
other's counts.  The three extractor functions below are
deliberately separate and never share inputs.
"""

from __future__ import annotations

from typing import Any

from .identity import canonicalize_note_id


# ── Nested-payload walk ────────────────────────────────────────────


def collect_string_values(node: Any, keys: tuple[str, ...]) -> set[str]:
    """Return every non-empty string value whose key is in ``keys``,
    at ANY depth of a parsed-JSON tree.

    Mirrors the legacy SQL ``LIKE`` fallback semantics — that scan
    matched nested mentions (``{"mutation": {"object_id": "…"}}``)
    because it scanned the whole JSON text.  The in-memory index
    must preserve that or evidence rows silently disappear.
    """
    found: set[str] = set()

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if k in keys and isinstance(v, str) and v:
                    found.add(v)
                _walk(v)
        elif isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(node)
    return found


# Object-class payload keys that are UNAMBIGUOUS at any depth.
# Deliberately excludes a bare ``slug``: the M24.2-era source
# producers (``absorb_pending_upsert`` / ``candidates_upserted`` /
# ``community_crystal_synthesized``) carry a top-level ``slug``
# that is the SOURCE slug, not an object id.  Sweeping ``slug``
# via the blind nested walk would contaminate ``by_object_id``
# with source identities (codex review on PR #247).  The
# object-class ``mutation.slug`` is recovered explicitly from the
# ``mutation`` dict below instead, so it is captured without the
# ambiguous top-level sweep.
_OBJECT_KEYS: tuple[str, ...] = (
    "object_id",
    "concept",
    "target_slug",
)

# Source-class payload keys.  These are file paths / filenames,
# never object slugs.
_SOURCE_KEYS: tuple[str, ...] = (
    "source",
    "file",
    "path",
    "source_path",
)

_CLUSTER_KEYS: tuple[str, ...] = ("cluster_id",)


def _basename_no_ext(value: str) -> str:
    """Strip directory + a single trailing ``.md``.  Keeps the
    pipeline suffixes (``_深度解读`` / ``_原文``) intact — merging
    raw vs deep-dive artifacts of one logical source is a separate
    product decision, not something this normalizer should do
    silently."""
    tail = value.replace("\\", "/").rsplit("/", 1)[-1]
    if tail.endswith(".md"):
        tail = tail[:-3]
    return tail


def audit_object_ids(payload: dict[str, Any]) -> set[str]:
    """Object-class identities for the kernel ``by_object_id`` index.

    Indexes BOTH the verbatim value and its canonicalized form so a
    lookup keyed on ``objects.object_id`` (already canonical) hits
    regardless of whether the producer wrote canonical or raw.
    Source / file keys are intentionally excluded — see the module
    docstring's hard boundary.
    """
    candidates: set[str] = set(
        collect_string_values(payload, _OBJECT_KEYS)
    )
    # ``mutation.slug`` is object-class ONLY in the mutation
    # context (promote/merge).  Extract it precisely from the
    # ``mutation`` dict rather than via the blind walk so a
    # top-level source ``slug`` never enters here.
    mutation = payload.get("mutation")
    if isinstance(mutation, dict):
        m_slug = mutation.get("slug")
        if isinstance(m_slug, str) and m_slug.strip():
            candidates.add(m_slug)

    out: set[str] = set()
    for raw in candidates:
        raw = raw.strip()
        if not raw:
            continue
        out.add(raw)
        canon = canonicalize_note_id(raw)
        if canon:
            out.add(canon)
    return out


def audit_cluster_ids(payload: dict[str, Any]) -> set[str]:
    """Cluster-class identities for the kernel ``by_cluster_id``
    index (nested-aware)."""
    return {
        c.strip()
        for c in collect_string_values(payload, _CLUSTER_KEYS)
        if c.strip()
    }


def audit_slug_for_column(payload: dict[str, Any]) -> str:
    """The single value for the ``audit_events.slug`` column —
    a SOURCE-class identity used by source-lifecycle discovery
    (``SELECT DISTINCT slug FROM audit_events``).

    Priority:

    1. Explicit ``slug`` (already the intended key).
    2. Single-element ``targets`` list (legacy deep-dive callers).
    3. ``source`` / ``file`` / ``path`` / ``source_path`` —
       basename, ``.md`` stripped, canonicalized.  This is the fix:
       previously these produced an empty slug column so the
       source lifecycle never saw the event.
    4. ``target_path`` — returned RAW (not canonicalized).  The
       lint ``check_zone_boundary`` rule matches by this exact key
       and a path.stem collapse would break it
       (e.g. ``30-Projects/*/Plan.md``).  Preserved verbatim from
       the pre-existing ``_infer_audit_slug`` contract.

    Object-class keys (``concept`` / ``mutation.*``) are NOT
    consulted here — those belong only in the object index.
    """
    slug = payload.get("slug")
    if isinstance(slug, str) and slug.strip():
        return canonicalize_note_id(slug.strip())

    targets = payload.get("targets")
    if (
        isinstance(targets, list)
        and len(targets) == 1
        and isinstance(targets[0], str)
        and targets[0].strip()
    ):
        return canonicalize_note_id(targets[0].strip())

    for key in _SOURCE_KEYS:
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return canonicalize_note_id(_basename_no_ext(val.strip()))

    target_path = payload.get("target_path")
    if isinstance(target_path, str) and target_path.strip():
        # RAW — lint zone-boundary contract (do not canonicalize).
        return target_path

    return ""


__all__ = [
    "audit_cluster_ids",
    "audit_object_ids",
    "audit_slug_for_column",
    "collect_string_values",
]
