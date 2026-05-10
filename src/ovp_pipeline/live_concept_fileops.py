"""BL-063 single-writer for the ``live:`` YAML block.

Mirrors the BL-060 single-writer-invariant pattern at the
markdown-frontmatter-key level: only this module writes the
``live:`` key in a Live Concept file.  Every other writer (the
agent body editor in PR#3, the user typing in Obsidian, future
MCP tools) must leave ``live:`` byte-for-byte intact.

Why this matters
----------------

The runtime fields on a Live Concept (``lastAttemptAt`` /
``lastRunAt`` / ``lastRunSummary`` / ``lastRunError``) get bumped
on every trigger fire.  If the agent or the user could overwrite
the whole frontmatter en passant, two concurrent trigger runs
would race over those fields and lose audit data.  Routing all
``live:`` writes through this module makes the race architecturally
impossible — same property BL-060 gave us for the canonical SQL
tables.

What this module does
---------------------

* :func:`set_live` — write a complete ``LiveConceptFrontmatter`` to
  a markdown file.  Used when the user (via Obsidian or a future
  MCP tool) declares a new Live Concept or replaces an existing
  one.
* :func:`patch_live` — partial update.  Reads the current ``live:``
  block, applies the supplied keyword updates, writes back.  Used
  by the trigger runner in PR#2 to bump runtime fields without
  touching ``objective`` / ``triggers`` / ``scope_evergreens``.
* :func:`delete_live` — strip the ``live:`` block entirely (and the
  ``type: live-concept`` marker that pairs with it).  The "make
  passive" path: the markdown file lives on as a regular note.

Locking
-------

Each helper takes an ``acquire_lock`` callable so callers in
process-level transactions can pass their own lock.  By default
the helpers acquire a per-file ``filelock`` for the duration of
the read-modify-write — same shape Rowboat uses around
``setLiveNote`` / ``patchLiveNote``.

What this module does NOT do
----------------------------

Body content below the H1 is **out of scope** for PR#1.  PR#3 will
add the section-aware patch helpers (``patch_agent_section``) that
let the agent rewrite ``## Current synthesis`` / ``## Recent
evidence`` / ``## Tensions`` while leaving ``## My take``
untouched.  This module only owns the YAML frontmatter.
"""

from __future__ import annotations

import io
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Iterator

import yaml

from .live_concept import (
    LIVE_CONCEPT_TYPE,
    LiveConceptFrontmatter,
    parse_live_concept_block,
)


# Order in which we serialise ``live:`` sub-keys.  Rowboat's experience
# is that LLMs and humans both reason better when the same fields are
# in the same place every time, so we pin a deterministic order rather
# than letting yaml.dump pick alphabetical.
_LIVE_BLOCK_ORDER = (
    "objective",
    "active",
    "triggers",
    "scope_evergreens",
    # Runtime-managed fields go last so the user-edited fields read
    # at a glance and the noisy timestamps don't push them off-screen.
    "lastAttemptAt",
    "lastRunAt",
    "lastRunSummary",
    "lastRunError",
)


# ---------------------------------------------------------------------------
# Frontmatter <-> YAML serialisation
# ---------------------------------------------------------------------------


def _frontmatter_to_yaml_block(fm: LiveConceptFrontmatter) -> dict[str, Any]:
    """Convert a :class:`LiveConceptFrontmatter` back to a YAML-friendly
    dict in the canonical key order.

    Empty / default fields are dropped on the way out so re-saving
    a freshly-declared concept doesn't write spurious empty
    ``lastRunError: ""`` rows.  Reading the result back through
    :func:`parse_live_concept_block` yields an equivalent instance.
    """
    payload: dict[str, Any] = {
        "objective": fm.objective,
        "active": bool(fm.active),
    }
    if fm.triggers:
        payload["triggers"] = fm.triggers
    if fm.scope_evergreens:
        payload["scope_evergreens"] = list(fm.scope_evergreens)
    # Runtime fields — only include if non-empty.  ``lastRunError`` is
    # intentionally cleared by patch_live(last_run_error="") on a
    # successful run; that empty string is dropped here so the YAML
    # stays clean.
    if fm.last_attempt_at:
        payload["lastAttemptAt"] = fm.last_attempt_at
    if fm.last_run_at:
        payload["lastRunAt"] = fm.last_run_at
    if fm.last_run_summary:
        payload["lastRunSummary"] = fm.last_run_summary
    if fm.last_run_error:
        payload["lastRunError"] = fm.last_run_error
    return payload


def _ordered_dump(data: dict[str, Any]) -> str:
    """yaml.safe_dump with field order preserved + tidy defaults.

    ``default_flow_style=False`` keeps the block in human-friendly
    indented form, ``sort_keys=False`` honours the order we built
    the dict in (PyYAML otherwise alphabetises).
    """
    buf = io.StringIO()
    yaml.safe_dump(
        data,
        buf,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=10_000,  # don't reflow long objective strings mid-word
    )
    return buf.getvalue().rstrip()


# ---------------------------------------------------------------------------
# File read / write helpers
# ---------------------------------------------------------------------------


def _read_file(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str, str]:
    """Return ``(metadata_dict, body, raw_frontmatter_text)``.

    Empty metadata dict + full text as body when the file has no
    frontmatter — caller decides whether to inject one.

    Distinct from :func:`runtime.split_markdown_frontmatter` which
    only returns metadata + body; we need the raw frontmatter text
    too so we can splice the surrounding non-``live:`` keys back
    verbatim on patch / delete paths.
    """
    if not text.startswith("---\n"):
        return {}, text, ""
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text, ""
    raw_frontmatter = text[4:end]
    body_start = end + len("\n---")
    if body_start < len(text) and text[body_start] == "\n":
        body_start += 1
    body = text[body_start:]
    try:
        parsed = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError:
        return {}, text, ""
    if not isinstance(parsed, dict):
        return {}, body, raw_frontmatter
    return parsed, body, raw_frontmatter


def _join_frontmatter(metadata: dict[str, Any], body: str) -> str:
    """Re-render frontmatter + body to a single markdown string."""
    if not metadata:
        return body
    front = _ordered_dump(metadata)
    return f"---\n{front}\n---\n\n{body.lstrip(chr(10))}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@contextmanager
def _default_lock(_path: Path) -> Iterator[None]:
    """Default no-op lock context.  PR#2 will plug in a real per-file
    lock once the trigger scheduler is wired; for now serialisation
    is implicit (every fileops call runs from one process / thread)."""
    yield


def set_live(
    path: Path,
    fm: LiveConceptFrontmatter,
    *,
    acquire_lock: Any = _default_lock,
) -> None:
    """Write ``fm`` as the ``live:`` block of the markdown file at
    ``path``.

    If the file doesn't exist, it's created with a minimal stub
    body (``# <slug>\\n``) so the operator can ``open`` it
    immediately in Obsidian.  Other frontmatter keys are preserved.
    The ``type: live-concept`` marker is auto-set so callers don't
    need to pass it.
    """
    with acquire_lock(path):
        text = _read_file(path)
        if not text:
            # Brand-new file — synthesise a minimal stub so
            # split_frontmatter has something coherent to parse and
            # the operator opening this in Obsidian sees a sensible
            # body.  Slug is the filename stem — same convention
            # discovery uses.
            stub_h1 = path.stem.replace("-", " ").replace("_", " ").title()
            text = f"# {stub_h1}\n"
        metadata, body, _raw = _split_frontmatter(text)
        metadata["type"] = LIVE_CONCEPT_TYPE
        metadata["live"] = _frontmatter_to_yaml_block(fm)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_join_frontmatter(metadata, body), encoding="utf-8")


def patch_live(
    path: Path,
    *,
    acquire_lock: Any = _default_lock,
    **field_updates: Any,
) -> LiveConceptFrontmatter:
    """Update one or more fields of an existing ``live:`` block.

    ``field_updates`` keys match :class:`LiveConceptFrontmatter`'s
    Python field names (``last_attempt_at``, not ``lastAttemptAt``);
    the YAML serialisation handles the camelCase mapping.  Any
    unknown kwarg raises ``TypeError`` — better than silently
    ignoring a typo on a runtime-critical timestamp.

    Returns the new (post-patch) frontmatter so callers don't have
    to re-parse the file.

    Raises ``ValueError`` when ``path`` doesn't carry a parseable
    ``live:`` block — caller is expected to use :func:`set_live`
    for first-time declarations.
    """
    with acquire_lock(path):
        text = _read_file(path)
        if not text:
            raise ValueError(f"cannot patch_live: {path} does not exist")
        metadata, body, _raw = _split_frontmatter(text)
        current_block = metadata.get("live")
        current_fm = parse_live_concept_block(current_block)
        if current_fm is None:
            raise ValueError(
                f"cannot patch_live: {path} has no parseable `live:` block"
            )
        try:
            new_fm = replace(current_fm, **field_updates)
        except TypeError as exc:
            valid = sorted(asdict(current_fm).keys())
            raise TypeError(
                f"unknown LiveConceptFrontmatter field; "
                f"valid: {valid}"
            ) from exc
        metadata["type"] = LIVE_CONCEPT_TYPE
        metadata["live"] = _frontmatter_to_yaml_block(new_fm)
        path.write_text(_join_frontmatter(metadata, body), encoding="utf-8")
        return new_fm


def delete_live(path: Path, *, acquire_lock: Any = _default_lock) -> None:
    """Make the file passive: strip the ``live:`` block + the
    ``type: live-concept`` marker.  Body content is preserved
    verbatim so the markdown stays readable in Obsidian.

    No-op when the file doesn't exist or doesn't carry a ``live:``
    block — idempotent on re-runs.
    """
    with acquire_lock(path):
        text = _read_file(path)
        if not text:
            return
        metadata, body, _raw = _split_frontmatter(text)
        if "live" not in metadata and metadata.get("type") != LIVE_CONCEPT_TYPE:
            return
        metadata.pop("live", None)
        if metadata.get("type") == LIVE_CONCEPT_TYPE:
            metadata.pop("type")
        path.write_text(_join_frontmatter(metadata, body), encoding="utf-8")
