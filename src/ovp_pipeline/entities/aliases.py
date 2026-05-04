"""``entity_aliases`` — single read surface for "what do we know
about every entity in the vault, by which strings can we identify
them in prose".

Unions four sources:

  1. ``60-Logs/authors.jsonl``
     Curated whitelist (PR-D1).  Each row carries a primary handle
     + optional ``aliases`` array + a hand-set authority.

  2. ``60-Logs/author_overrides.yaml``
     Same shape, yaml form (PR-D3).  Wins over JSONL on collision.

  3. ``entities`` table — twitter_author / person / organization /
     github_user (PR-E1/E2/E3/F1).  ``identity_key`` is the
     primary handle, ``canonical_name`` is the display string.
     Person / organization rows carry ``links`` arrays pointing at
     their platform-specific siblings, used to thread aliases
     across platforms.

  4. github_user.signals.canonical_handle — the back-link written
     by identity_merge (PR-F1 review fix #2).  Lets a github login
     resolve to its merged twitter_handle even when the merge was
     exact_handle / fuzzy and not self_reported.

This module produces the **data**.  Two consumers will plug into it
(M12 BL-039, BL-040):

  * ``auto_evergreen_extractor`` LLM prompt prime — feed top-N
    entity_aliases entries so the LLM uses canonical handles
    rather than inventing new ones.
  * ``ovp-link-entities`` (auto-wikilink) — scan evergreen body
    text, replace alias hits with ``[[canonical_handle]]``.

Output schema is intentionally flat — one row per (canonical, alias)
pair, with provenance + authority in every row.  Callers that want
"resolve string → canonical entity" build a dict; callers that want
"all aliases for canonical X" group on canonical_handle.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .store import Entity, EntityStore

logger = logging.getLogger(__name__)


# Sources we tag rows with — narrow Literal-style for grep-ability.
SOURCE_WHITELIST_JSONL = "whitelist_jsonl"
SOURCE_WHITELIST_YAML = "whitelist_yaml"
SOURCE_ENTITY_TWITTER = "entity_twitter_author"
SOURCE_ENTITY_PERSON = "entity_person"
SOURCE_ENTITY_ORGANIZATION = "entity_organization"
SOURCE_ENTITY_GITHUB_USER = "entity_github_user"

# What kind of string does the alias represent?  Used by downstream
# matchers to weight precision (an explicit @handle is unambiguous; a
# bare display name like "Andrej Karpathy" needs more context).
KIND_PRIMARY = "primary"            # the canonical_handle itself
KIND_AT_HANDLE = "at_handle"        # @karpathy form
KIND_DISPLAY_NAME = "display_name"  # "Andrej Karpathy"
KIND_EXPLICIT_ALIAS = "explicit_alias"  # row.aliases[i] from yaml/jsonl
KIND_GITHUB_LOGIN = "github_login"  # github_user.identity_key

# Truncation limit for malformed-line warning messages — full lines
# can be huge JSON blobs; 80 chars is enough to identify the line.
_LOG_TRUNCATION_CHARS = 80
# Fallback precedence rank when a source label isn't in the table.
# Higher than every real entry so unknown sources lose ties cleanly.
_FALLBACK_PRECEDENCE = 99


@dataclass(frozen=True, slots=True)
class EntityAlias:
    """One (alias-string, canonical-entity) pair.

    Multiple EntityAlias rows can point at the same canonical_handle
    — that's the whole point.  Callers that need a single
    "string → canonical" map should call ``build_alias_index``.
    """

    canonical_handle: str
    canonical_entity_type: str    # twitter_author / person / organization / whitelist
    alias: str                    # already lowercased + stripped + @-stripped
    alias_kind: str               # one of KIND_*
    authority: float | None       # max-of-platforms when known, else None
    source: str                   # one of SOURCE_*


def _normalize_alias(s: str | None) -> str:
    """Same canonicalization the resolver uses: lowercase + lstrip @ + trim."""
    if not s:
        return ""
    return s.strip().lstrip("@").lower()


# ---------------------------------------------------------------------------
# Source loaders
# ---------------------------------------------------------------------------


def _load_jsonl_whitelist(path: Path) -> list[EntityAlias]:
    """Read ``authors.jsonl``.  One JSON object per line; comments
    (``#``-prefixed) ignored.

    Each record contributes:
      * one ``primary`` row (handle → handle)
      * one ``at_handle`` row (handle → @handle, useful for prompt
        prime so the LLM treats ``@karpathy`` and ``karpathy`` as the
        same thing)
      * one ``explicit_alias`` row per ``aliases[]`` entry
    """
    if not path.exists():
        return []
    out: list[EntityAlias] = []
    # Stream the file line-by-line.  Authors files can grow to a few
    # MB on busy vaults; loading + splitlines doubles peak memory
    # for no benefit.
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "authors.jsonl parse error on %r: %s",
                    line[:_LOG_TRUNCATION_CHARS], exc,
                )
                continue
            handle_raw = rec.get("handle")
            if not isinstance(handle_raw, str):
                continue
            handle = _normalize_alias(handle_raw)
            if not handle:
                continue
            try:
                authority = (
                    float(rec.get("authority"))
                    if rec.get("authority") is not None else None
                )
            except (TypeError, ValueError):
                authority = None
            out.append(EntityAlias(
                canonical_handle=handle,
                canonical_entity_type="whitelist",
                alias=handle,
                alias_kind=KIND_PRIMARY,
                authority=authority,
                source=SOURCE_WHITELIST_JSONL,
            ))
            out.append(EntityAlias(
                canonical_handle=handle,
                canonical_entity_type="whitelist",
                alias=f"@{handle}",
                alias_kind=KIND_AT_HANDLE,
                authority=authority,
                source=SOURCE_WHITELIST_JSONL,
            ))
            aliases_raw = rec.get("aliases")
            if not isinstance(aliases_raw, list):
                continue
            for raw in aliases_raw:
                if not isinstance(raw, str):
                    continue
                alias = _normalize_alias(raw)
                if not alias or alias == handle:
                    continue
                out.append(EntityAlias(
                    canonical_handle=handle,
                    canonical_entity_type="whitelist",
                    alias=alias,
                    alias_kind=KIND_EXPLICIT_ALIAS,
                    authority=authority,
                    source=SOURCE_WHITELIST_JSONL,
                ))
    return out


def _load_yaml_overrides(path: Path) -> list[EntityAlias]:
    """Read ``author_overrides.yaml`` via the ``AuthorOverrides``
    loader so we get its validation + clamping for free.
    """
    if not path.exists():
        return []
    try:
        from ..source_signals.overrides import AuthorOverrides
    except ImportError:
        return []
    overrides = AuthorOverrides.load(path)
    out: list[EntityAlias] = []
    for rec in overrides.authors:
        handle = _normalize_alias(rec.get("handle"))
        if not handle:
            continue
        authority = rec.get("authority")
        out.append(EntityAlias(
            canonical_handle=handle,
            canonical_entity_type="whitelist",
            alias=handle,
            alias_kind=KIND_PRIMARY,
            authority=authority,
            source=SOURCE_WHITELIST_YAML,
        ))
        out.append(EntityAlias(
            canonical_handle=handle,
            canonical_entity_type="whitelist",
            alias=f"@{handle}",
            alias_kind=KIND_AT_HANDLE,
            authority=authority,
            source=SOURCE_WHITELIST_YAML,
        ))
        for raw in (rec.get("aliases") or []):
            if not isinstance(raw, str):
                continue
            alias = _normalize_alias(raw)
            if not alias or alias == handle:
                continue
            out.append(EntityAlias(
                canonical_handle=handle,
                canonical_entity_type="whitelist",
                alias=alias,
                alias_kind=KIND_EXPLICIT_ALIAS,
                authority=authority,
                source=SOURCE_WHITELIST_YAML,
            ))
    return out


def _emit_entity_aliases(
    entity: Entity, *, source_label: str, entity_type: str,
) -> list[EntityAlias]:
    """Yield EntityAlias rows for one ``entities`` table row.

    Always emits the primary identity_key + ``@identity_key`` form.
    Adds a display_name row when canonical_name differs from the
    handle (covers cases like ``identity_key='karpathy'``,
    ``canonical_name='Andrej Karpathy'``).
    """
    handle = _normalize_alias(entity.identity_key)
    if not handle:
        return []
    out: list[EntityAlias] = [
        EntityAlias(
            canonical_handle=handle,
            canonical_entity_type=entity_type,
            alias=handle,
            alias_kind=KIND_PRIMARY,
            authority=entity.derived_authority,
            source=source_label,
        ),
        EntityAlias(
            canonical_handle=handle,
            canonical_entity_type=entity_type,
            alias=f"@{handle}",
            alias_kind=KIND_AT_HANDLE,
            authority=entity.derived_authority,
            source=source_label,
        ),
    ]
    name = (entity.canonical_name or "").strip().lower()
    if name and name != handle:
        out.append(EntityAlias(
            canonical_handle=handle,
            canonical_entity_type=entity_type,
            alias=name,
            alias_kind=KIND_DISPLAY_NAME,
            authority=entity.derived_authority,
            source=source_label,
        ))
    return out


def _load_entities(store: EntityStore) -> list[EntityAlias]:
    """Walk twitter_author / person / organization / github_user
    rows in the entity table.  github_user rows that carry a
    ``canonical_handle`` back-link contribute their login as a
    ``github_login`` alias pointing at the canonical entity (so a
    text mention of the github login resolves to the merged
    person/organization, not the bare github_user).
    """
    out: list[EntityAlias] = []

    for e in store.list_by_type("twitter_author"):
        out.extend(_emit_entity_aliases(
            e, source_label=SOURCE_ENTITY_TWITTER, entity_type="twitter_author",
        ))

    for e in store.list_by_type("person"):
        out.extend(_emit_entity_aliases(
            e, source_label=SOURCE_ENTITY_PERSON, entity_type="person",
        ))

    for e in store.list_by_type("organization"):
        out.extend(_emit_entity_aliases(
            e, source_label=SOURCE_ENTITY_ORGANIZATION, entity_type="organization",
        ))

    # github_user — bare login is itself an alias.  When a back-link
    # exists to a person/organization, point the alias at THAT
    # canonical handle (so "karpathy" the github login resolves to
    # the merged person, not the bare github_user).  Otherwise the
    # github login is its own canonical.
    for e in store.list_by_type("github_user"):
        login = _normalize_alias(e.identity_key)
        if not login:
            continue
        backlink_type = e.signals.get("canonical_entity_type")
        backlink_handle = _normalize_alias(e.signals.get("canonical_handle"))
        if backlink_type and backlink_handle:
            out.append(EntityAlias(
                canonical_handle=backlink_handle,
                canonical_entity_type=backlink_type,
                alias=login,
                alias_kind=KIND_GITHUB_LOGIN,
                authority=e.derived_authority,
                source=SOURCE_ENTITY_GITHUB_USER,
            ))
        else:
            out.extend(_emit_entity_aliases(
                e, source_label=SOURCE_ENTITY_GITHUB_USER, entity_type="github_user",
            ))
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def collect_entity_aliases(
    *,
    vault_dir: Path,
    entity_store: EntityStore | None = None,
    authors_jsonl: Path | None = None,
    author_overrides_yaml: Path | None = None,
) -> list[EntityAlias]:
    """Union all four sources into a flat list.

    Defaults — pass ``None`` and we use the vault-relative paths:
      * ``<vault>/60-Logs/authors.jsonl``
      * ``<vault>/60-Logs/author_overrides.yaml``
      * ``EntityStore(<vault>/60-Logs/knowledge.db)``

    Returns a list, NOT a dict.  The list contains duplicates (same
    alias from multiple sources) on purpose — callers that need a
    single index should call ``build_alias_index`` to apply the
    precedence rule.  Other callers (e.g., the CLI dashboard) want
    the raw provenance data.
    """
    if authors_jsonl is None:
        authors_jsonl = vault_dir / "60-Logs" / "authors.jsonl"
    if author_overrides_yaml is None:
        author_overrides_yaml = vault_dir / "60-Logs" / "author_overrides.yaml"
    if entity_store is None:
        entity_store = EntityStore(db_path=vault_dir / "60-Logs" / "knowledge.db")

    out: list[EntityAlias] = []
    out.extend(_load_jsonl_whitelist(authors_jsonl))
    out.extend(_load_yaml_overrides(author_overrides_yaml))
    out.extend(_load_entities(entity_store))
    return out


# Precedence for collision resolution: lower number = higher priority.
# Curated whitelist beats derived entity data; canonical-merged
# entities beat raw platform rows.
_SOURCE_PRECEDENCE = {
    SOURCE_WHITELIST_YAML: 0,        # YAML is the editing surface
    SOURCE_WHITELIST_JSONL: 1,       # JSONL is legacy, still curated
    SOURCE_ENTITY_PERSON: 2,         # canonical merged person
    SOURCE_ENTITY_ORGANIZATION: 2,   # canonical merged organization
    SOURCE_ENTITY_TWITTER: 3,        # bare platform row
    SOURCE_ENTITY_GITHUB_USER: 4,
}


def _precedence(alias: EntityAlias) -> tuple[int, float]:
    """Sort key — lower is more authoritative.  Tiebreaker: HIGHER
    authority wins, so we negate it.
    """
    return (
        _SOURCE_PRECEDENCE.get(alias.source, _FALLBACK_PRECEDENCE),
        -(alias.authority or 0.0),
    )


def build_alias_index(aliases: list[EntityAlias]) -> dict[str, EntityAlias]:
    """Collapse the flat list into ``{lowercased_alias: best_row}``.

    Collision policy: when two sources claim the same alias string,
    pick by ``_SOURCE_PRECEDENCE`` (curated whitelist beats derived
    entity data); break ties on higher authority.  When the chosen
    rows still disagree on canonical_handle, log a warning — this
    is a real ambiguity that needs human attention.
    """
    by_alias: dict[str, EntityAlias] = {}
    for a in aliases:
        if not a.alias:
            continue
        existing = by_alias.get(a.alias)
        if existing is None:
            by_alias[a.alias] = a
            continue
        if _precedence(a) < _precedence(existing):
            if a.canonical_handle != existing.canonical_handle:
                logger.warning(
                    "alias %r resolves ambiguously: %s (%s, auth=%s) vs "
                    "%s (%s, auth=%s) — using %s",
                    a.alias,
                    a.canonical_handle, a.source, a.authority,
                    existing.canonical_handle, existing.source, existing.authority,
                    a.canonical_handle,
                )
            by_alias[a.alias] = a
    return by_alias
