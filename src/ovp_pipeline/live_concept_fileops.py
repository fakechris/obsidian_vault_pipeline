r"""BL-063 single-writer for the ``live:`` YAML block.

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

Verbatim preservation of non-``live:`` keys
-------------------------------------------

The helpers splice the ``live:`` block in / out of the raw
frontmatter text rather than re-rendering the whole metadata
dict.  This preserves comments, blank lines, scalar styles, and
key order on every other top-level frontmatter key — same
property the agent body editor will need in PR#3 for the body
sections.

Frontmatter fences
------------------

Both standard ``---`` fences and fenced YAML
(``\`\`\`yaml\\n---\\n...\\n---\\n\`\`\``) are supported, matching
:func:`ovp_pipeline.runtime.split_markdown_frontmatter`.  The
fence style is preserved on write so a file that started fenced
stays fenced.

Locking
-------

PR#1 ships with a no-op default lock — every helper takes an
``acquire_lock`` callable, but the default :func:`_default_lock`
just yields without serialising anything.  This is fine for the
PR#1 use cases (operator declares / removes a concept by hand;
no concurrent writers exist yet) but is **explicitly not safe**
for PR#2's trigger scheduler, which will fire concurrent
``patch_live`` calls when multiple triggers match the same
concept.  PR#2 will plug in a real per-file ``filelock`` via the
``acquire_lock`` parameter.

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


def _render_live_block(fm: LiveConceptFrontmatter) -> str:
    """Serialise ``fm`` as the ``live:`` block, including the leading
    ``live:`` line.  Ends without trailing newline; caller decides
    how to splice it in."""
    return _ordered_dump({"live": _frontmatter_to_yaml_block(fm)})


# ---------------------------------------------------------------------------
# Frontmatter splice helpers — preserve non-live keys verbatim
# ---------------------------------------------------------------------------

# Standard fence (``---``) is the OVP convention.  Fenced YAML
# (``\`\`\`yaml`` or ``\`\`\`yml``) is rare but appears in some
# Obsidian setups; ``runtime.split_markdown_frontmatter`` accepts both,
# so we mirror it here to avoid a write-side regression that would
# trip up :func:`parse_live_concept`.
_STANDARD_FENCE = "standard"
_FENCED_YAML = "fenced"


def _split_frontmatter(text: str) -> tuple[str, str, str]:
    """Return ``(raw_frontmatter, body, fence_style)``.

    Empty raw_frontmatter + full text as body when the file has no
    frontmatter — caller decides whether to inject one.

    fence_style is ``""`` (no frontmatter), ``"standard"``, or
    ``"fenced"``.  Preserved on rejoin so a fenced file stays
    fenced after a write.

    Distinct from :func:`runtime.split_markdown_frontmatter` which
    returns parsed metadata + body; we need the raw frontmatter
    text so we can splice the surrounding non-``live:`` keys back
    verbatim on patch / delete paths.
    """
    if text.startswith("```yaml\n---\n") or text.startswith("```yml\n---\n"):
        first_newline = text.find("\n")
        closing = "\n---\n```"
        end = text.find(closing, first_newline + 1)
        if end < 0:
            return "", text, ""
        raw = text[first_newline + len("\n---\n") : end]
        body_start = end + len(closing)
        if body_start < len(text) and text[body_start] == "\n":
            body_start += 1
        body = text[body_start:]
        return raw, body, _FENCED_YAML
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end < 0:
            return "", text, ""
        raw = text[4:end]
        body_start = end + len("\n---")
        if body_start < len(text) and text[body_start] == "\n":
            body_start += 1
        body = text[body_start:]
        return raw, body, _STANDARD_FENCE
    return "", text, ""


def _join_frontmatter(raw_frontmatter: str, body: str, fence_style: str) -> str:
    """Re-render frontmatter + body to a single markdown string.

    Empty frontmatter → no fence at all (file becomes pure body).
    """
    if not raw_frontmatter.strip():
        return body.lstrip("\n") if body else ""
    raw = raw_frontmatter.rstrip("\n")
    if fence_style == _FENCED_YAML:
        return f"```yaml\n---\n{raw}\n---\n```\n\n{body.lstrip(chr(10))}"
    return f"---\n{raw}\n---\n\n{body.lstrip(chr(10))}"


def _is_top_level_key_line(line: str) -> bool:
    """True when ``line`` is a top-level YAML key (column-0, has a colon,
    not a comment)."""
    if not line:
        return False
    if line[0].isspace():
        return False
    if line.startswith("#"):
        return False
    if line.startswith("---") or line.startswith("..."):
        return False
    return ":" in line


def _find_top_level_block(raw_frontmatter: str, key: str) -> tuple[int, int] | None:
    """Find the line range ``[start, end)`` of the top-level ``key:``
    block in raw_frontmatter.

    Returns ``None`` when the key isn't present.  ``end`` is the
    line index of the next top-level key (or ``len(lines)`` at EOF),
    so the slice ``lines[start:end]`` includes the key line plus
    every continuation / nested / blank line that belongs to it.

    Why line-based instead of YAML-AST: we need to splice text
    verbatim, including any comments or unusual scalar styles the
    user wrote.  PyYAML's safe_load discards both.  ruamel.yaml
    preserves them but adds a heavy dep.  A line-scan keyed on
    "starts at column 0" is enough for the keys we'd find in OVP
    frontmatter — none of them use folded literals at column 0 as
    multi-line scalars.
    """
    lines = raw_frontmatter.splitlines()
    prefix_a = f"{key}:"
    prefix_b = f"{key} :"  # tolerate odd whitespace around the colon
    start: int | None = None
    for i, line in enumerate(lines):
        if line.startswith(prefix_a) or line.startswith(prefix_b):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if _is_top_level_key_line(lines[j]):
            end = j
            break
    return (start, end)


def _splice_top_level_block(
    raw_frontmatter: str,
    key: str,
    new_block: str | None,
) -> str:
    """Replace, insert, or remove a top-level key's block.

    ``new_block`` is the complete YAML for the key (e.g.
    ``"live:\\n  objective: x"``) without trailing newline.  ``None``
    removes the block.  When the key doesn't exist and ``new_block``
    is given, append at the end of the frontmatter.  When the key
    doesn't exist and ``new_block`` is ``None``, no-op.

    Lines outside the spliced range are returned byte-for-byte
    identical to the input — that's the verbatim-preservation
    contract for non-``live:`` keys.
    """
    lines = raw_frontmatter.splitlines()
    block_range = _find_top_level_block(raw_frontmatter, key)
    if block_range is None:
        if new_block is None:
            return raw_frontmatter
        # Append at the end.  Strip trailing blank lines from existing
        # frontmatter first so the join doesn't double-blank.
        while lines and lines[-1].strip() == "":
            lines.pop()
        new_lines = lines + new_block.splitlines()
        return "\n".join(new_lines)
    start, end = block_range
    if new_block is None:
        new_lines = lines[:start] + lines[end:]
    else:
        new_lines = lines[:start] + new_block.splitlines() + lines[end:]
    return "\n".join(new_lines)


def _read_top_level_value(raw_frontmatter: str, key: str) -> Any:
    """Parse just enough YAML to read the current value of one key.

    Returns ``None`` when the key is absent or unparseable.  Used by
    :func:`patch_live` to read the existing ``live:`` block before
    applying updates, and by guards that check ``type:`` without
    re-rendering the whole metadata.
    """
    if not raw_frontmatter.strip():
        return None
    try:
        parsed = yaml.safe_load(raw_frontmatter)
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed.get(key)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def _read_file(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@contextmanager
def _default_lock(_path: Path) -> Iterator[None]:
    """No-op default lock context.

    Documented in the module header: PR#1 has no concurrent writers,
    so the read-modify-write windows in :func:`set_live` /
    :func:`patch_live` / :func:`delete_live` aren't serialised.
    PR#2's trigger scheduler will plug in a real per-file
    ``filelock`` via the ``acquire_lock`` parameter when concurrent
    trigger fires become possible.
    """
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
    immediately in Obsidian.  Other frontmatter keys are preserved
    byte-for-byte (including comments and scalar styles).  The
    ``type: live-concept`` marker is auto-set so callers don't need
    to pass it.
    """
    with acquire_lock(path):
        text = _read_file(path)
        if not text:
            stub_h1 = path.stem.replace("-", " ").replace("_", " ").title()
            text = f"# {stub_h1}\n"
        raw, body, fence = _split_frontmatter(text)
        if not fence:
            fence = _STANDARD_FENCE
        raw = _splice_top_level_block(raw, "type", f"type: {LIVE_CONCEPT_TYPE}")
        raw = _splice_top_level_block(raw, "live", _render_live_block(fm))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_join_frontmatter(raw, body, fence), encoding="utf-8")


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

    Raises ``ValueError`` when:

    * ``path`` doesn't exist
    * the file isn't marked ``type: live-concept`` (we refuse to
      claim a non-live note even when it happens to carry a
      ``live:`` key — caller is expected to use :func:`set_live`
      for first-time declarations)
    * the ``live:`` block is missing or unparseable
    """
    with acquire_lock(path):
        text = _read_file(path)
        if not text:
            raise ValueError(f"cannot patch_live: {path} does not exist")
        raw, body, fence = _split_frontmatter(text)
        if not fence:
            raise ValueError(
                f"cannot patch_live: {path} has no frontmatter"
            )
        if _read_top_level_value(raw, "type") != LIVE_CONCEPT_TYPE:
            raise ValueError(
                f"cannot patch_live: {path} is not type: {LIVE_CONCEPT_TYPE}"
            )
        current_block = _read_top_level_value(raw, "live")
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
        raw = _splice_top_level_block(raw, "live", _render_live_block(new_fm))
        path.write_text(_join_frontmatter(raw, body, fence), encoding="utf-8")
        return new_fm


def delete_live(path: Path, *, acquire_lock: Any = _default_lock) -> None:
    """Make the file passive: strip the ``live:`` block + the
    ``type: live-concept`` marker.  Body content is preserved
    verbatim so the markdown stays readable in Obsidian.

    No-op when the file doesn't exist or doesn't carry a
    ``type: live-concept`` marker — idempotent on re-runs, and
    refuses to strip a stray ``live:`` key from a non-live note
    (that key isn't ours to remove).
    """
    with acquire_lock(path):
        text = _read_file(path)
        if not text:
            return
        raw, body, fence = _split_frontmatter(text)
        if not fence:
            return
        if _read_top_level_value(raw, "type") != LIVE_CONCEPT_TYPE:
            return
        raw = _splice_top_level_block(raw, "live", None)
        raw = _splice_top_level_block(raw, "type", None)
        path.write_text(_join_frontmatter(raw, body, fence), encoding="utf-8")
