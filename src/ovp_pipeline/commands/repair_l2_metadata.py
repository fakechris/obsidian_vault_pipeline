"""ovp-repair-l2-metadata — Normalize MEDIUM-severity drift in L2 deep dives.

Two fixes for the MEDIUM-class violations the audit reports:

1. **Normalize ``type`` to the canonical set** ``{article, project,
   github-project}``.  LLM generators have emitted 30+ one-off variants
   (``ai``, ``technical-analysis``, ``technical-tutorial``,
   ``engineering-blog``, ``技术架构分析``, ``programming`` …).  All map
   to ``article`` since they describe the same conceptual layer
   (long-form deep dive).  ``tools`` is the lone ambiguous one — we
   treat it as ``article`` because the file name pattern is the same
   (``YYYY-MM-DD_<title>_深度解读.md``) and these were almost always
   articles ABOUT a tool, not GitHub project deep dives (which have
   the ``owner_repo`` filename pattern).

2. **Backfill missing/empty ``tags``** from the deep dive's topic
   directory: ``20-Areas/AI-Research/Topics/...`` → ``[ai-research]``
   etc.  Single-tag default; more specific classification stays the
   generator's job going forward.

Idempotent: files that already have canonical type or non-empty tags
are untouched.  Default is ``--dry-run``; pass ``--write`` to apply.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# Map non-canonical type values → canonical.  All long-form deep dives
# collapse to ``article``; project + github-project are the only other
# accepted values (see L2_ALLOWED_TYPES in layer_schemas.py).
TYPE_NORMALIZATION = {
    # English synonyms for "article"
    "ai": "article",
    "ai-agent": "article",
    "ai-agents": "article",
    "ai-engineering": "article",
    "ai-commentary": "article",
    "ai-infrastructure": "article",
    "ai-model": "article",
    "ai-workflow-guide": "article",
    "tool-analysis": "article",
    "article-analysis": "article",
    "career-transformation": "article",
    "engineering-blog": "article",
    "essay": "article",
    "interview-insights": "article",
    "product-analysis": "article",
    "programming": "article",
    "research-analysis": "article",
    "research-paper-analysis": "article",
    "technical-analysis": "article",
    "technical-article": "article",
    "technical-guide": "article",
    "technical-tutorial": "article",
    "tool-review": "article",
    "tools": "article",
    "tutorial": "article",
    # Chinese synonyms
    "技术实践解读": "article",
    "技术架构分析": "article",
    "行业分析": "article",
    "操作指南": "article",
    "产品发布/技术解读": "article",
}


# Topic directory → default tag.  Path is e.g.
# ``20-Areas/AI-Research/Topics/2026-04/foo_深度解读.md``; we read the
# segment after ``20-Areas/`` and lower-case it.
def _topic_tag_from_path(path: Path, vault_dir: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(vault_dir.resolve())
    except ValueError:
        return None
    parts = rel.parts
    # ``20-Areas/<topic>/Topics/...``
    if len(parts) >= 2 and parts[0] == "20-Areas":
        topic = parts[1].lower().replace(" ", "-")
        return topic
    return None


# See repair_yaml_titles.py: same pattern, split into capture groups for
# splice-rewriting; tolerates EOF after closing ``---``.
_FRONTMATTER_RE = re.compile(r"\A(---\s*\n)(.*?)(\n---\s*(?:\n|\Z))", re.DOTALL)
# Match a "key: value" line we can rewrite (top-level scalar).
_KEY_VALUE_RE = re.compile(r"^([a-zA-Z_][\w\-]*):\s*(.*?)\s*$")


@dataclass
class RepairReport:
    files_scanned: int = 0
    type_normalized: int = 0
    tags_backfilled: int = 0
    files_changed: int = 0
    skipped_no_fm: int = 0
    skipped_unparseable: int = 0
    examples: list[tuple[Path, list[str]]] = field(default_factory=list)


def _rewrite_type_line(block: str) -> tuple[str, str | None]:
    """If ``type`` line has a non-canonical value, rewrite it.

    Returns (new_block, replaced_value_or_None).
    """
    out_lines: list[str] = []
    replaced: str | None = None
    for line in block.split("\n"):
        m = _KEY_VALUE_RE.match(line)
        if m and m.group(1) == "type":
            current = m.group(2).strip().strip("'\"")
            canonical = TYPE_NORMALIZATION.get(current)
            if canonical and canonical != current:
                out_lines.append(f"type: {canonical}")
                replaced = current
                continue
        out_lines.append(line)
    return "\n".join(out_lines), replaced


def _ensure_tags(block: str, default_tag: str | None) -> tuple[str, bool]:
    """If ``tags`` is missing or empty, append a default-tag line.

    Returns (new_block, was_added).
    """
    if not default_tag:
        return block, False
    try:
        fm = yaml.safe_load(block) or {}
    except yaml.YAMLError:
        return block, False
    tags = fm.get("tags")
    if tags:  # already non-empty
        return block, False

    # Replace existing empty-tags line if present, otherwise append.
    new_lines: list[str] = []
    saw_tags = False
    for line in block.split("\n"):
        m = _KEY_VALUE_RE.match(line)
        if m and m.group(1) == "tags":
            new_lines.append(f"tags: [{default_tag}]")
            saw_tags = True
        else:
            new_lines.append(line)
    if not saw_tags:
        new_lines.append(f"tags: [{default_tag}]")
    return "\n".join(new_lines), True


def repair_one(file_path: Path, vault_dir: Path) -> tuple[str | None, list[str]]:
    """Return (new_content_or_None, change_log).

    ``new_content_or_None`` is None when no changes were needed.
    """
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, []

    m = _FRONTMATTER_RE.match(content)
    if m is None:
        return None, []
    opener, body, closer = m.group(1), m.group(2), m.group(3)

    changes: list[str] = []

    # 1) Normalize type
    new_body, replaced_type = _rewrite_type_line(body)
    if replaced_type:
        changes.append(f"type: {replaced_type!r} → article")
        body = new_body

    # 2) Backfill tags
    default_tag = _topic_tag_from_path(file_path, vault_dir)
    new_body2, tags_added = _ensure_tags(body, default_tag)
    if tags_added:
        changes.append(f"tags: backfilled to [{default_tag}]")
        body = new_body2

    if not changes:
        return None, []

    new_content = opener + body + closer + content[m.end():]
    return new_content, changes


def repair(
    vault_dir: Path, *, write: bool = False,
) -> RepairReport:
    report = RepairReport()
    for f in vault_dir.glob("20-Areas/**/Topics/**/*_深度解读.md"):
        if not f.is_file():
            continue
        report.files_scanned += 1
        new_content, changes = repair_one(f, vault_dir)
        if not changes:
            continue
        report.files_changed += 1
        if any("type:" in c for c in changes):
            report.type_normalized += 1
        if any("tags:" in c for c in changes):
            report.tags_backfilled += 1
        if len(report.examples) < 30:
            report.examples.append((f, changes))
        if write and new_content is not None:
            f.write_text(new_content, encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Normalize L2 deep dive type + backfill missing tags",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument("--write", action="store_true",
                        help="Apply repairs in place (default: dry-run)")
    parser.add_argument("--show", type=int, default=20,
                        help="Show first N example changes")
    args = parser.parse_args(argv)

    vault = args.vault_dir.resolve()
    if not vault.is_dir():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2

    report = repair(vault, write=args.write)
    print(f"Scanned: {report.files_scanned}")
    print(f"Files changed: {report.files_changed}")
    print(f"  type normalized: {report.type_normalized}")
    print(f"  tags backfilled: {report.tags_backfilled}")

    if report.examples:
        print(f"\nFirst {min(args.show, len(report.examples))} examples:")
        for path, changes in report.examples[:args.show]:
            try:
                rel = path.relative_to(vault)
            except ValueError:
                rel = path
            print(f"  {rel}")
            for c in changes:
                print(f"    - {c}")

    if not args.write:
        print("\n[dry-run] no files written. Pass --write to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
