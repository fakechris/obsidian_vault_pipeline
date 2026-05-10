"""BL-063: Live Concept primitive — data model + discovery.

A Live Concept is a user-declared markdown file under
``30-Projects/Tracking/<slug>.md`` whose YAML frontmatter carries a
``type: live-concept`` key plus a ``live:`` block describing the
operator's *interpretation surface* for one topic.  Where evergreen
notes are atomic facts (fourth-ledger Artifact + Claim), a Live
Concept is the *Interpretation* layer — the curated, evolving view
that the operator declares they care about.

Parsed shape::

    ---
    type: live-concept
    live:
      objective: |
        维护我对 LLM eval 方法论的当前理解。
        每周重新综合一次「My take」之外的部分。
      active: true
      triggers:
        on_ingest_match:
          concept_similarity_to: "llm-eval"
          threshold: 0.65
        on_contradiction_against_view: true
        weekly_resynthesis: "Mon 09:00"
      scope_evergreens:
        - llm-eval-leakage
        - eval-cost-vs-quality
      # Runtime-managed (don't hand-write):
      lastAttemptAt: "2026-05-10T08:00:00Z"
      lastRunAt: "2026-05-10T08:00:01Z"
      lastRunSummary: "Refreshed Recent Evidence with 3 new claims."
      lastRunError: ""
    ---

    # LLM Eval Landscape

    ## My take  <!-- user-owned section; agent never writes here -->

    ...

    ## Current synthesis  <!-- agent-owned -->

    ...

This module **does not** wire triggers or invoke any agent.  It only
defines the data model + discovery + parser:

* :func:`parse_live_concept` — read one file, return a
  :class:`LiveConceptHandle` or ``None`` when the file isn't a live
  concept (no frontmatter, missing ``type: live-concept``, or no
  ``live:`` block).
* :func:`list_live_concepts` — walk ``30-Projects/Tracking/`` and
  return handles for every live concept the vault carries.

PR#2 implements the three triggers (``on_ingest_match``,
``on_contradiction_against_view``, ``weekly_resynthesis``) on top of
this discovery.  PR#3 wires the agent prompt + section-aware patch
edits.  See ``BACKLOG.md`` BL-063 for the full sizing.

The single-writer invariant applies at the YAML-key level: only the
companion module ``live_concept_fileops`` writes the ``live:`` block
into a markdown file.  Every other writer (the agent body editor in
PR#3, the user via Obsidian UI) leaves ``live:`` byte-for-byte
intact.  Same architectural pattern Rowboat uses for their
``LiveNote`` schema — see the ``LIVE_NOTE.md`` discussion in the
2026-05-10 strategy review for the rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runtime import read_markdown_frontmatter, resolve_vault_dir

# Live concepts live under this vault-relative directory.  Single
# location keeps discovery cheap (one ``rglob`` instead of walking
# the whole vault) and gives the operator a clear mental model
# ("anything under 30-Projects/Tracking/ is being actively
# tracked").  Future BLs may add more roots; ``list_live_concepts``
# accepts a custom ``root`` argument so callers don't have to wait
# for a code change.
LIVE_CONCEPT_DIR = "30-Projects/Tracking"

# YAML ``type`` value that flags a markdown file as a Live Concept.
# Frontmatter without this exact value is ignored (so other
# ``type: ...`` notes — moc, evergreen, decision, etc. — never
# accidentally get parsed as live concepts).
LIVE_CONCEPT_TYPE = "live-concept"


@dataclass(frozen=True)
class LiveConceptFrontmatter:
    """Parsed contents of the ``live:`` YAML block.

    Frozen so callers can't mutate fields by accident; mutating
    helpers (``patch_live`` in ``live_concept_fileops``) construct
    a fresh instance per update.

    The schema is **liberal on shape, conservative on names**:
    ``triggers`` is a free-form dict so future trigger types don't
    require a schema bump, and unknown top-level keys are dropped
    silently rather than raising.  But the field names below are
    fixed — handwriting ``last_run_at: ...`` (snake_case) into the
    YAML won't be picked up because OVP convention matches Rowboat
    here on camelCase ``lastRunAt`` for runtime-managed fields.
    """

    objective: str
    active: bool = True
    triggers: dict[str, Any] = field(default_factory=dict)
    scope_evergreens: tuple[str, ...] = ()

    # Runtime-managed.  Hand-writing these is supported (the parser
    # reads them back as-is) but pointless — the runner overwrites
    # them on every fire.  ``last_attempt_at`` advances on every
    # attempt (backoff anchor); ``last_run_at`` advances only on
    # successful runs (cycle anchor) — same two-timestamp split
    # Rowboat uses.
    last_attempt_at: str = ""
    last_run_at: str = ""
    last_run_summary: str = ""
    last_run_error: str = ""

    @property
    def is_active(self) -> bool:
        """Convenience predicate for trigger gates.  PR#2 will skip
        any concept where ``is_active`` is False without firing
        triggers — same contract as Rowboat's ``active !== false``."""
        return bool(self.active)


@dataclass(frozen=True)
class LiveConceptHandle:
    """A discovered live concept on disk.

    Carries the absolute path (for fileops calls), the
    vault-relative path (for audit logs / cross-machine references),
    the slug (filename stem; used as the concept identifier in
    audit rows + scheduler state), and the parsed frontmatter.
    """

    path: Path
    relative_path: str
    slug: str
    frontmatter: LiveConceptFrontmatter


def _coerce_str(value: Any) -> str:
    """Cast YAML scalars to ``str`` defensively.  ``None`` → empty
    string so callers can ``if x:`` without isinstance checks."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _coerce_str_tuple(value: Any) -> tuple[str, ...]:
    """Cast a YAML list-of-strings to ``tuple[str, ...]``, dropping
    blank entries.  ``None`` / non-list → empty tuple."""
    if value is None:
        return ()
    if isinstance(value, str):
        # Single string convenience — wrap so the schema stays a
        # list type without rejecting a perfectly valid one-item case.
        stripped = value.strip()
        return (stripped,) if stripped else ()
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        stripped = _coerce_str(item).strip()
        if stripped:
            out.append(stripped)
    return tuple(out)


def _coerce_dict(value: Any) -> dict[str, Any]:
    """Cast to ``dict``; non-dict → empty.  Unlike the str/tuple
    coercers above, we keep the dict's interior types as YAML
    parsed them — the trigger types in PR#2 will validate sub-keys
    per-trigger-kind."""
    if isinstance(value, dict):
        # Coerce keys to str for safety (YAML can produce non-string
        # keys for ``true`` / ``false`` / numeric-looking keys).
        return {str(k): v for k, v in value.items()}
    return {}


def parse_live_concept_block(block: Any) -> LiveConceptFrontmatter | None:
    """Build a :class:`LiveConceptFrontmatter` from the raw ``live``
    YAML node.  Returns ``None`` when the block is missing the
    required ``objective`` field.

    Liberal in what it accepts: any field absent from the block falls
    back to its dataclass default.  Unknown extra keys are silently
    dropped (forward-compat for future BL additions).
    """
    if not isinstance(block, dict):
        return None
    objective = _coerce_str(block.get("objective")).strip()
    if not objective:
        return None

    raw_active = block.get("active", True)
    # YAML ``active: false`` parses as ``False``; YAML ``active:
    # null`` parses as ``None``.  Default to active (True) when the
    # key is missing entirely; explicit ``false`` / ``null`` both
    # disable.
    active = bool(raw_active) if raw_active is not None else False
    if "active" not in block:
        active = True

    return LiveConceptFrontmatter(
        objective=objective,
        active=active,
        triggers=_coerce_dict(block.get("triggers")),
        scope_evergreens=_coerce_str_tuple(block.get("scope_evergreens")),
        last_attempt_at=_coerce_str(block.get("lastAttemptAt")),
        last_run_at=_coerce_str(block.get("lastRunAt")),
        last_run_summary=_coerce_str(block.get("lastRunSummary")),
        last_run_error=_coerce_str(block.get("lastRunError")),
    )


def parse_live_concept(path: Path) -> LiveConceptHandle | None:
    """Read a markdown file at ``path``; return a handle when it
    parses as a live concept, else ``None``.

    Three short-circuit cases that all return ``None`` (silently —
    callers iterate many files and don't want one bad file to abort
    the scan):

    * file doesn't exist or isn't readable
    * frontmatter is missing / malformed
    * frontmatter has no ``type: live-concept`` key
    * frontmatter has no ``live:`` block, or ``live:`` block is
      missing the required ``objective`` field

    Path determinism:  ``relative_path`` is computed against the
    closest ``30-Projects/Tracking/`` ancestor when present, falling
    back to the file's basename otherwise.  This keeps audit rows
    portable across machines without depending on the runner's
    working directory.
    """
    if not path.is_file():
        return None
    try:
        metadata = read_markdown_frontmatter(path)
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    except Exception:  # noqa: BLE001 — yaml.YAMLError + anything else
        # Real-vault YAML occasionally has unquoted ``@`` in
        # ``source_anchor:`` etc.  Same defence
        # ``backfill_objects_source_url`` uses (BL-060 PR review fix).
        return None

    if not isinstance(metadata, dict):
        return None
    if metadata.get("type") != LIVE_CONCEPT_TYPE:
        return None
    parsed = parse_live_concept_block(metadata.get("live"))
    if parsed is None:
        return None

    relative_path = _vault_relative_path(path)
    slug = path.stem
    return LiveConceptHandle(
        path=path,
        relative_path=relative_path,
        slug=slug,
        frontmatter=parsed,
    )


def _vault_relative_path(path: Path) -> str:
    """Return ``path`` relative to the closest ``30-Projects/Tracking/``
    ancestor, or to the vault root if we can find one, else just the
    file basename.  Always uses forward slashes (Obsidian convention)
    so the result is machine-portable."""
    parts = path.parts
    for marker_idx in range(len(parts) - 1, -1, -1):
        if parts[marker_idx] == "30-Projects" and marker_idx + 1 < len(parts) \
                and parts[marker_idx + 1] == "Tracking":
            return "/".join(parts[marker_idx:])
    return path.name


def list_live_concepts(
    vault_dir: Path | str,
    *,
    root: str = LIVE_CONCEPT_DIR,
    active_only: bool = False,
) -> list[LiveConceptHandle]:
    """Walk ``<vault_dir>/<root>/`` and return every live concept the
    vault carries.

    ``root`` overrides the default discovery directory (mostly for
    tests).  ``active_only=True`` drops concepts whose
    ``frontmatter.active`` is False — useful for the trigger
    scheduler in PR#2 which never wants to fire on archived /
    paused concepts.

    Discovery cost: one ``rglob('*.md')`` over the
    ``30-Projects/Tracking/`` subtree, which on the live vault is a
    few hundred files at most (Tracking is purposefully small).
    """
    resolved = resolve_vault_dir(vault_dir)
    base = resolved / root
    if not base.is_dir():
        return []
    handles: list[LiveConceptHandle] = []
    for md in sorted(base.rglob("*.md")):
        handle = parse_live_concept(md)
        if handle is None:
            continue
        if active_only and not handle.frontmatter.is_active:
            continue
        handles.append(handle)
    return handles
