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


# Try to import litellm
try:
    import litellm
    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False


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

    def _build_file_index(self) -> None:
        """Build index of all markdown files in vault."""
        self._all_files = set()
        for md_file in self.vault_dir.rglob("*.md"):
            rel = md_file.relative_to(self.vault_dir)
            self._all_files.add(rel.as_posix())
            self._all_files.add(rel.stem)
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
    """Resolve broken links using registry + LLM."""

    def __init__(self, registry: ConceptRegistry, llm_client: Any = None):
        self.registry = registry
        self.llm = llm_client

    def resolve_unique_mention(self, mention: UniqueBrokenMention) -> ResolutionResult:
        """Resolve a unique broken mention to a decision."""
        surface = mention.surface
        contexts = mention.contexts[:3]  # Limit to 3 contexts

        # First try exact/alias match in registry
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

        # Search for similar concepts
        search_results = self.registry.search(surface, topk=10)
        if search_results:
            best_entry, best_score = search_results[0]
            if best_score >= 0.5:
                return ResolutionResult(
                    surface=surface,
                    action="link_existing",
                    slug=best_entry.slug,
                    display=surface,
                    confidence=best_score * 0.8,
                    occurrences=mention.occurrences,
                )

        # No good match - suggest candidate or no_link
        # Use LLM if available for better judgment
        if LITELLM_AVAILABLE and self.llm is not None:
            try:
                return self._resolve_via_llm(surface, contexts, mention.occurrences)
            except Exception as e:
                print(f"  Warning: LLM resolution failed for '{surface}': {e}")

        # Fallback: create candidate for reasonable-length surfaces
        if len(surface) >= 3 and not any(
            c in surface for c in ["http://", "https://", "file://"]
        ):
            return ResolutionResult(
                surface=surface,
                action="create_candidate",
                proposed_slug=self._surface_to_slug(surface),
                title=surface,
                confidence=0.5,
                occurrences=mention.occurrences,
            )
        else:
            return ResolutionResult(
                surface=surface,
                action="no_link",
                confidence=1.0,
                occurrences=mention.occurrences,
            )

    def _resolve_via_llm(self, surface: str, contexts: list[str],
                          occurrences: list[BrokenLinkOccurrence]) -> ResolutionResult:
        """Use LLM to decide resolution."""
        prompt = f"""你正在执行历史 wikilink 迁移。请对一个唯一 surface 的多条上下文做统一判断。

Surface: {surface}
Contexts:
{chr(10).join(f'- {c}' for c in contexts)}

请判断：
- 如果这只是译名/缩写/旧称，合并到已有概念 (link_existing)
- 如果这是新概念且有价值，创建候选 (create_candidate)
- 如果这只是临时表述，没有长期链接价值，no_link

只输出JSON：{{"action": "link_existing"|"create_candidate"|"no_link", "slug": "...", "confidence": 0.0}}
"""

        try:
            response, _ = self.llm.generate(
                system_prompt="你是一个概念链接决策专家。",
                user_prompt=prompt,
                max_tokens=500,
            )

            data = json.loads(response)
            return ResolutionResult(
                surface=surface,
                action=data.get("action", "no_link"),
                slug=data.get("slug", ""),
                display=surface,
                confidence=data.get("confidence", 0.5),
                occurrences=occurrences,
            )
        except Exception:
            return ResolutionResult(
                surface=surface,
                action="no_link",
                confidence=0.0,
                occurrences=occurrences,
            )

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

    # Categorize by confidence
    auto_fix = [r for r in results if r.action == "link_existing" and r.confidence >= MIN_AUTO_FIX_CONFIDENCE]
    review = [r for r in results if 0.7 <= r.confidence < MIN_AUTO_FIX_CONFIDENCE]
    candidates = [r for r in results if r.action == "create_candidate"]
    no_link = [r for r in results if r.action == "no_link"]

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
    print(f"  No link: {len(no_link)}")


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
        resolver = BrokenLinkResolver(registry)
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
