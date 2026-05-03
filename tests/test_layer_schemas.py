"""Layer schema fitness tests — Guardrail 2.

Lock the markdown frontmatter shape for every layer in
``ovp_pipeline.layer_schemas``.  Two test surfaces:

1. **Schema-itself unit tests** — the schema definitions are coherent:
   every layer has a non-empty glob, every required field has a sane
   default, validators don't crash on the empty case.
2. **Per-layer fixtures** — synthetic markdown for each layer, asserting
   ``audit_layer`` returns zero violations.  These are the canonical
   examples that future generators must keep producing.

These tests do *not* run against the user's real vault — those scans
are exposed via ``ovp-audit-layers`` and intentionally separated from
CI so test runs stay deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ovp_pipeline.layer_schemas import (
    ALL_LAYERS,
    DEEP_DIVE_SCHEMA,
    EVERGREEN_SCHEMA,
    ENTITY_ACTIVE_SCHEMA,
    ENTITY_CANDIDATE_SCHEMA,
    SOURCE_SCHEMA,
    audit_all_layers,
    audit_layer,
    parse_frontmatter,
    violations_by_rule,
)


# ---------------------------------------------------------------------------
# Schema-itself sanity
# ---------------------------------------------------------------------------


class TestSchemaCoherence:
    def test_every_schema_has_glob(self):
        for s in ALL_LAYERS:
            assert s.glob_patterns, f"{s.name} has no glob_patterns"

    def test_every_schema_has_rules(self):
        for s in ALL_LAYERS:
            assert s.rules, f"{s.name} has no field rules"

    def test_severities_are_valid(self):
        for s in ALL_LAYERS:
            for r in s.rules:
                assert r.severity in {"HIGH", "MEDIUM", "LOW"}, (
                    f"{s.name}.{r.name} has bogus severity {r.severity!r}"
                )

    def test_layer_names_unique(self):
        names = [s.name for s in ALL_LAYERS]
        assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Per-layer fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def layer_vault(tmp_path):
    """Vault with one canonical sample per layer, all schema-clean."""
    vault = tmp_path / "vault"
    # L1 Source
    (vault / "50-Inbox" / "03-Processed" / "2026-04").mkdir(parents=True)
    (vault / "50-Inbox" / "03-Processed" / "2026-04" / "sample.md").write_text(
        "---\n"
        "title: Sample article\n"
        "source: https://example.com/post\n"
        "date: 2026-04-01\n"
        "tags: [ai]\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    # L2 Deep Dive — article subtype
    dd_dir = vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04"
    dd_dir.mkdir(parents=True)
    (dd_dir / "2026-04-01_sample_深度解读.md").write_text(
        "---\n"
        "title: Sample\n"
        "source: https://example.com/post\n"
        "date: 2026-04-01\n"
        "type: article\n"
        "tags: [ai-agents]\n"
        "status: completed\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    # L2 Deep Dive — github project subtype (no source, has github)
    (dd_dir / "2026-04-02_sample_repo_深度解读.md").write_text(
        "---\n"
        "title: sample-repo\n"
        "github: https://github.com/foo/bar\n"
        "date: 2026-04-02\n"
        "type: project\n"
        "tags: [tool, github]\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    # L3 Evergreen
    (vault / "10-Knowledge" / "Evergreen").mkdir(parents=True)
    (vault / "10-Knowledge" / "Evergreen" / "sample-concept.md").write_text(
        "---\n"
        "note_id: sample-concept\n"
        "title: Sample Concept\n"
        "type: evergreen\n"
        "entity_type: concept\n"
        "date: 2026-04-01\n"
        "tags: [general, evergreen]\n"
        "aliases: [sample]\n"
        "area: general\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    # L4 Entity active
    (vault / "10-Knowledge" / "Entity").mkdir(parents=True)
    (vault / "10-Knowledge" / "Entity" / "anthropic.md").write_text(
        "---\n"
        "note_id: anthropic\n"
        "title: Anthropic\n"
        "type: entity\n"
        "entity_type: company\n"
        "date: 2026-04-01\n"
        "tags: [entity, company]\n"
        "aliases: [Anthropic AI]\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    # L5 Entity candidate
    (vault / "10-Knowledge" / "Entity" / "_Candidates").mkdir(parents=True)
    (vault / "10-Knowledge" / "Entity" / "_Candidates" / "tool-x.md").write_text(
        "---\n"
        "note_id: tool-x\n"
        "title: Tool X\n"
        "type: entity\n"
        "entity_type: tool\n"
        "status: candidate\n"
        "date: 2026-04-01\n"
        "tags: [entity, tool, candidate]\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    return vault


def _high_violations(report_layer):
    return [v for v in report_layer["violations"] if v["severity"] == "HIGH"]


class TestCanonicalFixturesPass:
    """Each layer's canonical sample must produce zero HIGH violations."""

    def test_l1_source_clean(self, layer_vault):
        violations, n = audit_layer(layer_vault, SOURCE_SCHEMA)
        high = [v for v in violations if v.severity == "HIGH"]
        assert n == 1
        assert high == [], f"L1 violations: {[v.message for v in high]}"

    def test_l2_deep_dive_article_clean(self, layer_vault):
        violations, n = audit_layer(layer_vault, DEEP_DIVE_SCHEMA)
        high = [v for v in violations if v.severity == "HIGH"]
        assert n == 2  # article + project samples
        assert high == [], f"L2 violations: {[v.message for v in high]}"

    def test_l3_evergreen_clean(self, layer_vault):
        violations, n = audit_layer(layer_vault, EVERGREEN_SCHEMA)
        high = [v for v in violations if v.severity == "HIGH"]
        assert n == 1
        assert high == [], f"L3 violations: {[v.message for v in high]}"

    def test_l4_entity_active_clean(self, layer_vault):
        violations, n = audit_layer(layer_vault, ENTITY_ACTIVE_SCHEMA)
        high = [v for v in violations if v.severity == "HIGH"]
        assert n == 1  # excludes _Candidates
        assert high == [], f"L4 violations: {[v.message for v in high]}"

    def test_l5_entity_candidate_clean(self, layer_vault):
        violations, n = audit_layer(layer_vault, ENTITY_CANDIDATE_SCHEMA)
        high = [v for v in violations if v.severity == "HIGH"]
        assert n == 1
        assert high == [], f"L5 violations: {[v.message for v in high]}"


# ---------------------------------------------------------------------------
# Defect-detection tests — every regression class we hit in May 2026 must
# light up the audit.
# ---------------------------------------------------------------------------


class TestKnownDefectsAreCaught:
    """The audit must detect every regression that survived without
    detection in the field.  Each test seeds a vault with a synthetic
    instance of a known defect and asserts the audit reports it HIGH.
    """

    def _vault_with_deep_dive(self, tmp_path: Path, body: str) -> Path:
        vault = tmp_path / "vault"
        d = vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04"
        d.mkdir(parents=True)
        (d / "x_深度解读.md").write_text(body, encoding="utf-8")
        return vault

    def test_fence_wrapped_frontmatter_caught(self, tmp_path):
        # The bug we discovered in Phase B + repaired in this PR:
        # ```yaml ... ``` wrapping makes Obsidian skip the frontmatter.
        vault = self._vault_with_deep_dive(tmp_path,
            "```yaml\n"
            "---\n"
            "title: x\n"
            "source: https://x.com/foo\n"
            "date: 2026-04-01\n"
            "type: article\n"
            "tags: [a]\n"
            "---\n"
            "```\n\n"
            "body\n",
        )
        violations, _ = audit_layer(vault, DEEP_DIVE_SCHEMA)
        # parse_frontmatter must reject the fenced form → frontmatter rule fires.
        assert any(v.rule == "frontmatter" and v.severity == "HIGH" for v in violations), (
            f"fence-wrap not caught: {[v.message for v in violations]}"
        )

    def test_unquoted_title_with_colon_caught(self, tmp_path):
        # The Phase B + repair_yaml_titles bug.
        vault = self._vault_with_deep_dive(tmp_path,
            "---\n"
            "title: Foo: Bar Baz\n"  # unquoted colon → YAML fail
            "source: https://x.com/foo\n"
            "date: 2026-04-01\n"
            "type: article\n"
            "tags: [a]\n"
            "---\n"
            "body\n",
        )
        violations, _ = audit_layer(vault, DEEP_DIVE_SCHEMA)
        assert any(v.severity == "HIGH" for v in violations)

    def test_l3_note_id_mismatch_caught(self, tmp_path):
        vault = tmp_path / "vault"
        d = vault / "10-Knowledge" / "Evergreen"
        d.mkdir(parents=True)
        (d / "concept-x.md").write_text(
            "---\n"
            "note_id: wrong-slug\n"   # mismatch with stem
            "title: x\n"
            "type: evergreen\n"
            "entity_type: concept\n"
            "date: 2026-04-01\n"
            "tags: [general, evergreen]\n"
            "aliases: [x]\n"
            "area: general\n"
            "---\n",
            encoding="utf-8",
        )
        violations, _ = audit_layer(vault, EVERGREEN_SCHEMA)
        high = [v for v in violations if v.severity == "HIGH"]
        assert any("does not match filename stem" in v.message for v in high)

    def test_l4_entity_type_outside_allowed_caught(self, tmp_path):
        vault = tmp_path / "vault"
        d = vault / "10-Knowledge" / "Entity"
        d.mkdir(parents=True)
        (d / "thing.md").write_text(
            "---\n"
            "note_id: thing\n"
            "title: Thing\n"
            "type: entity\n"
            "entity_type: not_a_real_kind\n"   # outside ENTITY_LAYER_TYPES
            "date: 2026-04-01\n"
            "tags: [entity]\n"
            "---\n",
            encoding="utf-8",
        )
        violations, _ = audit_layer(vault, ENTITY_ACTIVE_SCHEMA)
        assert any(v.rule == "entity_type" and v.severity == "HIGH" for v in violations)

    def test_l2_neither_source_nor_github_caught(self, tmp_path):
        # Cross-field rule: deep dives must have at least one URL origin.
        vault = self._vault_with_deep_dive(tmp_path,
            "---\n"
            "title: x\n"
            "date: 2026-04-01\n"
            "type: article\n"
            "tags: [a]\n"
            "---\n",
        )
        violations, _ = audit_layer(vault, DEEP_DIVE_SCHEMA)
        assert any(v.rule == "source_or_github" and v.severity == "HIGH" for v in violations)


class TestParseFrontmatter:
    def test_plain_frontmatter(self):
        fm = parse_frontmatter("---\ntitle: x\n---\nbody\n")
        assert fm == {"title": "x"}

    def test_no_frontmatter_returns_none(self):
        assert parse_frontmatter("just body, no frontmatter\n") is None

    def test_fence_wrapped_returns_none(self):
        # Critical: this is the bug — Obsidian / KG / parser must all reject.
        assert parse_frontmatter("```yaml\n---\ntitle: x\n---\n```\n") is None

    def test_malformed_yaml_returns_none(self):
        assert parse_frontmatter("---\ntitle: Foo: Bar\n---\n") is None


class TestViolationsByRuleHelper:
    def test_groups_by_rule(self):
        from ovp_pipeline.layer_schemas import Violation
        vs = [
            Violation(layer="L1", file=Path("a"), rule="title", severity="HIGH", message=""),
            Violation(layer="L1", file=Path("b"), rule="title", severity="HIGH", message=""),
            Violation(layer="L1", file=Path("c"), rule="tags", severity="MEDIUM", message=""),
        ]
        assert violations_by_rule(vs) == {"title": 2, "tags": 1}
