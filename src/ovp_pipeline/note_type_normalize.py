"""Note-type normalization (Phase 38.D).

The vault accumulated 50+ ad-hoc ``note_type`` values over time
(``engineering-blog-post``, ``ai-marketing-automation``, ``Threat
Intelligence Report``, ``论文深度解读``, ...). Most are singletons. This
module collapses them into a small canonical set so type-based filtering,
lint, and graph viz palettes can rely on a closed vocabulary.

Canonical set (8 values):

- ``raw``        — files in ``50-Inbox/01-Raw``.
- ``deep_dive``  — interpreted articles in ``20-Areas``.
- ``evergreen``  — atomic concepts in ``10-Knowledge/Evergreen``.
- ``moc``        — maps of content in ``10-Knowledge/Atlas``.
- ``daily_view`` — daily delta snapshots.
- ``article``    — external long-form (blog post, paper, technical analysis).
- ``project``    — external code/tool reference (github project, tool review).
- ``essay``      — long-form opinion/manifesto, distinct from neutral
  ``article`` so the "voice" signal is preserved for downstream readers.

Anything outside this set is reported by ``ovp-lint`` and rewritten by
``ovp-note-type-normalize`` according to the mapping in
``data/note_type_normalization.yaml``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CANONICAL_NOTE_TYPES: frozenset[str] = frozenset(
    {
        "raw",
        "deep_dive",
        "evergreen",
        "moc",
        "daily_view",
        "article",
        "project",
        "essay",
    }
)


_DEFAULT_MAPPING_PATH = Path(__file__).parent / "data" / "note_type_normalization.yaml"


@dataclass(frozen=True)
class NormalizationMapping:
    """Loaded mapping table.

    ``mapping`` is the legacy → canonical lookup. ``extras`` is the set of
    additional canonical types pack manifests opted into (kept separate so
    they aren't silently re-mapped).
    """

    mapping: dict[str, str]
    extras: frozenset[str] = frozenset()

    def canonical_set(self) -> frozenset[str]:
        return CANONICAL_NOTE_TYPES | self.extras

    def normalize(self, value: str) -> str:
        """Return the canonical type for ``value``.

        - If ``value`` is already canonical (or in ``extras``), return as-is.
        - If the lowercased value is in ``mapping``, return its target.
        - Otherwise return ``"article"`` as the safe catch-all (long-form
          external content) and let lint surface the unmapped case.
        """
        cleaned = (value or "").strip()
        if not cleaned:
            return "article"
        if cleaned in self.canonical_set():
            return cleaned
        return self.mapping.get(cleaned.lower(), "article")


def load_mapping(path: Path | None = None) -> NormalizationMapping:
    """Load the YAML mapping file. ``path=None`` uses the bundled default."""
    target = path or _DEFAULT_MAPPING_PATH
    with target.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    raw_mapping = data.get("mapping") or {}
    extras = frozenset(data.get("extras") or [])
    normalized: dict[str, str] = {}
    for legacy, canonical in raw_mapping.items():
        if not isinstance(legacy, str) or not isinstance(canonical, str):
            continue
        normalized[legacy.strip().lower()] = canonical.strip()
    return NormalizationMapping(mapping=normalized, extras=extras)


@dataclass(frozen=True)
class NoteTypeChange:
    """One file's frontmatter rewrite."""

    path: Path
    old_value: str
    new_value: str


@dataclass
class NormalizationReport:
    changed: list[NoteTypeChange] = field(default_factory=list)
    skipped: list[NoteTypeChange] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        lines = [f"changed:  {len(self.changed)}", f"skipped:  {len(self.skipped)}"]
        if self.errors:
            lines.append(f"errors:   {len(self.errors)}")
        return lines


_FRONTMATTER_RE = re.compile(r"^(---\n)(.*?)(\n---\n?)", re.DOTALL)
_TYPE_LINE_RE = re.compile(r"^(\s*type\s*:\s*)(.+?)(\s*)$", re.MULTILINE)
_NOTE_TYPE_LINE_RE = re.compile(r"^(\s*note_type\s*:\s*)(.+?)(\s*)$", re.MULTILINE)
_ORIGINAL_LINE_RE = re.compile(r"^\s*original_note_type\s*:", re.MULTILINE)


def _strip_yaml_quotes(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
        return cleaned[1:-1]
    return cleaned


def _quote_yaml_value(value: str) -> str:
    if value and re.fullmatch(r"[A-Za-z0-9_\-\.]+", value):
        return value
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"'


def _replace_or_insert(frontmatter_body: str, key: str, value: str) -> str:
    """Replace ``key: ...`` line (matching the first occurrence) or append a
    new one to the body. Body does not include the surrounding ``---`` fences.
    """
    pattern = re.compile(rf"^(\s*{re.escape(key)}\s*:\s*)(.+?)(\s*)$", re.MULTILINE)
    quoted = _quote_yaml_value(value)
    if pattern.search(frontmatter_body):
        return pattern.sub(lambda m: f"{m.group(1)}{quoted}", frontmatter_body, count=1)
    suffix = "\n" if frontmatter_body and not frontmatter_body.endswith("\n") else ""
    return f"{frontmatter_body}{suffix}{key}: {quoted}"


def rewrite_note_type(
    text: str, *, new_value: str, preserve_original: bool = True
) -> tuple[str, str | None]:
    """Rewrite the ``type:`` (and ``note_type:``) frontmatter values.

    Returns ``(new_text, original_value)``. ``original_value`` is the
    pre-rewrite value (or ``None`` if the file had no frontmatter at all).

    When ``preserve_original`` is True and the file had a non-empty
    ``type:``/``note_type:`` distinct from ``new_value``, an
    ``original_note_type:`` field is added/refreshed so the migration is
    invertible.
    """
    fm_match = _FRONTMATTER_RE.match(text)
    if not fm_match:
        return text, None

    fm_open, fm_body, fm_close = fm_match.group(1), fm_match.group(2), fm_match.group(3)

    type_match = _TYPE_LINE_RE.search(fm_body)
    note_type_match = _NOTE_TYPE_LINE_RE.search(fm_body)
    primary_match = type_match or note_type_match
    if primary_match is None:
        return text, None

    original_value = _strip_yaml_quotes(primary_match.group(2))
    if original_value == new_value:
        return text, original_value

    quoted_new = _quote_yaml_value(new_value)
    new_body = fm_body
    if type_match:
        new_body = _TYPE_LINE_RE.sub(
            lambda m: f"{m.group(1)}{quoted_new}", new_body, count=1
        )
    if note_type_match:
        new_body = _NOTE_TYPE_LINE_RE.sub(
            lambda m: f"{m.group(1)}{quoted_new}", new_body, count=1
        )

    if preserve_original and original_value and not _ORIGINAL_LINE_RE.search(new_body):
        new_body = _replace_or_insert(new_body, "original_note_type", original_value)

    rebuilt = f"{fm_open}{new_body}{fm_close}{text[fm_match.end():]}"
    return rebuilt, original_value


def iter_markdown_with_frontmatter(vault_dir: Path) -> list[Path]:
    """All ``*.md`` under ``vault_dir`` excluding ``.git`` and template files."""
    files: list[Path] = []
    for candidate in vault_dir.rglob("*.md"):
        rel_parts = candidate.relative_to(vault_dir).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        if candidate.stem.startswith("_"):
            continue
        files.append(candidate)
    return files


def plan_normalization(
    vault_dir: Path, mapping: NormalizationMapping
) -> NormalizationReport:
    """Walk the vault and produce a report of intended changes without writing."""
    report = NormalizationReport()
    canonical = mapping.canonical_set()
    for md in iter_markdown_with_frontmatter(vault_dir):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError as exc:
            report.errors.append((md, f"read failed: {exc}"))
            continue
        fm_match = _FRONTMATTER_RE.match(text)
        if not fm_match:
            continue
        fm_body = fm_match.group(2)
        type_match = _TYPE_LINE_RE.search(fm_body) or _NOTE_TYPE_LINE_RE.search(fm_body)
        if not type_match:
            continue
        old = _strip_yaml_quotes(type_match.group(2))
        new = mapping.normalize(old)
        change = NoteTypeChange(path=md, old_value=old, new_value=new)
        if old == new or new in canonical and old == new:
            report.skipped.append(change)
        elif old in canonical:
            report.skipped.append(change)
        else:
            report.changed.append(change)
    return report


def apply_normalization(
    vault_dir: Path,
    mapping: NormalizationMapping,
    *,
    dry_run: bool = False,
) -> NormalizationReport:
    """Plan + (when ``dry_run`` is False) write changes back to disk."""
    plan = plan_normalization(vault_dir, mapping)
    if dry_run:
        return plan
    applied: list[NoteTypeChange] = []
    for change in plan.changed:
        try:
            text = change.path.read_text(encoding="utf-8")
            new_text, _ = rewrite_note_type(text, new_value=change.new_value)
            if new_text != text:
                change.path.write_text(new_text, encoding="utf-8")
                applied.append(change)
        except OSError as exc:
            plan.errors.append((change.path, f"write failed: {exc}"))
    plan.changed = applied
    return plan
