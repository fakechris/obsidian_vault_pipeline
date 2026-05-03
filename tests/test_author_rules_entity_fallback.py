"""Tests for AuthorRulesProvider's PR-E3 entity-table fallback.

The whitelist behavior remains untouched.  These tests cover the
new code path: when ``entity_store_path`` is set and a candidate
handle isn't in the whitelist, look it up in the entity table.
"""

from __future__ import annotations

import json

from ovp_pipeline.entities.store import EntityStore
from ovp_pipeline.source_signals.author_rules import AuthorRulesProvider


def _empty_jsonl(tmp_path):
    p = tmp_path / "authors.jsonl"
    p.write_text("", encoding="utf-8")
    return p


def _whitelist_jsonl(tmp_path, *records):
    p = tmp_path / "authors.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return p


def _seed_twitter(store, handle, auth):
    store.upsert(
        entity_type="twitter_author", identity_key=handle, canonical_name=handle,
        signals={}, derived_authority=auth, fetch_source="t",
    )


class TestEntityFallback:
    def test_fallback_used_when_handle_not_in_whitelist(self, tmp_path):
        db = tmp_path / "k.db"
        store = EntityStore(db_path=db)
        _seed_twitter(store, "newperson", 0.60)
        provider = AuthorRulesProvider(
            authors_path=_empty_jsonl(tmp_path),
            entity_store_path=db,
        )
        sig = provider.score("https://x.com/newperson/status/1", {})
        assert sig is not None
        # Score is the entity's authority × multiplier (0.85 default)
        assert sig.value == round(0.60 * 0.85, 4)
        assert sig.raw["matched_via"] == "entity_table"
        assert sig.raw["entity_source"] == "twitter_author"

    def test_whitelist_still_wins_when_present(self, tmp_path):
        # Curated whitelist must outrank entity-table data.
        db = tmp_path / "k.db"
        store = EntityStore(db_path=db)
        _seed_twitter(store, "shared", 0.70)
        whitelist = _whitelist_jsonl(
            tmp_path, {"handle": "shared", "authority": 0.95},
        )
        provider = AuthorRulesProvider(
            authors_path=whitelist,
            entity_store_path=db,
        )
        sig = provider.score("https://x.com/shared/status/1", {})
        assert sig is not None
        # Whitelist gives 0.95 directly; entity fallback would give
        # 0.70 * 0.85 = 0.595 — confirm whitelist wins.
        assert sig.value == 0.95
        assert "matched_via" not in sig.raw or sig.raw.get("matched_via") != "entity_table"

    def test_fallback_returns_none_when_no_entity_store(self, tmp_path):
        # No entity_store_path → behavior unchanged from PR-D1.
        provider = AuthorRulesProvider(
            authors_path=_empty_jsonl(tmp_path),
            # entity_store_path defaults to None
        )
        sig = provider.score("https://x.com/newperson/status/1", {})
        assert sig is None

    def test_fallback_returns_none_when_handle_unknown(self, tmp_path):
        db = tmp_path / "k.db"
        # Empty store — no entity for "ghost"
        EntityStore(db_path=db)
        provider = AuthorRulesProvider(
            authors_path=_empty_jsonl(tmp_path),
            entity_store_path=db,
        )
        sig = provider.score("https://x.com/ghost/status/1", {})
        assert sig is None

    def test_multiplier_dampens_entity_score(self, tmp_path):
        # A custom multiplier of 1.0 disables the dampening — useful
        # when the user trusts entity data as much as the whitelist.
        db = tmp_path / "k.db"
        store = EntityStore(db_path=db)
        _seed_twitter(store, "newperson", 0.6)
        provider = AuthorRulesProvider(
            authors_path=_empty_jsonl(tmp_path),
            entity_store_path=db,
            entity_score_multiplier=1.0,
        )
        sig = provider.score("https://x.com/newperson/status/1", {})
        assert sig is not None
        assert sig.value == 0.6
