"""Lock down the ``EvergreenExtractor.SYSTEM_PROMPT`` shape.

BL-058 (2026-05-05) replaced the v1 prompt with a v2 ``CandidateUnit``
prompt.  v1 used a "抽得多比抽得少好" volume bias and a forced
``定义 / 详细解释 / 为什么重要`` template; v2 caps at 0-8 units, allows
``units=[]`` with ``skip_reason``, and demands a ``source_anchor``
verbatim quote on every unit.

The original PR-A "prompt liberation" tests (which verified the v1
LIBERATION away from a 3-5 cap and a "return [] if insufficient"
escape hatch) are mostly preserved here because BL-058 keeps those
fixes — but several v1-specific contracts that BL-058 deliberately
DROPPED have been updated to assert the new behavior instead.

These tests don't call the LLM (no API in CI); they assert the
prompt's SHAPE so the next time someone "tightens" it back, the test
catches it.
"""

from __future__ import annotations

from ovp_pipeline.auto_evergreen_extractor import EvergreenExtractor


class TestPromptLiberation:
    """Properties carried forward from PR-A's original liberation:

    * No hardcoded "extract 3-5" cap
    * No "return [] if information insufficient" — replaced by BL-058's
      explicit ``skip_reason`` mechanism (still permits empty output,
      but as a deliberate signal, not an escape hatch)
    * Title must be a declarative claim, not a noun phrase
    * Output must carry a ``unit_type`` classification
    """

    def test_no_hard_concept_cap(self):
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        assert "提取3-5个" not in prompt
        assert "提取 3-5 个" not in prompt
        assert "最多5个概念" not in prompt
        assert "最多 5 个概念" not in prompt

    def test_no_v1_escape_hatch(self):
        """v1's worst line: "信息不足以形成稳定知识，请返回空数组" — gave
        the LLM a permissive escape hatch.  BL-058 v2 replaces this with
        a structured ``skip_reason`` field that records WHY the model
        chose to return nothing, so empty output becomes auditable
        rather than silent."""
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        assert "信息不足以形成稳定知识" not in prompt
        # v2 still permits empty output, but as a deliberate signal:
        assert "skip_reason" in prompt

    def test_demands_declarative_claim_titles(self):
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        # v2 says: "title 是观点,不是分类" with concrete bad/good examples.
        assert "陈述句" in prompt or "claim" in prompt.lower()
        # The "title 是观点" rule is the BL-058-specific phrasing.
        assert "Title 是观点" in prompt or "title 是观点" in prompt.lower()

    def test_requires_unit_type_classification(self):
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        assert "unit_type" in prompt
        # v2 expanded the taxonomy from 4 to 10 — verify the new
        # categories are present.
        for value in (
            "fact", "method", "procedure", "tradeoff", "failure_mode",
            "counterexample", "case_detail", "learning", "decision", "quote",
        ):
            assert value in prompt, f"{value!r} missing from v2 unit_type taxonomy"


class TestPromptStillContractsCorrectly:
    """The v2 prompt must still preserve the structural contracts
    downstream code depends on (JSON shape, slug rules), even though
    the OUTER schema changed from a bare list to a wrapped object.
    """

    def test_v2_output_is_wrapped_json(self):
        """v1 returned a bare JSON array; v2 returns
        ``{units: [...], skip_reason, source_value_summary}``.  This
        test pins the wrapper shape so a future "simplification" back
        to a bare list fails fast."""
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        assert "JSON" in prompt
        # Wrapper keys
        assert '"units"' in prompt
        assert '"skip_reason"' in prompt
        assert '"source_value_summary"' in prompt

    def test_demands_source_anchor_per_unit(self):
        """v2 hard rule #9: each unit must carry a verbatim
        ``source_anchor`` from the source body.  Mechanical fidelity
        check — without this field, future ``ovp-fidelity-replay``
        cannot grep evergreen claims back to their source."""
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        assert "source_anchor" in prompt
        assert "逐字" in prompt or "verbatim" in prompt.lower()

    def test_demands_specifics_classification(self):
        """v2 each unit lists which kinds of specifics it preserved.
        The categories matter — they're how aggregate metrics roll up
        ("which unit_types lose which kinds of specifics most often")."""
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        assert "specifics" in prompt
        for category in ("numbers", "names", "tradeoffs", "examples", "edge_cases"):
            assert category in prompt, f"specifics category {category!r} missing"

    def test_caps_volume_at_eight(self):
        """v2 explicit cap.  v1's "抽得多比抽得少好" + 5-30 floors caused
        the abstraction inflation pattern documented in the 2026-05-05
        fidelity audit.  BL-058 inverts this: 0-8, 宁缺勿滥."""
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        # Numeric cap is in the rules
        assert "0-8" in prompt
        # The v1 volume-bias slogan must NOT come back.
        assert "抽得多比抽得少好" not in prompt
        assert "抽得多" not in prompt or "抽得多" in "宁可少抽,不要凑数".lower()  # phrasing OK

    def test_demands_kebab_case_slugs(self):
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        assert "kebab-case" in prompt

    def test_related_concepts_optional_in_v2(self):
        """v1 forced ``≥ 3`` related_concepts.  v2 explicitly allows
        ``0-5``.  This was an OVP-specific change driven by chris's
        observation that v1 evergreens with 3-link minimums often
        contained unrelated 凑数 wikilinks."""
        prompt = EvergreenExtractor.SYSTEM_PROMPT
        assert "related_concepts" in prompt
        # v1's "至少 3" requirement must be gone
        assert "至少 3" not in prompt
        assert "至少3" not in prompt
        # v2's 0-5 envelope must be present
        assert "0-5" in prompt
