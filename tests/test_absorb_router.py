"""BL-062 PR#1: tests for the absorb-router foundation.

Covers two halves of the new module:

1. ``build_evergreen_index`` — reads canonical store, returns a
   compact list of ``IndexEntry`` ordered by slug, with summaries +
   key claims truncated to budget; tolerates missing DB / empty
   store; respects ``pack_name`` scope.

2. ``parse_router_response`` — strict JSON parser for the
   ``v2_router`` prompt's output; tolerant on cosmetic LLM quirks
   (markdown fence, single-string evidence_segments) but strict on
   shape (missing slug/title, empty decision).

Pass 2 (the actual LLM call from ``route_source``) is BL-062 PR#2
and not exercised here.
"""

from __future__ import annotations

import json
import sqlite3

import pytest


# ---------------------------------------------------------------------------
# build_evergreen_index
# ---------------------------------------------------------------------------


def _seed_index_fixtures(temp_vault):
    """Minimal vault: 3 evergreens across 1 pack, with summaries +
    claims, so the index builder has something realistic to flatten.
    """
    eg = temp_vault / "10-Knowledge" / "Evergreen"
    eg.mkdir(parents=True, exist_ok=True)
    for slug, body in (
        ("alpha", "Alpha is foundational.\n"),
        ("beta", "Beta extends alpha.\n"),
        ("gamma", "Gamma is the third.\n"),
    ):
        (eg / f"{slug.title()}.md").write_text(
            f"---\nnote_id: {slug}\ntitle: {slug.title()}\n"
            f"type: evergreen\nentity_type: concept\ndate: 2026-04-13\n"
            f"---\n\n# {slug.title()}\n\n{body}",
            encoding="utf-8",
        )
    from ovp_pipeline.knowledge_index import rebuild_knowledge_index
    rebuild_knowledge_index(temp_vault)


def _truth_pack_name():
    """Every test fixture lands in the default truth pack — return its
    canonical name so assertions don't hard-code it."""
    from ovp_pipeline.knowledge_index import _truth_pack_name
    return _truth_pack_name(None)


def test_index_returns_one_entry_per_object_ordered_by_slug(temp_vault):
    from ovp_pipeline.absorb_router import build_evergreen_index

    _seed_index_fixtures(temp_vault)
    entries = build_evergreen_index(temp_vault)

    slugs = [e.slug for e in entries]
    assert slugs == sorted(slugs), "index must be ordered by slug for determinism"
    assert "alpha" in slugs and "beta" in slugs and "gamma" in slugs


def test_index_falls_back_to_title_when_no_summary(temp_vault):
    """Pre-rebuild compiled_summaries is sometimes empty; index still
    needs to render a useful row.  Falls back to ``title``."""
    from ovp_pipeline.absorb_router import build_evergreen_index
    from ovp_pipeline.runtime import VaultLayout

    _seed_index_fixtures(temp_vault)
    # Wipe summaries to simulate the pre-summary state.
    db = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM compiled_summaries")
        conn.commit()

    entries = build_evergreen_index(temp_vault)
    alpha = next(e for e in entries if e.slug == "alpha")
    # Summary is non-empty (uses title fallback), not empty string.
    assert alpha.summary
    assert "Alpha" in alpha.summary


def test_index_truncates_long_summary_to_budget(temp_vault):
    from ovp_pipeline.absorb_router import build_evergreen_index
    from ovp_pipeline.runtime import VaultLayout

    _seed_index_fixtures(temp_vault)
    long_summary = "alpha " * 500  # ~3000 chars
    db = VaultLayout.from_vault(temp_vault).knowledge_db
    pack = _truth_pack_name()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE compiled_summaries SET summary_text = ? "
            "WHERE pack = ? AND object_id = ?",
            (long_summary, pack, "alpha"),
        )
        conn.commit()

    entries = build_evergreen_index(temp_vault, max_summary_chars=120)
    alpha = next(e for e in entries if e.slug == "alpha")
    assert len(alpha.summary) <= 120
    # Truncation marker present so the prompt-side reader knows it's cut.
    assert alpha.summary.endswith("…")


def test_index_caps_claims_per_object(temp_vault):
    """Even when an object has many claims, the index only carries
    ``max_claims_per_object`` of them so the prompt stays compact."""
    from ovp_pipeline.absorb_router import build_evergreen_index
    from ovp_pipeline.runtime import VaultLayout

    _seed_index_fixtures(temp_vault)
    db = VaultLayout.from_vault(temp_vault).knowledge_db
    pack = _truth_pack_name()
    with sqlite3.connect(db) as conn:
        # Wipe claims for alpha then reseed 8 deterministic ones.
        conn.execute(
            "DELETE FROM claims WHERE pack = ? AND object_id = 'alpha'",
            (pack,),
        )
        conn.executemany(
            "INSERT INTO claims (pack, claim_id, object_id, claim_kind, "
            "claim_text, confidence) VALUES (?, ?, 'alpha', 'fact', ?, 0.9)",
            [(pack, f"alpha::c{i:02d}", f"alpha claim {i}") for i in range(8)],
        )
        conn.commit()

    entries = build_evergreen_index(temp_vault, max_claims_per_object=3)
    alpha = next(e for e in entries if e.slug == "alpha")
    assert len(alpha.key_claims) == 3
    # Lexicographic sort on claim_id picks c00 / c01 / c02 first.
    assert alpha.key_claims[0].startswith("alpha claim 0")


def test_index_pack_name_filter_excludes_other_packs(temp_vault):
    """Setting ``pack_name`` scopes the index to one pack."""
    from ovp_pipeline.absorb_router import build_evergreen_index

    _seed_index_fixtures(temp_vault)
    pack = _truth_pack_name()
    same_pack = build_evergreen_index(temp_vault, pack_name=pack)
    other_pack = build_evergreen_index(temp_vault, pack_name="nonexistent-pack")

    assert {e.slug for e in same_pack} >= {"alpha", "beta", "gamma"}
    assert other_pack == []


def test_index_empty_when_db_missing(tmp_path):
    """Best-effort: vault without a knowledge.db returns ``[]`` instead
    of raising."""
    from ovp_pipeline.absorb_router import build_evergreen_index

    fresh_vault = tmp_path / "fresh_vault"
    fresh_vault.mkdir()
    assert build_evergreen_index(fresh_vault) == []


# ---------------------------------------------------------------------------
# render_index_for_prompt
# ---------------------------------------------------------------------------


def test_render_index_for_prompt_emits_compact_markdown():
    from ovp_pipeline.absorb_router import IndexEntry, render_index_for_prompt

    rendered = render_index_for_prompt([
        IndexEntry(
            slug="structured-outputs-llm",
            title="Structured outputs from LLMs",
            entity_type="method",
            summary="JSON-Schema-as-grammar for parser-friendly LLM output.",
            key_claims=("Reduces parser failure rate", "Cheaper than retry"),
        ),
        IndexEntry(
            slug="alpha",
            title="Alpha",
            entity_type="concept",
            summary="Alpha",  # title-fallback case
            key_claims=(),
        ),
    ])

    assert "`structured-outputs-llm` (method)" in rendered
    assert "Structured outputs from LLMs" in rendered
    assert "JSON-Schema-as-grammar" in rendered
    assert "  - Reduces parser failure rate" in rendered
    assert "  - Cheaper than retry" in rendered
    # Title-fallback case: summary == title → no duplicate ``— Alpha — Alpha``.
    assert "— Alpha — Alpha" not in rendered


# ---------------------------------------------------------------------------
# parse_router_response — happy path
# ---------------------------------------------------------------------------


_GOLDEN_RESPONSE = json.dumps({
    "source_value_summary": "Article on Q3 LLM eval methodology shifts.",
    "updates": [
        {
            "slug": "llm-eval-leakage",
            "rationale": "Source paragraphs 5-9 add new evidence on test contamination.",
            "evidence_segments": ["para 5-9", "section 'Why benchmarks lie'"],
        },
    ],
    "creates": [
        {
            "title": "Judge model bias in eval",
            "kind": "tradeoff",
            "rationale": "No existing evergreen covers judge-model bias as a phenomenon.",
            "evidence_segments": ["section 'When judges disagree'"],
        },
    ],
    "skip_reason": "",
})


def test_parse_response_happy_path():
    from ovp_pipeline.absorb_router import parse_router_response

    decision = parse_router_response(_GOLDEN_RESPONSE)
    assert decision.source_value_summary.startswith("Article on Q3")
    assert len(decision.updates) == 1
    assert decision.updates[0].slug == "llm-eval-leakage"
    assert decision.updates[0].evidence_segments == (
        "para 5-9", "section 'Why benchmarks lie'",
    )
    assert len(decision.creates) == 1
    assert decision.creates[0].title == "Judge model bias in eval"
    assert decision.creates[0].kind == "tradeoff"
    assert decision.is_skip is False


def test_parse_response_skip_only():
    """``skip_reason`` set + empty updates/creates is a valid decision."""
    from ovp_pipeline.absorb_router import parse_router_response

    decision = parse_router_response(json.dumps({
        "source_value_summary": "Promotional landing page; no claims.",
        "updates": [],
        "creates": [],
        "skip_reason": "Source is marketing copy with no extractable claims.",
    }))
    assert decision.is_skip is True
    assert decision.updates == ()
    assert decision.creates == ()


# ---------------------------------------------------------------------------
# parse_router_response — tolerant on cosmetic LLM quirks
# ---------------------------------------------------------------------------


def test_parse_response_strips_markdown_fence():
    """LLMs sometimes wrap JSON in ```json ... ``` despite being told
    not to.  Strip it rather than reject."""
    from ovp_pipeline.absorb_router import parse_router_response

    fenced = "```json\n" + _GOLDEN_RESPONSE + "\n```"
    decision = parse_router_response(fenced)
    assert decision.updates[0].slug == "llm-eval-leakage"


def test_parse_response_evidence_segments_accepts_single_string():
    """Liberal in what we accept — a single-string ``evidence_segments``
    should not break a perfectly good routing decision."""
    from ovp_pipeline.absorb_router import parse_router_response

    payload = json.dumps({
        "source_value_summary": "x",
        "updates": [{
            "slug": "alpha",
            "rationale": "ok",
            "evidence_segments": "para 5",  # single string, not a list
        }],
        "creates": [],
        "skip_reason": "",
    })
    decision = parse_router_response(payload)
    assert decision.updates[0].evidence_segments == ("para 5",)


def test_parse_response_substitutes_placeholder_for_missing_rationale():
    """An update entry with empty rationale logs a warning but is
    accepted with a placeholder — rationale is for human review, not
    routing correctness."""
    from ovp_pipeline.absorb_router import parse_router_response

    payload = json.dumps({
        "source_value_summary": "x",
        "updates": [{"slug": "alpha", "rationale": "", "evidence_segments": []}],
        "creates": [],
        "skip_reason": "",
    })
    decision = parse_router_response(payload)
    assert decision.updates[0].slug == "alpha"
    assert "no rationale" in decision.updates[0].rationale.lower()


def test_parse_response_create_kind_defaults_to_concept():
    from ovp_pipeline.absorb_router import parse_router_response

    payload = json.dumps({
        "source_value_summary": "x",
        "updates": [],
        "creates": [{"title": "Some new idea", "rationale": "novel"}],
        "skip_reason": "",
    })
    decision = parse_router_response(payload)
    assert decision.creates[0].kind == "concept"


# ---------------------------------------------------------------------------
# parse_router_response — strict on real shape errors
# ---------------------------------------------------------------------------


def test_parse_response_rejects_empty_input():
    from ovp_pipeline.absorb_router import RouterResponseError, parse_router_response

    with pytest.raises(RouterResponseError, match=r"empty"):
        parse_router_response("")


def test_parse_response_rejects_input_with_no_json_object():
    """When no ``{...}`` span exists at all the regex extractor
    produces a clear "no JSON object found" error.  Distinct from
    the bad-JSON case below."""
    from ovp_pipeline.absorb_router import RouterResponseError, parse_router_response

    with pytest.raises(RouterResponseError, match=r"no JSON object found"):
        parse_router_response("Sorry, I cannot help with that request.")


def test_parse_response_rejects_invalid_json_inside_braces():
    """A ``{...}`` span exists but the contents don't lex as JSON —
    the parser surfaces ``json.JSONDecodeError`` wrapped in our
    error type."""
    from ovp_pipeline.absorb_router import RouterResponseError, parse_router_response

    with pytest.raises(RouterResponseError, match=r"not valid JSON"):
        parse_router_response("{this is not valid json content}")


def test_parse_response_tolerates_conversational_preamble():
    """Real LLMs sometimes prefix their JSON with ``Here is the JSON:``
    or similar.  The regex extraction picks the first ``{...}`` span
    and ignores prose around it."""
    from ovp_pipeline.absorb_router import parse_router_response

    wrapped = (
        "Sure — here's the routing decision you asked for:\n\n"
        + _GOLDEN_RESPONSE
        + "\n\nLet me know if you want me to refine."
    )
    decision = parse_router_response(wrapped)
    assert decision.updates[0].slug == "llm-eval-leakage"


def test_parse_response_rejects_update_without_slug():
    from ovp_pipeline.absorb_router import RouterResponseError, parse_router_response

    payload = json.dumps({
        "source_value_summary": "x",
        "updates": [{"rationale": "but no slug", "evidence_segments": []}],
        "creates": [],
        "skip_reason": "",
    })
    with pytest.raises(RouterResponseError, match=r"updates\[0\]\.slug is empty"):
        parse_router_response(payload)


def test_parse_response_rejects_create_without_title():
    from ovp_pipeline.absorb_router import RouterResponseError, parse_router_response

    payload = json.dumps({
        "source_value_summary": "x",
        "updates": [],
        "creates": [{"rationale": "ok", "kind": "concept"}],
        "skip_reason": "",
    })
    with pytest.raises(RouterResponseError, match=r"creates\[0\]\.title is empty"):
        parse_router_response(payload)


def test_parse_response_rejects_all_empty_decision():
    """A response with no updates, no creates, AND no skip_reason is a
    router malfunction — surface it instead of silently treating as
    skip."""
    from ovp_pipeline.absorb_router import RouterResponseError, parse_router_response

    payload = json.dumps({
        "source_value_summary": "x",
        "updates": [],
        "creates": [],
        "skip_reason": "",
    })
    with pytest.raises(RouterResponseError, match=r"empty"):
        parse_router_response(payload)
