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


def test_index_max_entries_truncates_after_sort(temp_vault):
    """BL-068: ``max_entries`` caps the number of evergreens fed to
    the router so the rendered prompt fits the model's context.
    Cap is applied AFTER the lexicographic sort, so the first N
    slugs survive deterministically across runs."""
    from ovp_pipeline.absorb_router import build_evergreen_index

    _seed_index_fixtures(temp_vault)
    # Fixture has alpha, beta, gamma (3 objects).  Cap to 2 → first two.
    entries = build_evergreen_index(temp_vault, max_entries=2)
    assert [e.slug for e in entries] == ["alpha", "beta"]

    # max_entries=None means no cap (legacy behavior, used by tests).
    full = build_evergreen_index(temp_vault, max_entries=None)
    assert len(full) >= 3

    # max_entries=0 keeps the function returning an empty list rather
    # than raising — best-effort contract for misconfigured env var.
    empty = build_evergreen_index(temp_vault, max_entries=0)
    assert empty == []


def test_build_index_priority_slugs_reorders_to_front(temp_vault):
    """BL-069: ``priority_slugs`` moves the supplied slugs to the
    front in the given order; remaining entries keep their default
    lexicographic order behind the priority block.  Combined with
    ``max_entries`` this lets the embedding ranker put topically-
    relevant evergreens above the cap."""
    from ovp_pipeline.absorb_router import build_evergreen_index

    _seed_index_fixtures(temp_vault)
    entries = build_evergreen_index(
        temp_vault,
        priority_slugs=["gamma", "alpha"],
    )
    # gamma first, alpha second (priority order), then beta (alpha-sort).
    assert [e.slug for e in entries] == ["gamma", "alpha", "beta"]


def test_build_index_priority_slugs_combined_with_max_entries(temp_vault):
    """``priority_slugs`` survives ``max_entries`` truncation — the
    priority block is at the front so the cap drops the *trailing*
    alphabetical entries, not the priority ones."""
    from ovp_pipeline.absorb_router import build_evergreen_index

    _seed_index_fixtures(temp_vault)
    entries = build_evergreen_index(
        temp_vault,
        priority_slugs=["gamma"],
        max_entries=2,
    )
    # gamma (priority) + alpha (first non-priority alphabetically).
    assert [e.slug for e in entries] == ["gamma", "alpha"]


def test_build_index_priority_slugs_with_unknown_slug(temp_vault):
    """Slugs in ``priority_slugs`` that don't exist in ``objects``
    are silently dropped — the ranker may have stale slugs."""
    from ovp_pipeline.absorb_router import build_evergreen_index

    _seed_index_fixtures(temp_vault)
    entries = build_evergreen_index(
        temp_vault,
        priority_slugs=["nonexistent-slug", "beta"],
    )
    assert [e.slug for e in entries] == ["beta", "alpha", "gamma"]


def test_resolve_router_index_strategy_reads_env(monkeypatch):
    """BL-069: env var picks strategy; unknown values fall back to
    embedding_topk (default) with a warning rather than silently
    reverting to the worse strategy."""
    from ovp_pipeline.absorb_router import _resolve_router_index_strategy

    monkeypatch.delenv("OVP_ROUTER_INDEX_STRATEGY", raising=False)
    assert _resolve_router_index_strategy() == "embedding_topk"

    monkeypatch.setenv("OVP_ROUTER_INDEX_STRATEGY", "alphabetical")
    assert _resolve_router_index_strategy() == "alphabetical"

    monkeypatch.setenv("OVP_ROUTER_INDEX_STRATEGY", "alpha")
    assert _resolve_router_index_strategy() == "alphabetical"

    monkeypatch.setenv("OVP_ROUTER_INDEX_STRATEGY", "embedding_topk")
    assert _resolve_router_index_strategy() == "embedding_topk"

    monkeypatch.setenv("OVP_ROUTER_INDEX_STRATEGY", "garbage")
    assert _resolve_router_index_strategy() == "embedding_topk"


def test_rank_evergreens_empty_source_returns_empty():
    """Empty / whitespace-only source content → empty ranking.
    Caller treats this as "fall back to alphabetical"."""
    from ovp_pipeline.absorb_router import rank_evergreens_by_source

    assert rank_evergreens_by_source("", "/nonexistent", top_k=10) == []
    assert rank_evergreens_by_source("   \n  ", "/nonexistent", top_k=10) == []
    assert rank_evergreens_by_source("anything", "/nonexistent", top_k=0) == []


def test_rank_evergreens_missing_db_returns_empty(tmp_path):
    """No knowledge.db (fresh vault, never rebuilt) → empty ranking,
    no exception.  Caller falls back to alphabetical."""
    from ovp_pipeline.absorb_router import rank_evergreens_by_source

    assert rank_evergreens_by_source(
        "real content", tmp_path, top_k=10,
    ) == []


def test_rank_evergreens_returns_relevant_slugs_first(temp_vault, monkeypatch):
    """BL-069 happy path: embed source + cosine top-K returns the
    evergreens whose vectors are nearest.  We monkeypatch the
    embedding model to a deterministic stub so the test doesn't
    require Qwen at test-time."""
    from ovp_pipeline.absorb_router import rank_evergreens_by_source
    from ovp_pipeline.runtime import VaultLayout
    import array as _array

    _seed_index_fixtures(temp_vault)
    # Inject deterministic 4-d unit vectors keyed by slug into the
    # page_embeddings table so cosine ranking has known winners.
    db = VaultLayout.from_vault(temp_vault).knowledge_db
    vectors = {
        "alpha": [1.0, 0.0, 0.0, 0.0],  # aligned with source vec
        "beta":  [0.0, 1.0, 0.0, 0.0],  # orthogonal
        "gamma": [0.7071, 0.7071, 0.0, 0.0],  # 45° to source
    }
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM page_embeddings")
        for slug, vec in vectors.items():
            blob = _array.array("f", vec).tobytes()
            conn.execute(
                "INSERT INTO page_embeddings (slug, chunk_index, section_title, "
                "chunk_text, embedding_blob, embedding_model) "
                "VALUES (?, 0, '', '', ?, 'stub-test-4d')",
                (slug, blob),
            )
        conn.commit()

    # Force the embed_text + get_model_name pair to produce a known
    # 4-d source vector with the same model name as the stored rows.
    import ovp_pipeline.embedding as emb
    monkeypatch.setattr(
        emb, "embed_text",
        lambda _text: _array.array("f", [1.0, 0.0, 0.0, 0.0]).tobytes(),
    )
    monkeypatch.setattr(emb, "get_model_name", lambda: "stub-test-4d")

    ranked = rank_evergreens_by_source(
        "anything", temp_vault, top_k=3,
    )
    # alpha (cos=1.0) > gamma (cos=0.707) > beta (cos=0.0)
    assert ranked == ["alpha", "gamma", "beta"]


def test_rank_evergreens_zero_norm_source_returns_empty(temp_vault, monkeypatch):
    """Codex P2 regression: under the BLAKE2b hash fallback,
    Chinese-only sources tokenise to no ``[a-z0-9]+`` matches and
    produce an all-zero embedding.  Without the zero-norm guard
    every dot-product would tie at 0 and route_source would record
    an arbitrary DB order as ``embedding_topk``.  This test pins
    the contract: zero-norm source vector → empty ranking →
    caller falls back to alphabetical."""
    from ovp_pipeline.absorb_router import rank_evergreens_by_source
    from ovp_pipeline.runtime import VaultLayout
    import array as _array

    _seed_index_fixtures(temp_vault)
    db = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM page_embeddings")
        conn.execute(
            "INSERT INTO page_embeddings (slug, chunk_index, section_title, "
            "chunk_text, embedding_blob, embedding_model) "
            "VALUES ('alpha', 0, '', '', ?, 'stub-test-4d')",
            (_array.array("f", [1.0, 0.0, 0.0, 0.0]).tobytes(),),
        )
        conn.commit()

    import ovp_pipeline.embedding as emb
    # Hash backend on Chinese-only source: all-zero vector.
    monkeypatch.setattr(
        emb, "embed_text",
        lambda _text: _array.array("f", [0.0, 0.0, 0.0, 0.0]).tobytes(),
    )
    monkeypatch.setattr(emb, "get_model_name", lambda: "stub-test-4d")

    assert rank_evergreens_by_source("中文", temp_vault, top_k=10) == []


def test_load_evergreen_vectors_l2_normalizes_mean(temp_vault):
    """Bot review HIGH: mean of L2-normalised vectors is NOT itself
    L2-normalised; downstream cosine assumes unit-length so without
    re-normalising the score for multi-chunk evergreens
    systematically under-shoots.  This test seeds two unit-length
    chunks with opposite tilts on the same slug and asserts the
    aggregated vector has unit norm."""
    from ovp_pipeline.absorb_router import _load_evergreen_vectors_for_pack
    from ovp_pipeline.runtime import VaultLayout
    import array as _array
    import math

    _seed_index_fixtures(temp_vault)
    db = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM page_embeddings")
        # Two unit-norm chunks: (1,0,0,0) + (0,1,0,0).  Mean is
        # (0.5, 0.5, 0, 0), norm 1/sqrt(2) ≈ 0.707.  After
        # re-normalisation the vector must have norm 1.0.
        for chunk_idx, vec in enumerate([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
        ]):
            conn.execute(
                "INSERT INTO page_embeddings (slug, chunk_index, section_title, "
                "chunk_text, embedding_blob, embedding_model) "
                "VALUES ('alpha', ?, '', '', ?, 'stub-l2-test')",
                (chunk_idx, _array.array("f", vec).tobytes()),
            )
        conn.commit()

    vectors = _load_evergreen_vectors_for_pack(db, "stub-l2-test", None)
    assert "alpha" in vectors
    vec = vectors["alpha"]
    norm = math.sqrt(sum(x * x for x in vec))
    assert abs(norm - 1.0) < 1e-6, (
        f"aggregated vector must be L2-normalised; got norm={norm}"
    )


def test_rank_evergreens_dimensionality_mismatch_returns_empty(temp_vault, monkeypatch):
    """If the source embedder produces a different dim than the
    stored vectors (model mismatch — hash 128-d on a Qwen 1024-d
    vault), the ranker returns empty so the caller falls back to
    alphabetical rather than producing garbage scores."""
    from ovp_pipeline.absorb_router import rank_evergreens_by_source
    from ovp_pipeline.runtime import VaultLayout
    import array as _array

    _seed_index_fixtures(temp_vault)
    db = VaultLayout.from_vault(temp_vault).knowledge_db
    # Stored: 4-d vectors.
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM page_embeddings")
        conn.execute(
            "INSERT INTO page_embeddings (slug, chunk_index, section_title, "
            "chunk_text, embedding_blob, embedding_model) "
            "VALUES ('alpha', 0, '', '', ?, 'stub-test-4d')",
            (_array.array("f", [1.0, 0.0, 0.0, 0.0]).tobytes(),),
        )
        conn.commit()

    import ovp_pipeline.embedding as emb
    # Source embedder returns 8-d — incompatible with stored 4-d.
    monkeypatch.setattr(
        emb, "embed_text",
        lambda _text: _array.array("f", [0.5] * 8).tobytes(),
    )
    monkeypatch.setattr(emb, "get_model_name", lambda: "stub-test-4d")

    assert rank_evergreens_by_source("x", temp_vault, top_k=10) == []


def test_resolve_max_index_entries_reads_env(monkeypatch):
    """BL-068: ``OVP_ROUTER_MAX_INDEX_ENTRIES`` env var overrides
    the default cap.  Invalid values fall back to default rather
    than raising."""
    from ovp_pipeline.absorb_router import (
        DEFAULT_MAX_INDEX_ENTRIES,
        _resolve_max_index_entries,
    )

    monkeypatch.delenv("OVP_ROUTER_MAX_INDEX_ENTRIES", raising=False)
    assert _resolve_max_index_entries() == DEFAULT_MAX_INDEX_ENTRIES

    monkeypatch.setenv("OVP_ROUTER_MAX_INDEX_ENTRIES", "500")
    assert _resolve_max_index_entries() == 500

    monkeypatch.setenv("OVP_ROUTER_MAX_INDEX_ENTRIES", "not-a-number")
    assert _resolve_max_index_entries() == DEFAULT_MAX_INDEX_ENTRIES

    monkeypatch.setenv("OVP_ROUTER_MAX_INDEX_ENTRIES", "-5")
    assert _resolve_max_index_entries() == DEFAULT_MAX_INDEX_ENTRIES


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


# ---------------------------------------------------------------------------
# build_router_user_prompt
# ---------------------------------------------------------------------------


def test_build_user_prompt_renders_source_and_index():
    from ovp_pipeline.absorb_router import (
        IndexEntry,
        build_router_user_prompt,
    )

    prompt_text = build_router_user_prompt(
        source_path="50-Inbox/foo.md",
        source_content="Article body about LLM evals.",
        index=[
            IndexEntry(
                slug="llm-eval-leakage",
                title="LLM eval leakage",
                entity_type="failure_mode",
                summary="Test contamination via memorisation.",
                key_claims=("Models memorise public benchmarks.",),
            ),
        ],
    )
    assert "50-Inbox/foo.md" in prompt_text
    assert "<source>" in prompt_text and "</source>" in prompt_text
    assert "Article body about LLM evals." in prompt_text
    assert "`llm-eval-leakage` (failure_mode)" in prompt_text
    assert "Test contamination via memorisation." in prompt_text


def test_build_user_prompt_handles_empty_index():
    """Empty index → tell the router so it doesn't waste turns."""
    from ovp_pipeline.absorb_router import build_router_user_prompt

    prompt_text = build_router_user_prompt(
        source_path="x.md",
        source_content="body",
        index=[],
    )
    # Specific phrasing isn't critical; the fact that we explain
    # "no evergreens yet" + tell the router to use ``creates`` only is.
    assert "creates" in prompt_text
    # No bogus "vault has these slugs" block when there's nothing.
    assert "已有的 evergreen 索引" not in prompt_text


def test_build_user_prompt_truncates_long_source():
    from ovp_pipeline.absorb_router import build_router_user_prompt

    huge_body = "x" * 200_000
    prompt_text = build_router_user_prompt(
        source_path="x.md",
        source_content=huge_body,
        index=[],
        max_source_chars=500,
    )
    # 500-char body + scaffolding ≪ 200K
    assert len(prompt_text) < 5000


# ---------------------------------------------------------------------------
# route_source — happy path + audit emission
# ---------------------------------------------------------------------------


class _FakeLogger:
    """Minimal PipelineLogger stub — collects emitted audit rows
    in memory so assertions can read them back."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def log(self, event_type: str, data: dict):
        self.events.append((event_type, dict(data)))


class _FakeLLM:
    """Returns a canned response.  Tests can override per-instance."""

    def __init__(self, response_text: str):
        self.response_text = response_text
        self.calls: list[dict] = []

    def generate(self, *, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        self.calls.append({
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "max_tokens": max_tokens,
        })
        return self.response_text


def test_route_source_happy_path_emits_ok_audit():
    from ovp_pipeline.absorb_router import (
        ABSORB_ROUTE_DECISION_EVENT,
        IndexEntry,
        ROUTE_STATUS_OK,
        route_source,
    )

    llm = _FakeLLM(_GOLDEN_RESPONSE)
    audit = _FakeLogger()
    decision = route_source(
        llm,
        source_path="x.md",
        source_content="body",
        pipeline_logger=audit,
        index=[
            IndexEntry(
                slug="llm-eval-leakage",
                title="LLM eval leakage",
                entity_type="failure_mode",
                summary="Test contamination.",
            ),
        ],
    )

    # Decision returned + LLM was actually called once with both
    # system + user prompts populated.
    assert decision is not None
    assert decision.updates[0].slug == "llm-eval-leakage"
    assert len(llm.calls) == 1
    assert llm.calls[0]["system_prompt"]   # system from registry
    assert "x.md" in llm.calls[0]["user_prompt"]

    # Audit row carries the right shape.
    event_type, payload = audit.events[-1]
    assert event_type == ABSORB_ROUTE_DECISION_EVENT
    assert payload["status"] == ROUTE_STATUS_OK
    assert payload["update_count"] == 1
    assert payload["create_count"] == 1
    assert payload["update_slugs"] == ["llm-eval-leakage"]
    assert payload["prompt_version"] == "v2_router"


def test_route_source_skip_decision_emits_skip_status():
    from ovp_pipeline.absorb_router import (
        ROUTE_STATUS_SKIP,
        route_source,
    )

    skip_response = json.dumps({
        "source_value_summary": "Promotional landing page",
        "updates": [],
        "creates": [],
        "skip_reason": "Marketing copy with no extractable claims.",
    })
    llm = _FakeLLM(skip_response)
    audit = _FakeLogger()
    decision = route_source(
        llm,
        source_path="x.md",
        source_content="body",
        pipeline_logger=audit,
        index=[],
    )

    assert decision is not None
    assert decision.is_skip
    assert audit.events[-1][1]["status"] == ROUTE_STATUS_SKIP
    assert audit.events[-1][1]["skip_reason"].startswith("Marketing")


def test_route_source_parse_error_returns_none_emits_audit():
    """Malformed router response → returns ``None`` (caller falls
    back) AND emits a ``parse_error`` audit row with a snippet of
    the offending response.  Does NOT raise."""
    from ovp_pipeline.absorb_router import (
        ROUTE_STATUS_PARSE_ERROR,
        route_source,
    )

    llm = _FakeLLM("Sorry I cannot help with that.")  # no JSON object
    audit = _FakeLogger()
    decision = route_source(
        llm,
        source_path="x.md",
        source_content="body",
        pipeline_logger=audit,
        index=[],
    )

    assert decision is None
    event_type, payload = audit.events[-1]
    assert payload["status"] == ROUTE_STATUS_PARSE_ERROR
    assert "no JSON object" in payload["error"]
    # Snippet is bounded so a runaway response can't flood the log.
    assert len(payload["raw_snippet"]) <= 240


def test_route_source_llm_failure_returns_none_emits_audit():
    """LLM client raising (network, auth, rate limit, context-cap) →
    returns ``None`` with a ``request_error`` audit so dashboards
    can tell provider failures apart from JSON-parsing failures."""
    from ovp_pipeline.absorb_router import (
        ROUTE_STATUS_REQUEST_ERROR,
        route_source,
    )

    class _RaisingLLM:
        def generate(self, **_kwargs):
            raise RuntimeError("simulated rate limit")

    audit = _FakeLogger()
    decision = route_source(
        _RaisingLLM(),
        source_path="x.md",
        source_content="body",
        pipeline_logger=audit,
        index=[],
    )
    assert decision is None
    payload = audit.events[-1][1]
    assert payload["status"] == ROUTE_STATUS_REQUEST_ERROR
    assert "simulated rate limit" in payload["error"]


def test_route_source_empty_response_returns_none_emits_audit():
    """LLM returns 200 with empty body → ``empty_response`` audit
    (distinct from request errors AND from parse failures)."""
    from ovp_pipeline.absorb_router import (
        ROUTE_STATUS_EMPTY_RESPONSE,
        route_source,
    )

    audit = _FakeLogger()
    decision = route_source(
        _FakeLLM(""),
        source_path="x.md",
        source_content="body",
        pipeline_logger=audit,
        index=[],
    )
    assert decision is None
    payload = audit.events[-1][1]
    assert payload["status"] == ROUTE_STATUS_EMPTY_RESPONSE
    assert payload["error"] == "empty router response"


def test_route_source_prompt_registry_failure_emits_specific_status(monkeypatch):
    """Missing/broken prompt template → ``prompt_registry_error``
    (OVP-side config bug, distinct from provider problems).
    Test patches ``get_prompt`` to simulate a registry KeyError."""
    from ovp_pipeline import absorb_router
    from ovp_pipeline.absorb_router import (
        ROUTE_STATUS_PROMPT_REGISTRY_ERROR,
        route_source,
    )

    def _broken_get_prompt(*_a, **_kw):
        raise KeyError("prompt 'absorb_router' missing from registry")

    monkeypatch.setattr(
        "ovp_pipeline.prompt_registry.get_prompt", _broken_get_prompt,
    )

    audit = _FakeLogger()
    decision = route_source(
        _FakeLLM("won't be called"),
        source_path="x.md",
        source_content="body",
        pipeline_logger=audit,
        index=[],
    )
    assert decision is None
    payload = audit.events[-1][1]
    assert payload["status"] == ROUTE_STATUS_PROMPT_REGISTRY_ERROR
    assert "prompt 'absorb_router' missing" in payload["error"]


def test_route_source_requires_index_or_vault_dir():
    from ovp_pipeline.absorb_router import route_source

    with pytest.raises(ValueError, match=r"index.*vault_dir"):
        route_source(
            _FakeLLM(""),
            source_path="x.md",
            source_content="body",
            pipeline_logger=_FakeLogger(),
        )
