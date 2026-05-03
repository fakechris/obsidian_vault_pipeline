"""Tests for PR-E4: GitHubSignalProvider's entity-table fast path.

Mirrors the test pattern from test_author_rules_entity_fallback.py
(which proved AuthorRulesProvider's entity-table integration).

Three invariants:
  * entity hit returns immediately (no live API call)
  * entity miss falls through to live fetch (back-compat)
  * no entity_store_path is a no-op (PR-D2 behavior preserved)
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch

from ovp_pipeline.entities.store import EntityStore
from ovp_pipeline.source_signals.github import GitHubSignalProvider


def _seed_project(store, identity, authority):
    store.upsert(
        entity_type="github_project",
        identity_key=identity,
        canonical_name=identity,
        signals={"stargazers_count": 10000},
        derived_authority=authority,
        fetch_source="github_rest",
    )


def _seed_user(store, login, authority):
    store.upsert(
        entity_type="github_user",
        identity_key=login,
        canonical_name=login,
        signals={"followers": 5000},
        derived_authority=authority,
        fetch_source="github_rest",
    )


def _fake_response(status: int, body):
    raw = body if isinstance(body, str) else json.dumps(body)
    f = io.BytesIO(raw.encode("utf-8"))
    f.status = status
    return f


class TestEntityHitShortCircuitsLiveAPI:
    def test_project_hit_returns_signal_without_api_call(self, tmp_path):
        db = tmp_path / "k.db"
        store = EntityStore(db_path=db)
        _seed_project(store, "karpathy/nanogpt", 0.92)

        provider = GitHubSignalProvider(entity_store_path=db)
        # Patch urlopen — if the fast path works, this is never called.
        with patch(
            "ovp_pipeline.source_signals.github.urllib.request.urlopen"
        ) as m:
            sig = provider.score("https://github.com/karpathy/nanoGPT", {})
        assert sig is not None
        assert sig.value == 0.92
        assert sig.raw["matched_via"] == "entity_table"
        assert sig.raw["entity_source"] == "github_project"
        # Critical: no live HTTP request was made.
        m.assert_not_called()

    def test_owner_fallback_uses_capped_score(self, tmp_path):
        # No project entity, but the owner is known.  Resolver caps
        # the owner-fallback at 0.55 (see entities/resolver.py); the
        # provider should surface that cap, not the raw user score.
        db = tmp_path / "k.db"
        store = EntityStore(db_path=db)
        _seed_user(store, "karpathy", 0.65)

        provider = GitHubSignalProvider(entity_store_path=db)
        with patch(
            "ovp_pipeline.source_signals.github.urllib.request.urlopen"
        ) as m:
            sig = provider.score("https://github.com/karpathy/dotfiles", {})
        assert sig is not None
        assert sig.value == 0.55
        assert sig.raw["entity_source"] == "github_user"
        m.assert_not_called()


class TestEntityMissFallsThroughToLive:
    def test_unknown_repo_hits_live_api(self, tmp_path):
        # Empty entity store — provider should fall through to live
        # fetch (PR-D2 behavior).
        db = tmp_path / "k.db"
        EntityStore(db_path=db)        # init schema only

        provider = GitHubSignalProvider(entity_store_path=db)
        with patch(
            "ovp_pipeline.source_signals.github.urllib.request.urlopen"
        ) as m:
            m.return_value.__enter__.return_value = _fake_response(
                200, {"stargazers_count": 1234, "forks_count": 56,
                      "pushed_at": "2026-04-01T00:00:00Z"},
            )
            sig = provider.score("https://github.com/freshrepo/x", {})
        assert sig is not None
        # Live formula: 0.40 base + tanh(...) star + recency
        assert 0.40 < sig.value <= 1.0
        # Came from the live path, not the entity table.
        assert sig.raw.get("matched_via") != "entity_table"
        m.assert_called_once()


class TestNoEntityStorePathIsNoOp:
    def test_default_constructor_preserves_pr_d2_behavior(self):
        # No entity_store_path provided → exactly PR-D2 behavior.
        provider = GitHubSignalProvider()
        with patch(
            "ovp_pipeline.source_signals.github.urllib.request.urlopen"
        ) as m:
            m.return_value.__enter__.return_value = _fake_response(
                200, {"stargazers_count": 100, "forks_count": 5,
                      "pushed_at": "2026-04-01T00:00:00Z"},
            )
            sig = provider.score("https://github.com/owner/repo", {})
        assert sig is not None
        assert sig.raw.get("matched_via") != "entity_table"


class TestMissingDbDoesNotCrash:
    def test_nonexistent_db_path_falls_through(self, tmp_path):
        # Provider was wired with a path that doesn't exist (e.g.
        # vault never had the entity layer initialized).  Behavior
        # must be: fall through to live API, NOT raise.
        bogus_db = tmp_path / "does-not-exist.db"
        provider = GitHubSignalProvider(entity_store_path=bogus_db)
        with patch(
            "ovp_pipeline.source_signals.github.urllib.request.urlopen"
        ) as m:
            m.return_value.__enter__.return_value = _fake_response(
                200, {"stargazers_count": 50, "forks_count": 2,
                      "pushed_at": "2026-04-01T00:00:00Z"},
            )
            sig = provider.score("https://github.com/x/y", {})
        # Schema gets auto-created by EntityStore.__post_init__, so
        # the store opens cleanly but has no rows → falls through.
        # Either way, the live API was hit.
        m.assert_called_once()
        assert sig is not None
