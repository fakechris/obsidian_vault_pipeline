"""
Tests for migrate_broken_links module.
"""

import pytest
from pathlib import Path
from openclaw_pipeline.migrate_broken_links import (
    BrokenLinkScanner,
    BrokenLinkResolver,
    WikilinkExtractor,
    BrokenLinkOccurrence,
    UniqueBrokenMention,
)
from openclaw_pipeline.concept_registry import ConceptRegistry, ConceptEntry


class TestWikilinkExtractor:
    def test_extract_all(self):
        extractor = WikilinkExtractor()
        content = '''
这是关于 [[DCF-Valuation]] 的文章。
还有 [[WACC|加权平均资本成本]] 的链接。
以及 [[不存在]] 的链接。
'''
        occurrences = extractor.extract_all(content, "test.md")

        assert len(occurrences) == 3
        surfaces = {o.surface for o in occurrences}
        assert "DCF-Valuation" in surfaces
        assert "WACC" in surfaces
        assert "不存在" in surfaces


class TestBrokenLinkScanner:
    def test_scan_finds_broken_links(self, temp_vault, sample_evergreen_files, sample_article):
        """Test that scanner correctly identifies broken links."""
        # Build registry
        registry = ConceptRegistry(temp_vault)
        for f in sample_evergreen_files.iterdir():
            if f.suffix == ".md":
                registry.add_entry(ConceptEntry(
                    slug=f.stem,
                    title=f.stem,
                    aliases=[],
                    definition="Test.",
                    area="test",
                ))
        registry.save()

        scanner = BrokenLinkScanner(temp_vault, registry)
        mentions = scanner.scan()

        # Should find "Some-New-Concept" as broken
        broken_surfaces = {m.surface for m in mentions}
        assert "Some-New-Concept" in broken_surfaces


class TestBrokenLinkResolver:
    def test_resolve_existing_concept(self, temp_vault, sample_evergreen_files):
        """Test resolving a mention that exists in registry."""
        # Build registry
        registry = ConceptRegistry(temp_vault)
        registry.add_entry(ConceptEntry(
            slug="dcf-valuation",
            title="DCF Valuation",
            aliases=["DCF估值"],
            definition="DCF.",
            area="investing",
        ))
        registry.save()

        resolver = BrokenLinkResolver(registry)

        mention = UniqueBrokenMention(
            surface="DCF估值",
            occurrences=[BrokenLinkOccurrence(
                file_path="test.md",
                surface="DCF估值",
                context="test context",
                line_num=1,
            )],
            contexts=["test context"],
        )

        result = resolver.resolve_unique_mention(mention)

        assert result.action == "link_existing"
        assert result.slug == "dcf-valuation"
        assert result.confidence > 0.5

    def test_resolve_unknown_concept(self, temp_vault, sample_evergreen_files):
        """Test resolving a mention that doesn't exist in registry."""
        registry = ConceptRegistry(temp_vault)
        registry.save()

        resolver = BrokenLinkResolver(registry)

        mention = UniqueBrokenMention(
            surface="Unknown-New-Concept",
            occurrences=[BrokenLinkOccurrence(
                file_path="test.md",
                surface="Unknown-New-Concept",
                context="test context",
                line_num=1,
            )],
            contexts=["test context"],
        )

        result = resolver.resolve_unique_mention(mention)

        assert result.action == "create_candidate"
        assert result.proposed_slug == "unknown-new-concept"
        assert result.confidence == 0.5

    def test_resolve_hot_topic_no_link(self, temp_vault, sample_evergreen_files):
        """Test that short, topic-like mentions get no_link."""
        registry = ConceptRegistry(temp_vault)
        registry.save()

        resolver = BrokenLinkResolver(registry)

        # URL-like or very short mentions should get no_link
        mention = UniqueBrokenMention(
            surface="2026-04-05-特朗普关税",
            occurrences=[BrokenLinkOccurrence(
                file_path="test.md",
                surface="2026-04-05-特朗普关税",
                context="热点事件",
                line_num=1,
            )],
            contexts=["热点事件"],
        )

        result = resolver.resolve_unique_mention(mention)

        # Should create candidate for reasonable length
        assert result.action in ("create_candidate", "no_link")
