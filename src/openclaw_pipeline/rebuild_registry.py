#!/usr/bin/env python3
"""
Rebuild Registry - Initialize registry from existing Evergreen files.

Scans 10-Knowledge/Evergreen/*.md and builds concept-registry.jsonl.

Usage:
    python -m openclaw_pipeline.rebuild_registry --vault-dir . --write
    python -m openclaw_pipeline.rebuild_registry --vault-dir . --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from .concept_registry import ConceptRegistry, ConceptEntry, STATUS_ACTIVE


EVERGREEN_DIR = Path("10-Knowledge/Evergreen")
CANDIDATES_DIR = Path("10-Knowledge/Evergreen/_Candidates")


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown content."""
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1].strip()
            body = parts[2].strip()
            fm = {}
            for line in fm_text.split("\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    fm[key.strip()] = value.strip().strip('"').strip("'")
            return fm, body
    return {}, content


def extract_h1(body: str) -> str | None:
    """Extract first H1 heading from body."""
    match = re.search(r'^#\s+(.+)$', body, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


def extract_definition(body: str, max_chars: int = 300) -> str:
    """Extract first substantive paragraph as definition."""
    lines = body.split("\n")
    for line in lines:
        line = line.strip()
        # Skip empty lines, headings, code blocks
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        # Skip blockquotes (often metadata)
        if line.startswith(">"):
            continue
        # Found a content paragraph
        if len(line) > 20:
            return line[:max_chars]
    return ""


def infer_area(file_path: Path, fm: dict, body: str) -> str:
    """Infer area from file path or content."""
    # Try path: 10-Knowledge/Evergreen/{area}/...
    parts = file_path.parts
    if len(parts) >= 3:
        area_candidate = parts[-2] if parts[-2] != "Evergreen" else None
        if area_candidate:
            return area_candidate

    # Try frontmatter tags
    tags = fm.get("tags", "")
    if isinstance(tags, list):
        for tag in tags:
            if tag in ("investing", "ai", "programming", "tools", "finance"):
                return tag

    # Try content keywords
    body_lower = body.lower()
    if any(k in body_lower for k in ["dcf", "wacc", "valuation", "equity", "bond"]):
        return "investing"
    if any(k in body_lower for k in ["neural", "model", "training", "ai", "llm"]):
        return "ai"
    if any(k in body_lower for k in ["python", "rust", "code", "api", "function"]):
        return "programming"
    if any(k in body_lower for k in ["tool", "cli", "editor", "editor"]):
        return "tools"

    return "general"


def extract_aliases(fm: dict, body: str) -> list[str]:
    """Extract aliases from frontmatter or body."""
    aliases = []

    # Frontmatter aliases
    fm_aliases = fm.get("aliases", [])
    if isinstance(fm_aliases, list):
        aliases.extend(fm_aliases)
    elif isinstance(fm_aliases, str) and fm_aliases:
        # Parse YAML list format: [item1, item2, item3]
        fm_aliases = fm_aliases.strip()
        if fm_aliases.startswith('[') and fm_aliases.endswith(']'):
            # Strip brackets and split
            inner = fm_aliases[1:-1]
            for item in inner.split(','):
                item = item.strip().strip('"').strip("'")
                if item and item not in aliases:
                    aliases.append(item)
        else:
            aliases.append(fm_aliases)

    # Also check body for alias definitions
    # Pattern: aliases: [alias1, alias2] or aliases: alias1, alias2
    alias_pattern = r'aliases[:\s]+[\["\']*([^\]"\']+)[\]"\']*\]?'
    for match in re.finditer(alias_pattern, body, re.IGNORECASE):
        alias_str = match.group(1)
        # Split by comma
        for a in alias_str.split(","):
            a = a.strip().strip('"\'')
            if a and a not in aliases:
                aliases.append(a)

    return aliases


def scan_evergreen_files(vault_dir: Path) -> list[Path]:
    """Find all Evergreen .md files (excluding _Candidates/)."""
    evergreen_dir = vault_dir / EVERGREEN_DIR
    if not evergreen_dir.exists():
        print(f"Warning: Evergreen directory not found: {evergreen_dir}")
        return []

    files = []
    for md_file in evergreen_dir.rglob("*.md"):
        # Skip _Candidates directory
        if "_Candidates" in md_file.parts:
            continue
        files.append(md_file)

    return files


def file_to_entry(vault_dir: Path, file_path: Path) -> ConceptEntry | None:
    """Convert an Evergreen file to a registry entry."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  Warning: Could not read {file_path}: {e}")
        return None

    fm, body = parse_frontmatter(content)

    # Slug is filename stem
    slug = file_path.stem

    # Title from frontmatter or H1
    title = fm.get("title", "")
    if not title:
        title = extract_h1(body) or slug

    # Aliases
    aliases = extract_aliases(fm, body)

    # Definition
    definition = fm.get("definition", "")
    if not definition:
        definition = extract_definition(body)

    # Area
    area = fm.get("area", "")
    if not area:
        area = infer_area(file_path, fm, body)

    return ConceptEntry(
        slug=slug,
        title=title,
        aliases=aliases,
        definition=definition,
        area=area,
        status=STATUS_ACTIVE,
        source_count=0,
        evidence_count=0,
        last_seen_at=datetime.now().strftime("%Y-%m-%d"),
        review_state="seeded_from_existing",
    )


def rebuild_registry(vault_dir: Path, dry_run: bool = False, verbose: bool = False) -> list[ConceptEntry]:
    """Rebuild registry from existing Evergreen files."""
    files = scan_evergreen_files(vault_dir)
    print(f"Found {len(files)} Evergreen files")

    entries = []
    skipped = 0

    for file_path in sorted(files):
        entry = file_to_entry(vault_dir, file_path)
        if entry:
            entries.append(entry)
            if verbose:
                print(f"  + {entry.slug}")
                if entry.aliases:
                    print(f"      aliases: {entry.aliases}")
        else:
            skipped += 1

    if skipped:
        print(f"Skipped {skipped} files")

    print(f"Built {len(entries)} registry entries")

    if not dry_run:
        registry = ConceptRegistry(vault_dir)
        for entry in entries:
            registry.upsert_entry(entry)
        registry.save()
        print(f"Written to {vault_dir / '10-Knowledge/Atlas / concept-registry.jsonl'}")

    return entries


def main():
    parser = argparse.ArgumentParser(description="Rebuild concept registry from Evergreen files")
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument("--write", action="store_true", help="Write to disk (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    dry_run = not args.write
    if dry_run:
        print("DRY RUN - no files will be written")

    entries = rebuild_registry(
        vault_dir=args.vault_dir,
        dry_run=dry_run,
        verbose=args.verbose,
    )

    # Summary
    print()
    print(f"Summary: {len(entries)} entries")

    by_area: dict[str, int] = {}
    for e in entries:
        by_area[e.area] = by_area.get(e.area, 0) + 1
    for area, count in sorted(by_area.items()):
        print(f"  {area}: {count}")


if __name__ == "__main__":
    main()
