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
import sys
from datetime import datetime
from pathlib import Path

from .concept_registry import (
    ConceptRegistry,
    ConceptEntry,
    STATUS_ACTIVE,
    STATUS_CANDIDATE,
    normalize_surface,
)
from .runtime import iter_markdown_files, resolve_vault_dir


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
    evergreen_dir = resolve_vault_dir(vault_dir) / EVERGREEN_DIR
    if not evergreen_dir.exists():
        print(f"Warning: Evergreen directory not found: {evergreen_dir}")
        return []

    files = []
    for md_file in iter_markdown_files(evergreen_dir, recursive=True):
        # Skip _Candidates directory
        if "_Candidates" in md_file.parts:
            continue
        files.append(md_file)

    return files


def scan_candidate_files(vault_dir: Path) -> list[Path]:
    """Find all candidate markdown files under Evergreen/_Candidates."""
    candidates_dir = resolve_vault_dir(vault_dir) / CANDIDATES_DIR
    if not candidates_dir.exists():
        return []
    return sorted(candidates_dir.glob("*.md"))


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


def candidate_file_slugs(vault_dir: Path) -> set[str]:
    """Return candidate slugs that have backing files on disk."""
    slugs: set[str] = set()
    for path in scan_candidate_files(vault_dir):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            slugs.add(path.stem)
            continue
        fm, _body = parse_frontmatter(content)
        note_id = str(fm.get("note_id", "")).strip()
        title = str(fm.get("title", "")).strip()
        if note_id:
            slugs.add(note_id)
        elif title:
            slugs.add(normalize_surface(title).replace(" ", "-"))
        else:
            slugs.add(path.stem)
    return slugs


def rebuild_registry(vault_dir: Path, dry_run: bool = False, verbose: bool = False) -> list[ConceptEntry]:
    """Rebuild registry from existing Evergreen files."""
    vault_dir = resolve_vault_dir(vault_dir)
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
        print(f"Written to {vault_dir / '10-Knowledge' / 'Atlas' / 'concept-registry.jsonl'}")

    return entries


def reconcile_registry(vault_dir: Path, write: bool = False, verbose: bool = False) -> dict:
    """
    Compare Evergreen files against the registry and optionally add missing entries.

    This is the authoritative implementation used by the CLI.
    """
    vault_dir = resolve_vault_dir(vault_dir)
    entries = rebuild_registry(vault_dir, dry_run=True, verbose=verbose)
    built_slugs = {entry.slug for entry in entries}
    built_map = {entry.slug: entry for entry in entries}
    candidate_slugs = candidate_file_slugs(vault_dir)

    registry = ConceptRegistry(vault_dir).load()
    registry_slugs = {entry.slug for entry in registry.entries}

    result = {
        "dry_run": not write,
        "write": write,
        "fs_file_count": len(entries),
        "registry_entry_count": len(registry.entries),
        "fs_slugs": sorted(built_slugs),
        "not_in_registry": [],
        "not_in_filesystem": [],
        "in_sync": sorted(built_slugs & registry_slugs),
        "orphan_registry_entries": [],
    }

    for slug in sorted(built_slugs - registry_slugs):
        entry = built_map[slug]
        result["not_in_registry"].append({
            "slug": slug,
            "title": entry.title,
            "area": entry.area,
        })

    for entry in sorted(registry.entries, key=lambda item: item.slug):
        if entry.slug not in built_slugs and not (
            entry.status == STATUS_CANDIDATE and entry.slug in candidate_slugs
        ):
            payload = {
                "slug": entry.slug,
                "title": entry.title,
                "status": entry.status,
                "kind": getattr(entry, "kind", "unknown"),
            }
            result["not_in_filesystem"].append(payload)
            if entry.status == STATUS_ACTIVE:
                result["orphan_registry_entries"].append(payload)

    if write:
        retained_entries = [
            entry
            for entry in registry.entries
            if entry.slug not in built_slugs and (
                entry.status != STATUS_CANDIDATE or entry.slug in candidate_slugs
            )
        ]
        for slug in sorted(built_slugs):
            existing = next((entry for entry in retained_entries if entry.slug == slug), None)
            if existing is not None:
                retained_entries.remove(existing)
            retained_entries.append(built_map[slug])
        registry._entries = retained_entries
        registry._registry_entries = [entry.to_registry_entry() for entry in retained_entries]
        registry._build_surface_index()
        registry._token_cache = {}
        registry.save()
        retained_slugs = {entry.slug for entry in retained_entries}
        result["registry_entry_count"] = len(retained_entries)
        result["not_in_registry"] = []
        result["not_in_filesystem"] = []
        result["orphan_registry_entries"] = []
        result["in_sync"] = sorted(built_slugs & retained_slugs)

    return result


def print_report(result: dict) -> None:
    """Print a human-readable reconciliation report."""
    print("\n" + "=" * 60)
    print("Registry Rebuild Report")
    print("=" * 60)

    print(f"\nFilesystem Evergreen files: {result['fs_file_count']}")
    print(f"Registry entries: {result.get('registry_entry_count', 'N/A')}")

    sync_count = len(result["in_sync"])
    not_in_reg = len(result["not_in_registry"])
    not_in_fs = len(result["not_in_filesystem"])

    print("\nSync status:")
    print(f"  ✅ In sync: {sync_count}")
    print(f"  ⚠️  Not in registry: {not_in_reg}")
    print(f"  ⚠️  Not in filesystem: {not_in_fs}")

    if not not_in_reg and not not_in_fs:
        print("\n✅ Registry and filesystem are fully synchronized!")
    else:
        pct_in_sync = sync_count / max(sync_count + not_in_reg, 1) * 100
        print(f"\n📊 Sync rate: {pct_in_sync:.1f}%")

    if result["dry_run"] and (not_in_reg > 0 or not_in_fs > 0):
        print("\n💡 Run with --write to apply fixes")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild concept registry from Evergreen files")
    parser.add_argument("--vault-dir", type=Path, default=None)
    parser.add_argument("--write", action="store_true", help="Write to disk (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    result = reconcile_registry(
        vault_dir=vault_dir,
        write=args.write,
        verbose=args.verbose,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)

    if result["not_in_registry"] or result["not_in_filesystem"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
