"""
Tests for concept_resolver module.
"""

import pytest
from pathlib import Path
from openclaw_pipeline.concept_registry import (
    ConceptRegistry,
    ConceptEntry,
    STATUS_ACTIVE,
)
from openclaw_pipeline.concept_resolver import (
    MentionExtractor,
    LinkRenderer,
    LinkResolutionSidecar,
    ResolutionDecision,
)


class TestMentionExtractor:
    def test_extract_from_wikilinks(self):
        extractor = MentionExtractor()
        content = '''
这是关于 [[DCF-Valuation]] 的文章。

还有 [[WACC|加权平均资本成本]] 的链接。
'''
        mentions = extractor.extract_from_wikilinks(content)
        assert len(mentions) == 2
        surfaces = {m.surface for m in mentions}
        assert "DCF-Valuation" in surfaces
        assert "WACC" in surfaces

    def test_extract_from_wikilinks_dedupe(self):
        extractor = MentionExtractor()
        content = '''
[[DCF-Valuation]] 第一次出现。
[[DCF-Valuation]] 第二次出现。
'''
        mentions = extractor.extract_from_wikilinks(content)
        assert len(mentions) == 1
        assert mentions[0].surface == "DCF-Valuation"


class TestLinkRenderer:
    def test_render_wikilink_link_existing(self):
        registry = ConceptRegistry(Path("/tmp"))
        renderer = LinkRenderer(registry)

        decision = ResolutionDecision(
            surface="DCF估值",
            action="link_existing",
            slug="DCF-Valuation",
            display="DCF估值",
            confidence=0.95,
        )

        result = renderer.render_wikilink(decision)
        assert result == "[[DCF-Valuation|DCF估值]]"

    def test_render_wikilink_create_candidate(self):
        registry = ConceptRegistry(Path("/tmp"))
        renderer = LinkRenderer(registry)

        decision = ResolutionDecision(
            surface="Some-New-Concept",
            action="create_candidate",
            proposed_slug="some-new-concept",
            confidence=0.5,
        )

        result = renderer.render_wikilink(decision)
        assert result == "Some-New-Concept"  # No link, plain text

    def test_render_wikilink_no_link(self):
        registry = ConceptRegistry(Path("/tmp"))
        renderer = LinkRenderer(registry)

        decision = ResolutionDecision(
            surface="临时表述",
            action="no_link",
            confidence=1.0,
        )

        result = renderer.render_wikilink(decision)
        assert result == "临时表述"  # No link, plain text

    def test_render_all(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        registry.add_entry(ConceptEntry(
            slug="dcf-valuation",
            title="DCF Valuation",
            aliases=["DCF估值"],
            definition="DCF.",
            area="investing",
        ))
        registry.save()

        renderer = LinkRenderer(registry)

        content = '''
这是关于 [[DCF估值]] 的文章。
还有 [[不存在]] 的链接。
'''
        decisions = [
            ResolutionDecision(
                surface="DCF估值",
                action="link_existing",
                slug="dcf-valuation",
                display="DCF估值",
                confidence=0.95,
            ),
            ResolutionDecision(
                surface="不存在",
                action="no_link",
                confidence=1.0,
            ),
        ]

        result = renderer.render_all(content, decisions)
        assert "[[dcf-valuation|DCF估值]]" in result
        assert "不存在" in result
        assert "[[DCF估值]]" not in result  # Should be replaced


class TestResolutionDecision:
    def test_decision_to_dict(self):
        decision = ResolutionDecision(
            surface="DCF估值",
            action="link_existing",
            slug="DCF-Valuation",
            display="DCF估值",
            confidence=0.95,
        )

        d = decision.to_dict()
        assert d["surface"] == "DCF估值"
        assert d["action"] == "link_existing"
        assert d["slug"] == "DCF-Valuation"
        assert d["confidence"] == 0.95

    def test_decision_from_dict(self):
        data = {
            "surface": "DCF估值",
            "action": "link_existing",
            "slug": "DCF-Valuation",
            "confidence": 0.95,
        }

        decision = ResolutionDecision.from_dict(data)
        assert decision.surface == "DCF估值"
        assert decision.action == "link_existing"
        assert decision.slug == "DCF-Valuation"


class TestLinkResolutionSidecar:
    def test_sidecar_to_dict(self):
        decisions = [
            ResolutionDecision(
                surface="DCF估值",
                action="link_existing",
                slug="DCF-Valuation",
                confidence=0.95,
            ),
        ]

        sidecar = LinkResolutionSidecar(
            article="2026-04-01_Test_深度解读",
            resolver_version="v2",
            area="investing",
            decisions=decisions,
        )

        d = sidecar.to_dict()
        assert d["article"] == "2026-04-01_Test_深度解读"
        assert d["resolver_version"] == "v2"
        assert d["area"] == "investing"
        assert len(d["decisions"]) == 1

    def test_sidecar_write(self, temp_vault):
        decisions = [
            ResolutionDecision(
                surface="DCF估值",
                action="link_existing",
                slug="DCF-Valuation",
                confidence=0.95,
            ),
        ]

        sidecar = LinkResolutionSidecar(
            article="test-article",
            decisions=decisions,
        )

        output_path = temp_vault / "60-Logs" / "link-resolution" / "test-article.json"
        sidecar.write(output_path)

        assert output_path.exists()
