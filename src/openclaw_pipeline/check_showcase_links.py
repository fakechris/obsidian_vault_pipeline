#!/usr/bin/env python3
"""
Showcase Link Checker - 链接稳定性检查器
检查所有 wikilink 是否指向存在的文件，并接入 registry 进行语义检查

Uses concept registry to distinguish:
- ok: link to active concept
- alias_warning: link targets an alias (should use canonical slug)
- candidate_warning: link targets a candidate concept
- broken: link target doesn't exist
"""

from __future__ import annotations

import os
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

try:
    from .runtime import iter_markdown_files, resolve_vault_dir
except ImportError:
    from runtime import iter_markdown_files, resolve_vault_dir  # type: ignore

# Try to import concept registry
try:
    from .concept_registry import ConceptRegistry
    HAS_REGISTRY = True
except ImportError:
    HAS_REGISTRY = False


@dataclass
class LinkCheckResult:
    """Result of checking a single link."""
    file_path: str
    surface: str
    status: str  # "ok", "alias_warning", "candidate_warning", "broken"
    canonical_slug: str = ""
    suggestion: str = ""


class WikilinkExtractor:
    """Extract wikilinks from markdown content."""

    # Pattern: [[target]] or [[target|display]]
    WIKILINK_PATTERN = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]')

    @classmethod
    def extract_all(cls, content: str) -> list[tuple[str, str]]:
        """Extract all wikilinks. Returns list of (surface, display) tuples."""
        results = []
        for match in cls.WIKILINK_PATTERN.finditer(content):
            surface = match.group(1).strip()
            # Check for display
            full_match = match.group(0)
            if '|' in full_match:
                display = full_match.split('|')[1].split(']]')[0].strip()
            else:
                display = surface
            results.append((surface, display))
        return results


class RegistryLinkChecker:
    """Check links using the concept registry."""

    def __init__(self, vault_dir: Path):
        self.vault_dir = vault_dir
        self.registry = None
        self._all_files: set[str] = set()
        self._build_file_index()

        if HAS_REGISTRY:
            try:
                self.registry = ConceptRegistry(vault_dir).load()
            except Exception as e:
                print(f"Warning: could not load registry: {e}")

    def _build_file_index(self) -> None:
        """Build index of all markdown files."""
        self._all_files = set()
        for md_file in self.vault_dir.rglob("*.md"):
            rel = md_file.relative_to(self.vault_dir)
            self._all_files.add(rel.as_posix())
            self._all_files.add(rel.stem)
            for parent in rel.parents:
                self._all_files.add(parent.as_posix())

    def check_link(self, surface: str) -> LinkCheckResult:
        """Check a single link surface form."""
        # Check registry (active concept)
        if self.registry:
            entry = self.registry.find_by_slug(surface)
            if entry and entry.status == "active":
                return LinkCheckResult(
                    file_path="",
                    surface=surface,
                    status="ok",
                    canonical_slug=entry.slug,
                    suggestion=""
                )

            # Check alias
            entry = self.registry.find_by_alias(surface)
            if entry and entry.status == "active":
                return LinkCheckResult(
                    file_path="",
                    surface=surface,
                    status="alias_warning",
                    canonical_slug=entry.slug,
                    suggestion=f"Use [[{entry.slug}|{surface}]] instead"
                )

            # Check candidate
            entry = self.registry.find_by_slug(surface)
            if entry and entry.status == "candidate":
                return LinkCheckResult(
                    file_path="",
                    surface=surface,
                    status="candidate_warning",
                    canonical_slug=entry.slug,
                    suggestion=f"'{surface}' is a candidate concept, not yet active"
                )

        # Check filesystem
        if surface in self._all_files:
            return LinkCheckResult(
                file_path="",
                surface=surface,
                status="ok",
                canonical_slug="",
                suggestion=""
            )

        # Check common path patterns
        for pattern in [
            f"10-Knowledge/Evergreen/{surface}.md",
            f"10-Knowledge/Evergreen/{surface}/index.md",
            f"20-Areas/{surface}.md",
        ]:
            if pattern in self._all_files or (self.vault_dir / pattern).exists():
                return LinkCheckResult(
                    file_path="",
                    surface=surface,
                    status="ok",
                    canonical_slug="",
                    suggestion=""
                )

        # True broken link
        return LinkCheckResult(
            file_path="",
            surface=surface,
            status="broken",
            canonical_slug="",
            suggestion=self._suggest_fixes(surface)
        )

    def _suggest_fixes(self, surface: str) -> str:
        """Suggest top-3 possible fixes for a broken link."""
        if not self.registry:
            return "No suggestions available"

        suggestions = []

        # Search registry for similar
        results = self.registry.search(surface, topk=3)
        if results:
            suggestions.append(f"Similar concepts: {', '.join(e.slug for e, _ in results)}")

        # Check if it might be an alias
        if self.registry.has_alias(surface):
            entry = self.registry.find_by_alias(surface)
            if entry:
                suggestions.append(f"Alias of '{entry.slug}' - use canonical slug")

        if not suggestions:
            return "No similar concepts found in registry"

        return "; ".join(suggestions[:3])


def check_links(vault_dir: Path):
    """Check all links in the vault."""
    print("=" * 70)
    print("Showcase Link Checker (Registry-Aware)")
    print("=" * 70)
    print(f"Vault: {vault_dir}")
    print()

    if not vault_dir.exists():
        print(f"✗ Error: Vault directory does not exist")
        return 1

    # Initialize checker
    checker = RegistryLinkChecker(vault_dir)
    print(f"✓ Discovered {len(checker._all_files)} files")
    if checker.registry:
        print(f"✓ Registry loaded: {len(checker.registry.entries)} entries")
    print()

    # Statistics
    stats = {
        "ok": 0,
        "alias_warning": 0,
        "candidate_warning": 0,
        "broken": 0,
    }

    broken_links: dict[str, list[str]] = defaultdict(list)
    alias_warnings: dict[str, list[str]] = defaultdict(list)
    candidate_warnings: dict[str, list[str]] = defaultdict(list)

    # Check all files
    for md_file in iter_markdown_files(vault_dir):
        rel_path = md_file.relative_to(vault_dir)
        try:
            content = md_file.read_text(encoding='utf-8')
            wikilinks = WikilinkExtractor.extract_all(content)

            for surface, display in wikilinks:
                result = checker.check_link(surface)
                result.file_path = str(rel_path)

                stats[result.status] = stats.get(result.status, 0) + 1

                if result.status == "broken":
                    broken_links[str(rel_path)].append(surface)
                elif result.status == "alias_warning":
                    alias_warnings[str(rel_path)].append(f"{surface} -> {result.canonical_slug}")
                elif result.status == "candidate_warning":
                    candidate_warnings[str(rel_path)].append(surface)

        except Exception as e:
            print(f"✗ Error reading {rel_path}: {e}")

    # Report
    total = sum(stats.values())
    print(f"Total links checked: {total}")
    print(f"  OK: {stats['ok']}")
    print(f"  Alias warnings: {stats['alias_warning']}")
    print(f"  Candidate warnings: {stats['candidate_warning']}")
    print(f"  Broken: {stats['broken']}")
    print()

    # Detailed reports
    if broken_links:
        print("=" * 70)
        print(f"BROKEN LINKS ({sum(len(v) for v in broken_links.values())} total)")
        print("=" * 70)
        for file, links in sorted(broken_links.items()):
            print(f"\n📄 {file}")
            for link in links:
                result = checker.check_link(link)
                print(f"   ✗ [[{link}]]")
                if result.suggestion:
                    print(f"     → {result.suggestion}")
        print()

    if alias_warnings:
        print("=" * 70)
        print(f"ALIAS WARNINGS ({sum(len(v) for v in alias_warnings.values())} total)")
        print("=" * 70)
        print("(These links work but should use canonical slug)")
        for file, warnings in sorted(alias_warnings.items())[:10]:  # Limit output
            print(f"\n📄 {file}")
            for w in warnings:
                print(f"   ⚠ {w}")
        if len(alias_warnings) > 10:
            print(f"\n... and {len(alias_warnings) - 10} more files")
        print()

    if candidate_warnings:
        print("=" * 70)
        print(f"CANDIDATE WARNINGS ({sum(len(v) for v in candidate_warnings.values())} total)")
        print("=" * 70)
        print("(These links target candidate concepts that are not yet active)")
        for file, warnings in sorted(candidate_warnings.items())[:10]:
            print(f"\n📄 {file}")
            for w in warnings:
                print(f"   ? [[{w}]]")
        if len(candidate_warnings) > 10:
            print(f"\n... and {len(candidate_warnings) - 10} more files")
        print()

    if not broken_links:
        print("✓ No broken links!")
    print()

    return 0 if not broken_links else 1


def check_data_quality(vault_dir: Path):
    """Check data quality."""
    print("=" * 70)
    print("Data Quality Check")
    print("=" * 70)
    print()

    issues = []

    for md_file in iter_markdown_files(vault_dir):
        rel_path = md_file.relative_to(vault_dir)
        try:
            content = md_file.read_text(encoding='utf-8')

            if not content.startswith('---'):
                issues.append(f"{rel_path}: Missing frontmatter")

            lines = content.split('\n')
            content_lines = [l for l in lines if l.strip() and not l.strip().startswith('#')]
            if len(content_lines) < 5:
                issues.append(f"{rel_path}: Content too short ({len(content_lines)} lines)")

            if len(content.strip()) < 100:
                issues.append(f"{rel_path}: File content too small")

        except Exception as e:
            issues.append(f"{rel_path}: Read error - {e}")

    if issues:
        print(f"⚠ Found {len(issues)} issues:")
        for issue in issues[:20]:
            print(f"  - {issue}")
        if len(issues) > 20:
            print(f"  ... and {len(issues) - 20} more")
        return 1
    else:
        print("✓ Data quality check passed")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Registry-aware showcase link checker")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory to inspect")
    args = parser.parse_args()

    vault_dir = resolve_vault_dir(args.vault_dir)
    link_status = check_links(vault_dir)
    print()
    quality_status = check_data_quality(vault_dir)

    print()
    print("=" * 70)
    if link_status == 0 and quality_status == 0:
        print("✓ All checks passed")
        return 0

    print("⚠ Some checks failed, see details above")
    return 1


if __name__ == "__main__":
    sys.exit(main())
