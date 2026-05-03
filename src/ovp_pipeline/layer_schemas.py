"""Layer schema definitions + scanner.

Single source of truth for what each markdown layer's frontmatter must
contain.  Backed by ``ovp-doctor`` (CLI) and ``test_layer_schemas.py``
(CI fitness gate).

Layers (in dependency order):

  L1 Source         — 50-Inbox/03-Processed/{YYYY-MM}/*.md
  L2 Deep Dive      — 20-Areas/**/Topics/{YYYY-MM}/*_深度解读.md
  L3 Evergreen      — 10-Knowledge/Evergreen/*.md
  L4 Entity (active)   — 10-Knowledge/Entity/*.md   (excludes _Candidates)
  L5 Entity (candidate) — 10-Knowledge/Entity/_Candidates/*.md

Each layer has:
  required_fields  — frontmatter keys that MUST be present (non-empty)
  optional_fields  — keys that may appear (used to flag unknown fields)
  field_validators — per-field invariant checks (e.g. note_id == stem)

Violations are graded:
  HIGH    — silently breaks downstream (e.g. note_id mismatch)
  MEDIUM  — vocab drift / inconsistent values across the layer
  LOW     — missing optional field, cosmetic
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml


# ---------------------------------------------------------------------------
# Allowed entity_type values (for Entity layers + Evergreen)
# ---------------------------------------------------------------------------

EVERGREEN_ENTITY_TYPE = "concept"
ENTITY_LAYER_TYPES = {"person", "company", "tool", "project", "paper", "event"}


# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldRule:
    name: str
    required: bool = True
    severity: str = "HIGH"  # HIGH | MEDIUM | LOW
    # Optional invariant: returns None on success, error string on failure.
    validator: Callable[[Any, Path], str | None] | None = None
    # Allowed values; if set, value must be in this set (or be in the set
    # for list-valued fields).  ``None`` means any value.
    allowed: set[str] | None = None


@dataclass(frozen=True, slots=True)
class CrossFieldRule:
    """A rule that runs once per file against the whole frontmatter.

    Unlike ``FieldRule``, the validator sees the complete dict and can
    enforce either-or constraints (e.g. "source OR github required").
    """
    name: str
    severity: str = "HIGH"
    validator: Callable[[dict[str, Any], Path], str | None] = lambda _fm, _p: None


@dataclass(frozen=True, slots=True)
class LayerSchema:
    name: str
    glob_patterns: tuple[str, ...]
    rules: tuple[FieldRule, ...]
    # Files matching these glob patterns under the same root are excluded
    # (e.g. ``_Candidates/*`` excluded from L4 active scan).
    exclude_globs: tuple[str, ...] = ()
    # Cross-field rules: validators that get the full frontmatter dict.
    cross_field_rules: tuple[CrossFieldRule, ...] = ()


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def _validate_note_id_matches_stem(value: Any, file_path: Path) -> str | None:
    if not isinstance(value, str) or not value:
        return "note_id missing or empty"
    if value != file_path.stem:
        return f"note_id={value!r} does not match filename stem {file_path.stem!r}"
    return None


def _validate_tags_is_list(value: Any, _file: Path) -> str | None:
    if not isinstance(value, list):
        return f"tags must be a list, got {type(value).__name__}"
    return None


def _validate_aliases_is_list(value: Any, _file: Path) -> str | None:
    if not isinstance(value, list):
        return f"aliases must be a list, got {type(value).__name__}"
    return None


def _validate_entity_type_is_concept(value: Any, _file: Path) -> str | None:
    if value != EVERGREEN_ENTITY_TYPE:
        return f"evergreen entity_type must be {EVERGREEN_ENTITY_TYPE!r}, got {value!r}"
    return None


def _validate_entity_type_in_layer_types(value: Any, _file: Path) -> str | None:
    if value not in ENTITY_LAYER_TYPES:
        return f"entity_type {value!r} not in {sorted(ENTITY_LAYER_TYPES)}"
    return None


def _validate_evergreen_tag_present(value: Any, _file: Path) -> str | None:
    # Don't silently pass when tags isn't a list — that was the bug
    # CodeRabbit flagged: a malformed scalar value would bypass the
    # membership check entirely.
    if not isinstance(value, list):
        return f"tags must be a list, got {type(value).__name__}"
    if "evergreen" not in value:
        return f"tags must include 'evergreen', got {value}"
    return None


def _validate_entity_tag_present(value: Any, _file: Path) -> str | None:
    if not isinstance(value, list):
        return f"tags must be a list, got {type(value).__name__}"
    if "entity" not in value:
        return f"tags must include 'entity', got {value}"
    return None


def _validate_candidate_tag_present(value: Any, _file: Path) -> str | None:
    if not isinstance(value, list):
        return f"tags must be a list, got {type(value).__name__}"
    if "candidate" not in value:
        return f"tags must include 'candidate', got {value}"
    return None


# ---------------------------------------------------------------------------
# Layer schemas
# ---------------------------------------------------------------------------


SOURCE_SCHEMA = LayerSchema(
    name="L1 Source",
    glob_patterns=("50-Inbox/03-Processed/**/*.md",),
    rules=(
        FieldRule("title", required=True, severity="HIGH"),
        FieldRule("source", required=False, severity="MEDIUM"),
        FieldRule("date", required=False, severity="LOW"),
        FieldRule("tags", required=False, severity="LOW", validator=_validate_tags_is_list),
    ),
)

def _validate_l2_url_origin(fm: dict[str, Any], _file: Path) -> str | None:
    """L2 deep dives must have at least one URL-origin field set.

    Accepts ``source``, ``github``, or ``source_url`` (the canonical KG
    field name some generators emit directly).
    """
    if fm.get("source") or fm.get("github") or fm.get("source_url"):
        return None
    return "none of 'source' / 'github' / 'source_url' set; one is required"


# L2 actually has two subtypes:
#   - ``type: article``       — requires ``source: <URL>``
#   - ``type: project`` / ``github-project`` — requires ``github: <URL>``
# We model both with the same schema and let a custom validator enforce
# the either-or, since FieldRule can only see one field at a time.
L2_ALLOWED_TYPES = {
    "article",
    "project",
    "github-project",  # legacy synonym for project; normalized in audit reports
}


DEEP_DIVE_SCHEMA = LayerSchema(
    name="L2 Deep Dive",
    glob_patterns=("20-Areas/**/Topics/**/*_深度解读.md",),
    rules=(
        FieldRule("title", required=True, severity="HIGH"),
        # ``source`` and ``github`` are individually optional; the
        # cross-field rule below enforces "one of them must be set".
        FieldRule("source", required=False, severity="MEDIUM"),
        FieldRule("github", required=False, severity="MEDIUM"),
        FieldRule("date", required=True, severity="MEDIUM"),
        FieldRule("type", required=True, severity="MEDIUM",
                  allowed=L2_ALLOWED_TYPES),
        FieldRule("tags", required=True, severity="MEDIUM",
                  validator=_validate_tags_is_list),
        FieldRule("status", required=False, severity="LOW"),
        FieldRule("author", required=False, severity="LOW"),
        # B1 from Phase B: deep dives lack note_id.  Flagged MEDIUM (not
        # HIGH) because the KG auto-derives it from path; tests can assert
        # this either way, depending on whether we choose to backfill.
        FieldRule("note_id", required=False, severity="MEDIUM"),
    ),
    cross_field_rules=(
        CrossFieldRule(
            name="source_or_github",
            severity="HIGH",
            validator=_validate_l2_url_origin,
        ),
    ),
)

EVERGREEN_SCHEMA = LayerSchema(
    name="L3 Evergreen",
    glob_patterns=("10-Knowledge/Evergreen/*.md",),
    exclude_globs=("10-Knowledge/Evergreen/_Candidates/**/*.md",),
    rules=(
        FieldRule("note_id", required=True, severity="HIGH",
                  validator=_validate_note_id_matches_stem),
        FieldRule("title", required=True, severity="HIGH"),
        FieldRule("type", required=True, severity="HIGH", allowed={"evergreen"}),
        FieldRule("entity_type", required=True, severity="MEDIUM",
                  validator=_validate_entity_type_is_concept),
        FieldRule("date", required=True, severity="MEDIUM"),
        FieldRule("tags", required=True, severity="MEDIUM",
                  validator=_validate_evergreen_tag_present),
        FieldRule("aliases", required=True, severity="LOW",
                  validator=_validate_aliases_is_list),
        FieldRule("area", required=False, severity="LOW"),
    ),
)

ENTITY_ACTIVE_SCHEMA = LayerSchema(
    name="L4 Entity (active)",
    glob_patterns=("10-Knowledge/Entity/*.md",),
    exclude_globs=("10-Knowledge/Entity/_Candidates/**/*.md",),
    rules=(
        FieldRule("note_id", required=True, severity="HIGH",
                  validator=_validate_note_id_matches_stem),
        FieldRule("title", required=True, severity="HIGH"),
        FieldRule("type", required=True, severity="HIGH", allowed={"entity"}),
        FieldRule("entity_type", required=True, severity="HIGH",
                  validator=_validate_entity_type_in_layer_types),
        FieldRule("date", required=True, severity="MEDIUM"),
        FieldRule("tags", required=True, severity="MEDIUM",
                  validator=_validate_entity_tag_present),
        FieldRule("aliases", required=False, severity="LOW",
                  validator=_validate_aliases_is_list),
    ),
)

ENTITY_CANDIDATE_SCHEMA = LayerSchema(
    name="L5 Entity (candidate)",
    glob_patterns=("10-Knowledge/Entity/_Candidates/*.md",),
    rules=(
        FieldRule("note_id", required=True, severity="HIGH",
                  validator=_validate_note_id_matches_stem),
        FieldRule("title", required=True, severity="HIGH"),
        FieldRule("type", required=True, severity="HIGH", allowed={"entity"}),
        FieldRule("entity_type", required=True, severity="HIGH",
                  validator=_validate_entity_type_in_layer_types),
        FieldRule("status", required=True, severity="HIGH", allowed={"candidate"}),
        FieldRule("date", required=True, severity="MEDIUM"),
        FieldRule("tags", required=True, severity="MEDIUM",
                  validator=_validate_candidate_tag_present),
    ),
)


ALL_LAYERS: tuple[LayerSchema, ...] = (
    SOURCE_SCHEMA,
    DEEP_DIVE_SCHEMA,
    EVERGREEN_SCHEMA,
    ENTITY_ACTIVE_SCHEMA,
    ENTITY_CANDIDATE_SCHEMA,
)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Violation:
    layer: str
    file: Path
    rule: str
    severity: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "file": str(self.file),
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
        }


# ``(?:\n|\Z)`` lets the closing ``---`` be either followed by a newline
# or be the very last bytes of the file — without ``\Z`` we false-positive
# on legitimate markdown files that end exactly at the closing delimiter.
#
# Public so the repair scripts can share the exact same pattern; do NOT
# re-roll this regex anywhere else in the codebase.  ``parse_frontmatter``
# is the high-level entry point; for callers that need to keep the full
# delimiter-bracketed match (e.g. to splice repaired body back together),
# use this regex directly.
FRONTMATTER_BLOCK_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_FRONTMATTER_RE = FRONTMATTER_BLOCK_RE  # legacy private alias


def parse_frontmatter(text: str) -> dict[str, Any] | None:
    """Return the YAML frontmatter dict, or ``None`` if none / malformed.

    Single source of truth for frontmatter extraction across the
    auditor, the repair commands, and the layer schemas tests.  Other
    modules MUST import this rather than re-roll the regex (see PR #111
    review for the duplication that motivated the consolidation).
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _files_for_layer(vault_dir: Path, schema: LayerSchema) -> list[Path]:
    """Single-pass file iteration with inline exclusion check.

    Compiles each ``exclude_globs`` pattern into a regex via
    ``Path.match`` semantics so we don't have to separately glob the
    excluded subtree (the previous double-scan approach was wasteful on
    large vaults — see PR #111 review).
    """
    seen: set[Path] = set()
    files: list[Path] = []
    for pat in schema.glob_patterns:
        for p in vault_dir.glob(pat):
            if not p.is_file():
                continue
            resolved = p.resolve()
            if resolved in seen:
                continue
            # Check exclusion via Path.match so we never have to walk
            # the excluded glob ourselves.
            try:
                rel = p.relative_to(vault_dir)
            except ValueError:
                rel = p
            if any(rel.match(ex) for ex in schema.exclude_globs):
                continue
            seen.add(resolved)
            files.append(p)
    return sorted(files)


def _validate_one_file(
    schema: LayerSchema, file: Path, fm: dict[str, Any] | None,
) -> list[Violation]:
    out: list[Violation] = []
    if fm is None:
        out.append(Violation(
            layer=schema.name, file=file, rule="frontmatter",
            severity="HIGH",
            message="missing or malformed YAML frontmatter",
        ))
        return out

    for rule in schema.rules:
        present = rule.name in fm and fm[rule.name] not in (None, "", [])
        if not present:
            if rule.required:
                out.append(Violation(
                    layer=schema.name, file=file, rule=rule.name,
                    severity=rule.severity,
                    message=f"required field {rule.name!r} missing or empty",
                ))
            continue

        value = fm[rule.name]
        if rule.allowed is not None:
            if isinstance(value, list):
                bad = [v for v in value if v not in rule.allowed]
                if bad:
                    out.append(Violation(
                        layer=schema.name, file=file, rule=rule.name,
                        severity=rule.severity,
                        message=f"{rule.name} contains values outside allowed set: {bad}",
                    ))
            elif value not in rule.allowed:
                out.append(Violation(
                    layer=schema.name, file=file, rule=rule.name,
                    severity=rule.severity,
                    message=f"{rule.name}={value!r} not in allowed {sorted(rule.allowed)}",
                ))
        if rule.validator is not None:
            err = rule.validator(value, file)
            if err:
                out.append(Violation(
                    layer=schema.name, file=file, rule=rule.name,
                    severity=rule.severity,
                    message=err,
                ))

    for cross in schema.cross_field_rules:
        err = cross.validator(fm, file)
        if err:
            out.append(Violation(
                layer=schema.name, file=file, rule=cross.name,
                severity=cross.severity, message=err,
            ))

    return out


def audit_layer(
    vault_dir: Path, schema: LayerSchema,
    *,
    sample_size: int | None = None,
    violation_limit: int | None = None,
    severity_floor: str | None = None,
) -> tuple[list[Violation], int]:
    """Scan every file in ``schema``'s glob and validate against rules.

    Parameters
    ----------
    sample_size : optional
        If set, only scan the first N files (useful for fast unit tests).
    violation_limit : optional
        Stop scanning as soon as this many violations have been collected
        (early-exit optimization for callers that only need a peek).
    severity_floor : optional
        If set (``"HIGH"`` / ``"MEDIUM"`` / ``"LOW"``), only count
        violations at or above this level toward ``violation_limit``.
        Other severities are still emitted but don't trigger the early
        exit.

    Returns
    -------
    (violations, files_scanned)
    """
    files = _files_for_layer(vault_dir, schema)
    if sample_size is not None:
        files = files[:sample_size]

    severity_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    floor_rank = severity_rank.get(severity_floor) if severity_floor else None

    violations: list[Violation] = []
    counted = 0  # running counter — was previously O(n²) sum() per file
    files_scanned = 0
    for f in files:
        files_scanned += 1
        new_violations: list[Violation]
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            new_violations = [Violation(
                layer=schema.name, file=f, rule="read",
                severity="HIGH",
                message=f"could not read file: {exc}",
            )]
        else:
            fm = parse_frontmatter(text)
            new_violations = _validate_one_file(schema, f, fm)
        violations.extend(new_violations)

        if violation_limit is not None:
            if floor_rank is None:
                counted = len(violations)
            else:
                counted += sum(
                    1 for v in new_violations
                    if severity_rank[v.severity] <= floor_rank
                )
            if counted >= violation_limit:
                break

    return violations, files_scanned


def audit_all_layers(
    vault_dir: Path,
    *,
    sample_size: int | None = None,
    layer_filter: str | None = None,
    violation_limit_per_layer: int | None = None,
    severity_floor: str | None = None,
) -> dict[str, Any]:
    """Audit every layer; return a structured report.

    Layout::

        {
          "vault_dir": "...",
          "layers": [
            {"name": "L1 Source", "files_scanned": 150,
             "violations_by_severity": {"HIGH": 0, "MEDIUM": 3, "LOW": 12},
             "violations": [...],
            },
            ...
          ],
          "total_files": ...,
          "total_violations": {"HIGH": ..., "MEDIUM": ..., "LOW": ...},
        }
    """
    layer_reports: list[dict[str, Any]] = []
    grand_total = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    total_files = 0
    for schema in ALL_LAYERS:
        if layer_filter and schema.name != layer_filter:
            # Skip the whole scan when caller wants a single layer —
            # avoids walking 6500+ Evergreen files just to view L2.
            layer_reports.append({
                "name": schema.name, "files_scanned": 0,
                "violations_by_severity": {"HIGH": 0, "MEDIUM": 0, "LOW": 0},
                "violations": [],
            })
            continue
        violations, n_files = audit_layer(
            vault_dir, schema,
            sample_size=sample_size,
            violation_limit=violation_limit_per_layer,
            severity_floor=severity_floor,
        )
        total_files += n_files
        by_sev = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for v in violations:
            by_sev[v.severity] += 1
            grand_total[v.severity] += 1
        layer_reports.append({
            "name": schema.name,
            "files_scanned": n_files,
            "violations_by_severity": by_sev,
            "violations": [v.to_dict() for v in violations],
        })
    return {
        "vault_dir": str(vault_dir),
        "layers": layer_reports,
        "total_files": total_files,
        "total_violations": grand_total,
    }


def summarize_report(report: dict[str, Any]) -> str:
    """Compact terminal summary of an audit report."""
    lines: list[str] = []
    lines.append(f"Vault: {report['vault_dir']}")
    lines.append(f"Files scanned: {report['total_files']}")
    lines.append(f"Total violations: HIGH={report['total_violations']['HIGH']}, "
                 f"MEDIUM={report['total_violations']['MEDIUM']}, "
                 f"LOW={report['total_violations']['LOW']}")
    lines.append("")
    for layer in report["layers"]:
        bs = layer["violations_by_severity"]
        lines.append(f"  {layer['name']:30s}  files={layer['files_scanned']:5d}  "
                     f"HIGH={bs['HIGH']:4d}  MED={bs['MEDIUM']:4d}  LOW={bs['LOW']:4d}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Aggregation helpers (used by tests + report sections)
# ---------------------------------------------------------------------------


def violations_by_rule(violations: Iterable[Violation]) -> dict[str, int]:
    """Group violation count by rule name (e.g. ``note_id`` → 12)."""
    counts: dict[str, int] = {}
    for v in violations:
        counts[v.rule] = counts.get(v.rule, 0) + 1
    return counts
