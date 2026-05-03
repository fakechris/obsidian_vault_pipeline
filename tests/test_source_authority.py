"""Tests for the source-authority subsystem.

Coverage:
  * Each provider's ``applies`` + ``score`` for canonical inputs
  * Orchestrator combination math (geometric-mean + domain floor)
  * SQLite persistence + idempotent schema migration
  * Stub providers correctly return None when their backend isn't set
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from ovp_pipeline.source_authority import (
    AuthorityScore,
    _combine,
    ensure_schema,
    score_source,
    upsert_score,
)
from ovp_pipeline.source_signals import (
    ArxivSignalProvider,
    AuthorRulesProvider,
    DomainRulesProvider,
    GitHubSignalProvider,
    Signal,
    SubstackSignalProvider,
    TwitterSignalProvider,
)


# ---------------------------------------------------------------------------
# DomainRulesProvider
# ---------------------------------------------------------------------------


class TestDomainRules:
    def test_canonical_domain_high_score(self):
        p = DomainRulesProvider()
        sig = p.score("https://www.anthropic.com/news/claude-3-opus", {})
        assert sig is not None
        assert sig.value >= 0.85
        assert sig.raw["bucket"] == "canonical"

    def test_unknown_domain_default(self):
        p = DomainRulesProvider()
        sig = p.score("https://random-blog.example.com/post", {})
        assert sig is not None
        assert 0.4 <= sig.value <= 0.5
        assert sig.raw["bucket"] == "unknown"

    def test_subdomain_match(self):
        p = DomainRulesProvider()
        sig = p.score("https://blog.huggingface.co/post", {})
        assert sig is not None
        # Subdomain takes a small penalty vs base domain
        assert sig.value > 0.8

    def test_path_override_no_longer_lifts_unknown_domain(self):
        """After PR review: ``/blog/`` on an unrecognized domain stays
        at the unknown-domain default — only hosts in the explicit
        ``_TRUSTED_BLOG_HOSTS`` set get the canonical-blog bump.
        """
        p = DomainRulesProvider()
        sig = p.score("https://random-startup.example.com/blog/launch", {})
        assert sig is not None
        assert sig.value < 0.55  # falls back to unknown default
        assert sig.raw["bucket"] == "unknown"

    def test_github_orgs_path_dampener_still_fires(self):
        """The host-scoped override for github.com /orgs/ pages still
        works — those are listings, never canonical artifacts.
        """
        p = DomainRulesProvider()
        sig = p.score("https://github.com/orgs/anthropic", {})
        assert sig is not None
        assert sig.value <= 0.10
        assert "github org listing" in sig.raw["reason"]

    def test_multipart_tld_subdomain_matched_correctly(self):
        """``blog.huggingface.co`` should match ``huggingface.co``,
        not be misclassified as ``co.uk``-style.  After PR review the
        registrable-domain logic handles ``.co.uk`` etc.
        """
        from ovp_pipeline.source_signals.domain_rules import (
            _extract_registrable_domain,
        )
        assert _extract_registrable_domain("blog.example.co.uk") == "example.co.uk"
        assert _extract_registrable_domain("blog.example.com") == "example.com"
        assert _extract_registrable_domain("example.com") == "example.com"
        assert _extract_registrable_domain("a.b.c.example.co.jp") == "example.co.jp"

    def test_does_not_apply_to_non_http(self):
        p = DomainRulesProvider()
        assert not p.applies("file:///tmp/local.md", {})
        assert not p.applies("", {})


# ---------------------------------------------------------------------------
# AuthorRulesProvider
# ---------------------------------------------------------------------------


@pytest.fixture
def authors_file(tmp_path):
    f = tmp_path / "authors.jsonl"
    f.write_text(
        '\n'.join([
            json.dumps({"handle": "karpathy", "aliases": ["andrej karpathy"], "authority": 0.95}),
            json.dumps({"handle": "akshay_pachaar", "authority": 0.75}),
        ]),
        encoding="utf-8",
    )
    return f


class TestAuthorRules:
    def test_exact_handle_match(self, authors_file):
        p = AuthorRulesProvider(authors_path=authors_file)
        sig = p.score("https://x.com/karpathy/status/12345", {})
        assert sig is not None
        assert sig.value == 0.95
        assert sig.raw["matched"] == "karpathy"

    def test_frontmatter_alias_match(self, authors_file):
        p = AuthorRulesProvider(authors_path=authors_file)
        sig = p.score("", {"author": "Andrej Karpathy"})
        assert sig is not None
        assert sig.value == 0.95

    def test_unknown_author_returns_none(self, authors_file):
        p = AuthorRulesProvider(authors_path=authors_file)
        sig = p.score("https://x.com/nobody_42/status/1", {})
        assert sig is None

    def test_missing_file_returns_none(self, tmp_path):
        p = AuthorRulesProvider(authors_path=tmp_path / "nonexistent.jsonl")
        sig = p.score("https://x.com/karpathy/status/1", {})
        assert sig is None

    def test_substring_match_softer_score(self, authors_file):
        p = AuthorRulesProvider(authors_path=authors_file)
        sig = p.score("", {"author": "Dr. Andrej Karpathy of OpenAI"})
        assert sig is not None
        # Substring match takes 0.9× penalty
        assert sig.value == pytest.approx(0.95 * 0.9, abs=0.001)


# ---------------------------------------------------------------------------
# GitHubSignalProvider — mock the network
# ---------------------------------------------------------------------------


class TestGitHubProvider:
    def test_does_not_apply_to_non_github(self):
        p = GitHubSignalProvider()
        assert not p.applies("https://example.com/repo", {})
        assert not p.applies("https://github.com/", {})  # no owner/repo

    def test_score_for_high_star_repo(self):
        p = GitHubSignalProvider()
        canned = json.dumps({
            "stargazers_count": 50000,
            "forks_count": 5000,
            "archived": False,
            "pushed_at": "2026-04-30T00:00:00Z",
        }).encode()
        with patch(
            "ovp_pipeline.source_signals.github.urllib.request.urlopen",
        ) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = canned
            sig = p.score("https://github.com/torvalds/linux", {})
        assert sig is not None
        assert sig.value > 0.85   # high stars + recent
        assert sig.raw["stars"] == 50000

    def test_archived_repo_loses_recency(self):
        p = GitHubSignalProvider()
        canned = json.dumps({
            "stargazers_count": 1000,
            "forks_count": 50,
            "archived": True,
            "pushed_at": "2024-01-01T00:00:00Z",
        }).encode()
        with patch(
            "ovp_pipeline.source_signals.github.urllib.request.urlopen",
        ) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = canned
            sig = p.score("https://github.com/foo/bar", {})
        assert sig is not None
        assert sig.raw["archived"] is True


# ---------------------------------------------------------------------------
# Stub providers must return None until backend env is set
# ---------------------------------------------------------------------------


class TestStubProvidersAreSilent:
    def test_twitter_stub_returns_none_without_backend(self, monkeypatch):
        monkeypatch.delenv("OVP_TWITTER_BACKEND", raising=False)
        p = TwitterSignalProvider()
        assert p.applies("https://x.com/karpathy/status/123", {})  # interface still matches
        assert p.score("https://x.com/karpathy/status/123", {}) is None

    def test_substack_stub_returns_none_without_backend(self, monkeypatch):
        monkeypatch.delenv("OVP_SUBSTACK_BACKEND", raising=False)
        p = SubstackSignalProvider()
        assert p.applies("https://thoughts.substack.com/p/post", {})
        assert p.score("https://thoughts.substack.com/p/post", {}) is None

    def test_twitter_stub_raises_for_real_backend(self, monkeypatch):
        monkeypatch.setenv("OVP_TWITTER_BACKEND", "api_v2_basic")
        p = TwitterSignalProvider()
        # Score path raises NotImplementedError, but the orchestrator
        # catches it; the contract is that requesting a real backend
        # without wiring it up surfaces loud, not silent.
        with pytest.raises(NotImplementedError, match="api_v2_basic"):
            p.score("https://x.com/karpathy/status/123", {})


# ---------------------------------------------------------------------------
# Orchestrator combination math
# ---------------------------------------------------------------------------


class TestCombine:
    def test_no_signals_returns_neutral(self):
        assert _combine([]) == 0.45

    def test_weighted_average(self):
        signals = [
            Signal(provider="a", value=0.5, weight=1.0),
            Signal(provider="b", value=0.9, weight=1.0),
        ]
        assert _combine(signals) == pytest.approx(0.7, abs=0.01)

    def test_domain_floor_protects_against_drop(self):
        """An anthropic.com source with one weak signal should not
        drop below 70% of the domain rule's value."""
        signals = [
            Signal(provider="domain_rules", value=0.95, weight=1.0),
            Signal(provider="some_random", value=0.10, weight=1.0),
        ]
        out = _combine(signals)
        # Floor is 0.95 * 0.7 = 0.665; primary average is (0.95 + 0.10)/2 = 0.525
        # max(0.665, 0.525) = 0.665
        assert out == pytest.approx(0.665, abs=0.01)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_schema_idempotent(self, tmp_path):
        db_path = tmp_path / "k.db"
        with sqlite3.connect(db_path) as conn:
            ensure_schema(conn)
            ensure_schema(conn)  # second call — must not error
        # table exists
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='source_authority'"
            ).fetchone()
        assert row is not None

    def test_upsert_overwrites_previous(self, tmp_path):
        db_path = tmp_path / "k.db"
        with sqlite3.connect(db_path) as conn:
            ensure_schema(conn)
            score1 = AuthorityScore(
                source_id="https://example.com/x",
                authority=0.5, signals=[], scored_at="2026-05-01", scorer_version="v1",
            )
            score2 = AuthorityScore(
                source_id="https://example.com/x",
                authority=0.85, signals=[], scored_at="2026-05-03", scorer_version="v1",
            )
            upsert_score(conn, score1)
            upsert_score(conn, score2)
            row = conn.execute(
                "SELECT authority, scored_at FROM source_authority WHERE source_id = ?",
                ("https://example.com/x",),
            ).fetchone()
        assert row == (0.85, "2026-05-03")


# ---------------------------------------------------------------------------
# End-to-end: score a real-looking source
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_canonical_anthropic_blog_score(self, tmp_path):
        # No GitHub / no author file — just T1 rules
        providers = [
            DomainRulesProvider(),
            AuthorRulesProvider(authors_path=tmp_path / "authors.jsonl"),
        ]
        score = score_source(
            "https://www.anthropic.com/news/claude-3-opus",
            {"author": "Anthropic Team"},
            providers=providers,
        )
        assert score.authority >= 0.85
        assert any(s.provider == "domain_rules" for s in score.signals)

    def test_unknown_blog_defaults_to_neutral(self):
        providers = [DomainRulesProvider()]
        score = score_source(
            "https://random.example.com/post",
            {},
            providers=providers,
        )
        assert 0.4 <= score.authority <= 0.5
