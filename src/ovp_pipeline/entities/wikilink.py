"""Auto-wikilink: scan prose, replace canonical-entity mentions with
``[[canonical_handle|original_text]]`` Obsidian wikilinks.

Companion to ``entities/aliases.py`` (BL-038) and the extraction
prime (BL-039).  This module is the **read-time wiring** that
turns "the entity layer knows who Karpathy is" into "the evergreen
notes link to the canonical entity page".

Skip regions
------------

The replacer must NEVER touch:

  * **Frontmatter** — the ``---``-delimited YAML block at the top of
    every Obsidian note.  Wikilinks in frontmatter would corrupt
    YAML parsing.
  * **Fenced code blocks** (``` ``` `` / ``` ~~~ ```) — code is
    literal; turning ``karpathy`` into a link inside a code sample
    would silently break copy-paste.
  * **Inline code** (`` `...` ``) — same.
  * **Existing wikilinks** ``[[...]]`` — already linked, don't
    double-link.
  * **Existing markdown links** ``[text](url)`` — leave the user's
    explicit link intact.

Match semantics
---------------

Word-boundary regex with case-insensitive lookup.  For an alias
that is itself the canonical (``karpathy`` → ``karpathy``), emit
``[[karpathy]]``.  For any other alias (``Andrej Karpathy`` →
``karpathy``), emit ``[[karpathy|Andrej Karpathy]]`` so Obsidian
preserves the prose surface.

Longest-first ordering: when multiple aliases match the same span
(e.g., ``karpathy`` is a prefix of ``karpathy.ai``), the longer
alias wins.

CJK is supported via Python's default ``re.UNICODE`` semantics —
display names like ``歸藏`` and ``姚金刚`` round-trip cleanly.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .aliases import (
    KIND_AT_HANDLE,
    KIND_EXPLICIT_ALIAS,
    KIND_GITHUB_LOGIN,
    KIND_PRIMARY,
    EntityAlias,
)

logger = logging.getLogger(__name__)


# Which alias_kinds are safe to auto-link.  ``KIND_DISPLAY_NAME`` is
# deliberately EXCLUDED by default because it's auto-derived from
# ``entity.canonical_name`` and trips on common English words —
# e.g., a github_user with login ``image1`` and canonical_name
# ``Image`` yields a ``display_name`` alias of ``image`` that would
# rewrite every occurrence of "image" in evergreen prose.  Users
# can opt in with the ``kinds=`` parameter; the default keeps the
# auto-link surface to handles + explicit aliases.
DEFAULT_LINKABLE_KINDS: frozenset[str] = frozenset({
    KIND_PRIMARY,
    KIND_AT_HANDLE,
    KIND_EXPLICIT_ALIAS,
    KIND_GITHUB_LOGIN,
})

# Minimum alias length we'll auto-link.  ``ai`` (2 chars) shouldn't
# linkify every "AI" mention; ``xxx`` (3 chars) is ambiguous but
# borderline-OK if the entity is real.  Set to 3 by default to err
# on the safe side — users can lower it for niche use cases.
DEFAULT_MIN_ALIAS_LENGTH = 3


# Pre-compiled patterns for the skip-region scan.  Used in order;
# results are merged into a single flat list of (start, end) ranges.
_FRONTMATTER_RE = re.compile(r"\A---\n[\s\S]*?\n---\n")
_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_WIKILINK_RE = re.compile(r"\[\[[^\]\n]+\]\]")
_MD_LINK_RE = re.compile(r"\[[^\]\n]+\]\([^)\n]+\)")


@dataclass(frozen=True, slots=True)
class WikilinkResult:
    """One file's worth of replacement output."""

    text: str            # the rewritten markdown
    n_replaced: int      # how many alias hits were converted to wikilinks
    canonicals_used: set[str]  # which canonical_handles got linked


# ---------------------------------------------------------------------------
# Skip regions
# ---------------------------------------------------------------------------


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Coalesce overlapping/adjacent ranges so the in-skip check is
    a single pass instead of N regex hits per match."""
    if not ranges:
        return []
    ranges = sorted(ranges)
    merged = [ranges[0]]
    for start, end in ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _find_skip_regions(text: str) -> list[tuple[int, int]]:
    """Identify char spans that the alias replacer must leave alone."""
    ranges: list[tuple[int, int]] = []
    fm = _FRONTMATTER_RE.match(text)
    if fm is not None:
        ranges.append((fm.start(), fm.end()))
    # Fenced code first (bigger), then inline; merging dedupes.
    for m in _FENCED_CODE_RE.finditer(text):
        ranges.append((m.start(), m.end()))
    for m in _INLINE_CODE_RE.finditer(text):
        ranges.append((m.start(), m.end()))
    for m in _WIKILINK_RE.finditer(text):
        ranges.append((m.start(), m.end()))
    for m in _MD_LINK_RE.finditer(text):
        ranges.append((m.start(), m.end()))
    return _merge_ranges(ranges)


def _is_in_skip(pos: int, skip: list[tuple[int, int]]) -> bool:
    # Linear scan — skip lists are tiny in practice (<50 ranges per
    # evergreen).  Binary search would be premature optimization.
    for s, e in skip:
        if s <= pos < e:
            return True
    return False


# ---------------------------------------------------------------------------
# Alias pattern + replacement
# ---------------------------------------------------------------------------


def _is_ascii(s: str) -> bool:
    return all(ord(c) < 128 for c in s)


def build_alias_pattern(alias_index: dict[str, EntityAlias]) -> re.Pattern[str]:
    """Compile a single regex covering every alias.

    Sorted longest-first so the regex engine's left-to-right
    alternation prefers the most specific match.  Word-boundary
    behaviour is split by alias script:

      * **ASCII aliases** — Unicode-aware ``\\w`` boundary on both sides.
        ``foo@karpathy`` doesn't match ``karpathy`` (left side is
        a word char), ``karpathys`` doesn't match ``karpathy``
        (right side is a word char).

      * **CJK / non-ASCII aliases** — ASCII-only boundary on both
        sides.  Python's ``\\w`` treats CJK letters as word chars,
        which would block the common case of CJK prose where the
        alias has no whitespace neighbours (``我觉得歸藏写过这个``
        — ``得`` is ``\\w``, so ``(?<!\\w)`` would fail).  The
        ASCII-only boundary still rejects ``karpathy歸藏`` (``y``
        is ASCII alpha) but accepts the natural CJK case.

    Returns a sentinel regex that never matches (alternation of
    nothing) when the index is empty.
    """
    if not alias_index:
        return re.compile(r"(?!x)x")        # never matches
    parts = sorted(alias_index.keys(), key=len, reverse=True)
    ascii_parts = [p for p in parts if _is_ascii(p)]
    cjk_parts = [p for p in parts if not _is_ascii(p)]
    branches: list[str] = []
    if ascii_parts:
        branches.append(
            r"(?<![\w@])(?:"
            + "|".join(re.escape(p) for p in ascii_parts)
            + r")(?!\w)"
        )
    if cjk_parts:
        branches.append(
            r"(?<![A-Za-z0-9_@])(?:"
            + "|".join(re.escape(p) for p in cjk_parts)
            + r")(?![A-Za-z0-9_])"
        )
    return re.compile("|".join(branches), re.IGNORECASE)


def _normalize_for_lookup(matched: str) -> str:
    return matched.strip().lstrip("@").lower()


@dataclass(frozen=True, slots=True)
class PreparedMatcher:
    """Pre-filtered + pre-compiled state for batch auto-wikilink runs.

    ``apply_wikilinks`` filters the alias index and compiles the regex
    on every call — fine for a one-shot, expensive when scanning
    thousands of evergreens.  ``prepare_matcher`` builds the state
    once; ``apply_prepared_matcher`` reuses it.
    """

    filtered_index: dict[str, EntityAlias]
    pattern: re.Pattern[str]


def _passes_length(alias: str, min_length: int) -> bool:
    """Length floor with a CJK escape hatch: a 2-char Chinese name
    like ``歸藏`` carries far more entropy than a 2-char English
    bigram like ``ai``, so non-ASCII aliases bypass the floor."""
    if len(alias) >= min_length:
        return True
    return any(ord(c) > 127 for c in alias)


def prepare_matcher(
    alias_index: dict[str, EntityAlias],
    *,
    kinds: frozenset[str] | set[str] | None = None,
    min_length: int = DEFAULT_MIN_ALIAS_LENGTH,
) -> PreparedMatcher:
    """Pre-build the filter + regex once, for batch reuse.

    ``ovp-link-entities`` walks ~7000 evergreens with ~929 canonicals;
    re-filtering + re-compiling per file was a measurable cost.  Now
    the CLI calls ``prepare_matcher`` once outside the loop and feeds
    the result into ``apply_prepared_matcher`` per file.
    """
    if kinds is None:
        kinds = DEFAULT_LINKABLE_KINDS
    filtered = {
        a: row for a, row in alias_index.items()
        if row.alias_kind in kinds and _passes_length(a, min_length)
    }
    return PreparedMatcher(
        filtered_index=filtered,
        pattern=build_alias_pattern(filtered),
    )


def apply_wikilinks(
    text: str,
    alias_index: dict[str, EntityAlias],
    *,
    kinds: frozenset[str] | set[str] | None = None,
    min_length: int = DEFAULT_MIN_ALIAS_LENGTH,
) -> WikilinkResult:
    """Rewrite ``text`` so canonical-entity mentions become wikilinks.

    Parameters
    ----------
    kinds :
        Which ``alias_kind`` values are safe to auto-link.  Default
        is ``DEFAULT_LINKABLE_KINDS`` which excludes
        ``display_name`` — see the constant's docstring for why.
        Pass an explicit set to opt in to the noisier kinds.
    min_length :
        Skip aliases shorter than this many chars.  Default 3
        prevents ``ai`` / ``ml`` from linking every occurrence of
        those bigrams in evergreen prose.

    Returns the rewritten text + a count of replacements + the set
    of canonicals that got at least one link.  Idempotent on the
    rewritten output: running again is a no-op because the new
    wikilinks land in the skip regions.

    Rebuilds the filter + regex on every call.  Batch callers
    walking many files should use ``prepare_matcher`` +
    ``apply_prepared_matcher`` to amortize the cost.
    """
    if not alias_index:
        return WikilinkResult(text=text, n_replaced=0, canonicals_used=set())
    matcher = prepare_matcher(
        alias_index, kinds=kinds, min_length=min_length,
    )
    return apply_prepared_matcher(text, matcher)


def apply_prepared_matcher(
    text: str, matcher: PreparedMatcher,
) -> WikilinkResult:
    """Batch entry point: apply a pre-built matcher to one file.

    The matcher carries the filtered alias index + compiled regex,
    both expensive to build.  Callers running over thousands of
    files build the matcher once and call this per file.
    """
    if not matcher.filtered_index:
        return WikilinkResult(text=text, n_replaced=0, canonicals_used=set())

    skip = _find_skip_regions(text)
    pattern = matcher.pattern
    filtered_index = matcher.filtered_index
    n_replaced = 0
    used: set[str] = set()

    def _replace(m: re.Match[str]) -> str:
        nonlocal n_replaced
        if _is_in_skip(m.start(), skip):
            return m.group(0)
        matched = m.group(0)
        canonical_alias = filtered_index.get(_normalize_for_lookup(matched))
        if canonical_alias is None:
            return matched
        canonical = canonical_alias.canonical_handle
        used.add(canonical)
        n_replaced += 1
        # When the prose is already exactly the canonical, no alias
        # piping needed — keeps the wikilink terse.
        if _normalize_for_lookup(matched) == canonical:
            return f"[[{canonical}]]"
        return f"[[{canonical}|{matched}]]"

    new_text = pattern.sub(_replace, text)
    return WikilinkResult(text=new_text, n_replaced=n_replaced, canonicals_used=used)


# ---------------------------------------------------------------------------
# Entity stub generation
# ---------------------------------------------------------------------------


# Where the auto-wikilink targets live.  Existing OVP convention is
# ``10-Knowledge/Entity/`` (already used by entity_registry).  We
# create stubs only when missing — never overwrite a user's curated
# entity page.
_ENTITY_STUB_DIR = Path("10-Knowledge") / "Entity"


def _render_stub_frontmatter(alias: EntityAlias) -> str:
    """Render the minimal frontmatter for a stub entity page.

    Kept narrow on purpose: enough fields for the page to render in
    Obsidian + carry the canonical_handle for downstream tooling,
    but nothing that pretends the user has reviewed the entity.
    The ``stub: true`` flag tells the rest of OVP this is a
    placeholder and a human hasn't curated the body yet.
    """
    parts = [
        "---",
        f"slug: {alias.canonical_handle}",
        f"entity_type: {alias.canonical_entity_type}",
        f"canonical_handle: {alias.canonical_handle}",
    ]
    if alias.authority is not None:
        parts.append(f"authority: {alias.authority:.4f}")
    parts.extend([
        "stub: true",
        'created_by: "ovp-link-entities"',
        "tags: [entity, stub]",
        "---",
        "",
        f"# {alias.canonical_handle}",
        "",
        "*Auto-generated entity stub.  Replace this body with curated content "
        "when you have time — until then it serves as a wikilink target so "
        "evergreen-body mentions don't dead-end.*",
        "",
    ])
    return "\n".join(parts)


def ensure_entity_stub_files(
    vault_dir: Path,
    canonicals: dict[str, EntityAlias],
    *,
    dry_run: bool = False,
) -> list[Path]:
    """Create ``10-Knowledge/Entity/<canonical>.md`` for each
    canonical in ``canonicals`` that doesn't already have a
    markdown page.

    ``canonicals`` should be ``{canonical_handle: representative_alias}``
    — one alias per canonical (the representative tells us the
    entity_type + authority for the stub frontmatter).

    Returns the list of paths that were created (or *would be*
    created in dry-run).  Never overwrites an existing file.
    """
    entity_dir = vault_dir / _ENTITY_STUB_DIR
    if not dry_run:
        entity_dir.mkdir(parents=True, exist_ok=True)
    # Resolve once for the path-escape guard below.  ``parents=True``
    # makes mkdir succeed even if the dir already exists; resolve()
    # on a missing dir works on every platform we ship to.
    entity_dir_resolved = entity_dir.resolve()
    created: list[Path] = []
    for canonical, alias in canonicals.items():
        path = entity_dir / f"{canonical}.md"
        # Defense in depth: collect_entity_aliases already filters
        # canonical_handle through a slug allowlist, but if a future
        # caller wires this function up with a different source we
        # still refuse to write outside the entity stub directory.
        try:
            path.resolve().relative_to(entity_dir_resolved)
        except ValueError:
            logger.warning(
                "refusing to write entity stub outside %s: canonical=%r",
                entity_dir_resolved, canonical,
            )
            continue
        if path.exists():
            continue
        created.append(path)
        if dry_run:
            continue
        path.write_text(_render_stub_frontmatter(alias), encoding="utf-8")
    return created
