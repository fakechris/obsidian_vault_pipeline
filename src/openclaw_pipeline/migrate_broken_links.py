#!/usr/bin/env python3
"""
Migrate Broken Links - Scan and fix broken wikilinks across the vault.

This script:
1. Scans all markdown files for wikilinks
2. Identifies broken links (not in registry, not in filesystem)
3. Deduplicates by unique surface form
4. Samples contexts
5. Resolves via registry lookup + LLM
6. Produces dry-run report or applies fixes

Usage:
    python -m openclaw_pipeline.migrate_broken_links --scan
    python -m openclaw_pipeline.migrate_broken_links --resolve --dry-run
    python -m openclaw_pipeline.migrate_broken_links --apply --min-confidence 0.9
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .concept_registry import ConceptRegistry, ConceptEntry, STATUS_ACTIVE


# Constants
MIN_AUTO_FIX_CONFIDENCE = 0.90
REPORT_DIR = Path("60-Logs/migration-reports")


@dataclass
class BrokenLinkOccurrence:
    """A single broken wikilink occurrence."""
    file_path: str
    surface: str
    context: str  # Surrounding text
    line_num: int


@dataclass
class UniqueBrokenMention:
    """A unique broken mention (deduped by surface)."""
    surface: str
    occurrences: list[BrokenLinkOccurrence] = field(default_factory=list)
    contexts: list[str] = field(default_factory=list)


@dataclass
class ResolutionResult:
    """Result of resolving a unique broken mention."""
    surface: str
    action: str  # "link_existing", "create_candidate", "no_link"
    slug: str = ""
    display: str = ""
    confidence: float = 0.0
    proposed_slug: str = ""
    title: str = ""
    definition: str = ""
    reason: str = ""
    occurrences: list[BrokenLinkOccurrence] = field(default_factory=list)


class WikilinkExtractor:
    """Extract wikilinks from markdown content."""

    # Pattern: [[target]] or [[target|display]]
    WIKILINK_PATTERN = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]')

    @classmethod
    def extract_all(cls, content: str, file_path: str) -> list[BrokenLinkOccurrence]:
        """Extract all wikilinks from content."""
        occurrences = []
        lines = content.split('\n')

        for line_num, line in enumerate(lines, 1):
            for match in cls.WIKILINK_PATTERN.finditer(line):
                surface = match.group(1).strip()
                if surface:
                    # Get surrounding context
                    start = max(0, match.start() - 30)
                    end = min(len(line), match.end() + 30)
                    context = line[start:end].replace('\n', ' ').strip()

                    occurrences.append(BrokenLinkOccurrence(
                        file_path=file_path,
                        surface=surface,
                        context=context,
                        line_num=line_num,
                    ))

        return occurrences


class BrokenLinkScanner:
    """Scan vault for broken wikilinks."""

    def __init__(self, vault_dir: Path, registry: ConceptRegistry):
        self.vault_dir = vault_dir
        self.registry = registry
        self._all_files: set[str] = set()
        self._file_map: dict[str, Path] = {}  # stem -> Path

    def _build_file_index(self) -> None:
        """Build index of all markdown files in vault."""
        self._all_files = set()
        self._file_map = {}
        for md_file in self.vault_dir.rglob("*.md"):
            rel = md_file.relative_to(self.vault_dir)
            self._all_files.add(rel.as_posix())
            self._all_files.add(rel.stem)
            # Map stem to full path for fuzzy matching
            self._file_map[rel.stem] = rel
            # Also add without extension at various depths
            for parent in rel.parents:
                self._all_files.add(parent.as_posix())

    def is_broken(self, surface: str) -> bool:
        """Check if a link target is broken."""
        # Check registry (active concepts only)
        if self.registry.has_active_slug(surface):
            return False
        if self.registry.has_alias(surface):
            return False

        # Check filesystem
        if surface in self._all_files:
            return False

        # Check common path patterns
        for pattern in [
            f"10-Knowledge/Evergreen/{surface}.md",
            f"10-Knowledge/Evergreen/{surface}/index.md",
            f"20-Areas/{surface}.md",
            f"20-Areas/{surface}/index.md",
        ]:
            if pattern in self._all_files or (self.vault_dir / pattern).exists():
                return False

        return True

    def find_matching_file(self, surface: str) -> Path | None:
        """Find a file matching the given surface (for path-like or fuzzy matches)."""
        # Direct path match
        if (self.vault_dir / surface).exists():
            return Path(surface)

        # Try normalized path
        normalized = surface.replace('\\', '/')
        if (self.vault_dir / normalized).exists():
            return Path(normalized)

        # Try relative path resolution
        for base in ['', '10-Knowledge/Evergreen/', '20-Areas/', '40-Resources/']:
            test_path = base + surface
            if (self.vault_dir / test_path).exists():
                return Path(test_path)

        # Try to find by stem (fuzzy match for date-titled articles)
        # e.g., "2026-03-25_Harness_engineering_leveraging_Codex" -> "2026-03-25_Harness_engineering_leveraging_Codex_深度解读.md"
        if surface in self._file_map:
            return self._file_map[surface]

        return None

    def scan(self) -> list[UniqueBrokenMention]:
        """Scan all markdown files and find broken links."""
        self._build_file_index()

        # Collect all occurrences by surface
        surface_map: dict[str, UniqueBrokenMention] = {}

        for md_file in self.vault_dir.rglob("*.md"):
            # Skip very large files (binary-like)
            if md_file.stat().st_size > 5_000_000:
                continue

            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue

            rel_path = md_file.relative_to(self.vault_dir).as_posix()
            occurrences = WikilinkExtractor.extract_all(content, rel_path)

            for occ in occurrences:
                if self.is_broken(occ.surface):
                    if occ.surface not in surface_map:
                        surface_map[occ.surface] = UniqueBrokenMention(surface=occ.surface)
                    surface_map[occ.surface].occurrences.append(occ)
                    if occ.context not in surface_map[occ.surface].contexts:
                        surface_map[occ.surface].contexts.append(occ.context)

        # Convert to list and sort by occurrence count
        result = list(surface_map.values())
        result.sort(key=lambda x: len(x.occurrences), reverse=True)
        return result


class BrokenLinkResolver:
    """
    Resolve broken links using registry.

    约定目录方案 (Agreed Directory Convention):
    - Wikilink 包含 `/` → 路径型引用，跳过 registry，只验证文件存在
    - Wikilink 不含 `/` → slug 型引用，通过 registry 解析

    Resolution pipeline for slug-based wikilinks (no `/`):
    1. Registry exact/alias match -> link_existing
    2. Registry search match (score >= 0.5) -> link_existing
    3. Otherwise -> create_candidate

    Path-based wikilinks are left untouched (keep_as_path).
    """

    def __init__(self, registry: ConceptRegistry, scanner: "BrokenLinkScanner | None" = None,
                 llm_client: Any = None):
        self.registry = registry
        self.scanner = scanner
        self.llm = llm_client

    def resolve_unique_mention(self, mention: UniqueBrokenMention) -> ResolutionResult:
        """Resolve a unique broken mention to a decision."""
        surface = mention.surface

        # Step 0: 判断是路径型还是 slug 型
        if '/' in surface:
            return self._resolve_path_based(surface, mention)
        else:
            return self._resolve_slug_based(surface, mention)

    def _resolve_path_based(self, surface: str, mention: UniqueBrokenMention) -> ResolutionResult:
        """
        路径型 wikilink 处理：
        - 尝试找对应文件（直接路径、规范化路径、.md后缀）
        - 找到 → keep_as_path（不修改，保留文章链接）
        - 找不到 → no_link（真正破碎的路径引用）
        """
        # 规范化路径（去除 ../ 或 ./ 等）
        normalized = self._normalize_path(surface)

        # 尝试多种路径组合（按优先级排序）
        path_attempts = [
            # 原始
            surface,
            normalized,
            # 加 .md 后缀
            f"{normalized}.md",
            f"{surface}.md",
            # 目录形式
            f"{normalized}/index.md",
            f"{surface}/index.md",
        ]

        vault_dir = self.scanner.vault_dir if self.scanner else None
        if not vault_dir:
            return ResolutionResult(
                surface=surface,
                action="no_link",
                confidence=1.0,
                reason="no_scanner",
                occurrences=mention.occurrences,
            )

        for path_attempt in path_attempts:
            if (vault_dir / path_attempt).is_file():
                return ResolutionResult(
                    surface=surface,
                    action="keep_as_path",
                    confidence=1.0,
                    reason="file_exists_path_reference",
                    occurrences=mention.occurrences,
                )

        # 文件找不到 → no_link
        return ResolutionResult(
            surface=surface,
            action="no_link",
            confidence=1.0,
            reason="broken_path_reference",
            occurrences=mention.occurrences,
        )

    def _resolve_slug_based(self, surface: str, mention: UniqueBrokenMention) -> ResolutionResult:
        """
        Slug 型 wikilink 处理：
        - Registry 精确/别名匹配 → link_existing
        - Registry 搜索匹配 (score >= 0.5) → link_existing
        - 找不到 → create_candidate
        """
        # Step 1: Registry exact/alias match
        entry = self.registry.find_by_surface(surface)
        if entry and entry.status == STATUS_ACTIVE:
            return ResolutionResult(
                surface=surface,
                action="link_existing",
                slug=entry.slug,
                display=surface,
                confidence=0.95,
                occurrences=mention.occurrences,
            )

        # Step 2: Registry search match
        search_results = self.registry.search(surface, topk=10)
        if search_results:
            best_entry, best_score = search_results[0]
            if best_score >= 0.5:
                return ResolutionResult(
                    surface=surface,
                    action="link_existing",
                    slug=best_entry.slug,
                    display=surface,
                    confidence=best_score,
                    occurrences=mention.occurrences,
                )

        # Step 3: No match -> create_candidate
        return ResolutionResult(
            surface=surface,
            action="create_candidate",
            proposed_slug=self._surface_to_slug(surface),
            title=surface,
            confidence=0.5,
            reason="no_registry_match",
            occurrences=mention.occurrences,
        )

    def _normalize_path(self, surface: str) -> str:
        """规范化路径：去除 ../ ./ 等"""
        # 去除开头和结尾的 .././等
        normalized = surface
        # 去除 ../ 相对路径前缀
        while normalized.startswith('../') or normalized.startswith('./'):
            if normalized.startswith('../'):
                normalized = normalized[3:]
            elif normalized.startswith('./'):
                normalized = normalized[2:]
        # 去除结尾的 \ 或 /
        normalized = normalized.rstrip('\\/')
        return normalized

    def _surface_to_slug(self, surface: str) -> str:
        """Convert surface to kebab-case slug."""
        slug = surface.strip()
        slug = re.sub(r'\s+', '-', slug)
        slug = re.sub(r'[^\w\-]', '', slug)
        slug = re.sub(r'-+', '-', slug)
        return slug.lower()


class LinkPatcher:
    """Apply link fixes to markdown files."""

    def __init__(self, vault_dir: Path, dry_run: bool = True):
        self.vault_dir = vault_dir
        self.dry_run = dry_run
        self._patched_files: list[str] = []
        self._patch_count: int = 0

    def patch(self, result: ResolutionResult) -> None:
        """Apply a resolution result to all occurrences."""
        if result.action != "link_existing" or not result.slug:
            return

        wikilink = f"[[{result.slug}|{result.display}]]"

        for occ in result.occurrences:
            file_path = self.vault_dir / occ.file_path
            if not file_path.exists():
                continue

            try:
                content = file_path.read_text(encoding="utf-8")

                # Replace [[surface]] with [[slug|surface]]
                old_pattern = rf'\[\[{re.escape(occ.surface)}\]\]'
                new_content = re.sub(old_pattern, wikilink, content)

                # Also handle [[anything|surface]]
                old_pattern2 = rf'\[\[[^\]]+\|{re.escape(occ.surface)}\]\]'
                new_content = re.sub(old_pattern2, wikilink, new_content)

                if new_content != content:
                    if not self.dry_run:
                        file_path.write_text(new_content, encoding="utf-8")
                    self._patched_files.append(occ.file_path)
                    self._patch_count += 1

            except Exception as e:
                print(f"  Warning: Could not patch {occ.file_path}: {e}")

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "patched_files": len(set(self._patched_files)),
            "total_patches": self._patch_count,
        }


def write_scan_report(mentions: list[UniqueBrokenMention], vault_dir: Path) -> None:
    """Write scan results to report files."""
    report_dir = vault_dir / REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    # JSON report
    json_path = report_dir / f"broken-links-unique-{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        data = {
            "total_unique": len(mentions),
            "total_occurrences": sum(len(m.occurrences) for m in mentions),
            "mentions": [
                {
                    "surface": m.surface,
                    "occurrence_count": len(m.occurrences),
                    "contexts": m.contexts,
                }
                for m in mentions
            ],
        }
        json.dump(data, f, ensure_ascii=False, indent=2)

    # CSV report
    csv_path = report_dir / f"broken-links-unique-{timestamp}.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["surface", "occurrence_count", "files"])
        for m in mentions:
            files = sorted(set(o.file_path for o in m.occurrences))
            writer.writerow([m.surface, len(m.occurrences), "; ".join(files)])

    print(f"Scan report written to:")
    print(f"  {json_path}")
    print(f"  {csv_path}")


def write_resolve_report(results: list[ResolutionResult], vault_dir: Path,
                         dry_run: bool) -> None:
    """Write resolution results to report."""
    report_dir = vault_dir / REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    mode = "dry-run" if dry_run else "apply"

    # Categorize by action
    auto_fix = [r for r in results if r.action == "link_existing" and r.confidence >= MIN_AUTO_FIX_CONFIDENCE]
    review = [r for r in results if r.action == "link_existing" and 0.7 <= r.confidence < MIN_AUTO_FIX_CONFIDENCE]
    candidates = [r for r in results if r.action == "create_candidate"]
    no_link = [r for r in results if r.action == "no_link"]
    keep_as_path = [r for r in results if r.action == "keep_as_path"]

    # JSON report
    json_path = report_dir / f"resolution-report-{mode}-{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        data = {
            "mode": mode,
            "total": len(results),
            "auto_fix_count": len(auto_fix),
            "review_count": len(review),
            "candidate_count": len(candidates),
            "no_link_count": len(no_link),
            "keep_as_path_count": len(keep_as_path),
            "results": [
                {
                    "surface": r.surface,
                    "action": r.action,
                    "slug": r.slug or r.proposed_slug,
                    "confidence": r.confidence,
                    "occurrence_count": len(r.occurrences),
                }
                for r in results
            ],
        }
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Summary CSV
    csv_path = report_dir / f"resolution-summary-{mode}-{timestamp}.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["surface", "action", "slug", "confidence", "occurrences", "files"])
        for r in results:
            files = sorted(set(o.file_path for o in r.occurrences))
            writer.writerow([
                r.surface,
                r.action,
                r.slug or r.proposed_slug,
                f"{r.confidence:.2f}",
                len(r.occurrences),
                "; ".join(files),
            ])

    print(f"Resolution report written to:")
    print(f"  {json_path}")
    print(f"  {csv_path}")
    print()
    print(f"Summary ({mode}):")
    print(f"  Total unique mentions: {len(results)}")
    print(f"  Auto-fix (>= {MIN_AUTO_FIX_CONFIDENCE}): {len(auto_fix)}")
    print(f"  Review (0.7-0.9): {len(review)}")
    print(f"  Create candidate: {len(candidates)}")
    print(f"  No link (broken path refs): {len(no_link)}")
    print(f"  Keep as path (article links): {len(keep_as_path)}")


def main():
    parser = argparse.ArgumentParser(description="Migrate broken wikilinks")
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument("--scan", action="store_true", help="Scan for broken links")
    parser.add_argument("--resolve", action="store_true", help="Resolve broken links")
    parser.add_argument("--apply", action="store_true", help="Apply fixes (requires --resolve)")
    parser.add_argument("--dry-run", action="store_true", help="Dry run (default for resolve)")
    parser.add_argument("--min-confidence", type=float, default=MIN_AUTO_FIX_CONFIDENCE,
                        help=f"Min confidence for auto-fix (default: {MIN_AUTO_FIX_CONFIDENCE})")
    args = parser.parse_args()

    if not (args.scan or args.resolve):
        parser.print_help()
        return

    # Load registry
    registry = ConceptRegistry(args.vault_dir).load()
    print(f"Loaded {len(registry.entries)} registry entries")

    if args.scan:
        print("Scanning for broken links...")
        scanner = BrokenLinkScanner(args.vault_dir, registry)
        mentions = scanner.scan()
        print(f"Found {len(mentions)} unique broken mentions")
        print(f"Total broken occurrences: {sum(len(m.occurrences) for m in mentions)}")
        write_scan_report(mentions, args.vault_dir)

    if args.resolve:
        # Scan first
        print("Scanning for broken links...")
        scanner = BrokenLinkScanner(args.vault_dir, registry)
        mentions = scanner.scan()
        print(f"Found {len(mentions)} unique broken mentions")

        # Resolve
        print("Resolving...")
        resolver = BrokenLinkResolver(registry, scanner=scanner)
        results = []
        for i, mention in enumerate(mentions):
            if (i + 1) % 50 == 0:
                print(f"  Resolved {i + 1}/{len(mentions)}...")
            result = resolver.resolve_unique_mention(mention)
            results.append(result)
        print(f"Resolved {len(results)} mentions")

        # Report
        dry_run = not args.apply
        write_resolve_report(results, args.vault_dir, dry_run)

        # Apply if requested
        if args.apply:
            print("Applying fixes...")
            patcher = LinkPatcher(args.vault_dir, dry_run=False)
            for result in results:
                if result.action == "link_existing" and result.confidence >= args.min_confidence:
                    patcher.patch(result)
            stats = patcher.stats
            print(f"Patched {stats['patched_files']} files ({stats['total_patches']} links)")


if __name__ == "__main__":
    main()
