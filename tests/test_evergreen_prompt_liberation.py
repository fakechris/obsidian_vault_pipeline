"""Lock down the new ``EvergreenExtractor.SYSTEM_PROMPT`` shape.

PR-A liberates the prompt from the legacy ``3-5 个核心概念`` cap and the
``信息不足返回空数组`` escape hatch.  The May 2026 8-article cross-platform
deep-dive showed OVP extracted 0 evergreens on 7/8 articles while NM
extracted 4-11 atomic memories per article — the prompt was the
70%-weight root cause.

These tests don't call the LLM (no API in CI); they assert the prompt's
SHAPE so the next time someone "tightens" it back, the test catches it.
"""

from __future__ import annotations

from ovp_pipeline.auto_evergreen_extractor import EvergreenExtractor


class TestPromptLiberation:
    """The new prompt must:

      * NOT cap concept count at 3-5
      * NOT tell the LLM "return empty array if information is insufficient"
      * Demand declarative-claim titles, not noun phrases
      * Require ``unit_type`` classification (fact/procedure/learning/concept)
    """

    def test_no_hard_concept_cap(self):
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        # The legacy cap phrase that was responsible for OVP undercounting:
        assert "提取3-5个" not in prompt
        assert "提取 3-5 个" not in prompt
        assert "最多5个概念" not in prompt
        assert "最多 5 个概念" not in prompt

    def test_no_escape_hatch_for_insufficient_information(self):
        """The single most damaging line: it gave the LLM permission to
        return ``[]`` whenever it judged the article as 'marketing' or
        'not enough information'.  In the 8-article audit this fired on
        Polymarket / 缓存机制 / 457 PR / etc.
        """
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        assert "信息不足以形成稳定知识，请返回空数组" not in prompt
        assert "信息不足以形成稳定知识,请返回空数组" not in prompt
        assert "请返回空数组" not in prompt

    def test_demands_declarative_claim_titles(self):
        """NM's killer feature is that memory titles are claims you can
        read and learn from.  OVP's old prompt produced noun phrases like
        ``intention-layer``.  The new prompt must demand declarative
        sentences.
        """
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        assert "陈述句" in prompt or "declarative" in prompt.lower()
        assert "断言性" in prompt or "claim" in prompt.lower()

    def test_requires_unit_type_classification(self):
        """``unit_type`` is the structured taxonomy NM uses
        (fact/procedure/learning).  Output without unit_type loses the
        clarity that makes NM memories searchable by 'all procedures
        about caching' etc.
        """
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        assert "unit_type" in prompt
        for value in ("fact", "procedure", "learning"):
            assert value in prompt, f"{value!r} missing from unit_type taxonomy"

    def test_handles_long_primer_articles(self):
        """The Aman Context Engineering primer (95KB / 12,783 words)
        produced 0 evergreens under the old prompt.  The new prompt must
        explicitly demand atomic-unit extraction per section.
        """
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        # Some signal that long articles get more units, not the same 3-5.
        long_signals = ("长文", "primer", "综述", "15-30", "按章节")
        assert any(sig in prompt for sig in long_signals), (
            "Prompt no longer signals to handle long articles with more units"
        )

    def test_handles_chinese_technical_articles(self):
        """OVP's 缓存机制 / 457 PR / Polymarket articles all got 0
        evergreens.  These are Chinese technical articles.  The prompt
        must explicitly state these get the same granularity as English.
        """
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        chinese_signals = ("中文技术", "中文文章", "完全按英文同等粒度")
        assert any(sig in prompt for sig in chinese_signals), (
            "Prompt no longer instructs to give Chinese technical articles "
            "the same atomic-unit density as English"
        )


class TestPromptStillContractsCorrectly:
    """The new prompt must still preserve the structural contracts
    downstream code depends on (JSON shape, kind taxonomy, slug rules).
    """

    def test_still_demands_json_array_output(self):
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        assert "JSON" in prompt
        assert "[" in prompt and "]" in prompt

    def test_still_lists_entity_type_taxonomy(self):
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        # The 10 entity_type values that downstream object_kinds.py expects:
        for kind in ("concept", "person", "company", "tool", "project",
                     "paper", "event", "framework", "method"):
            assert kind in prompt, f"entity_type kind {kind!r} dropped"

    def test_still_demands_kebab_case_slugs(self):
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        assert "kebab-case" in prompt

    def test_still_demands_related_concepts(self):
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        assert "related_concepts" in prompt
        assert "至少 3" in prompt or "至少3" in prompt
