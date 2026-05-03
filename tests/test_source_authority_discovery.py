"""Tests for PR-D3: discovery + LLM-assisted scoring + yaml overrides."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from ovp_pipeline.source_signals.overrides import (
    AuthorOverrides,
    DomainOverrides,
)
from ovp_pipeline.source_signals.domain_rules import DomainRulesProvider
from ovp_pipeline.source_signals.author_rules import AuthorRulesProvider
from ovp_pipeline.source_signals.url_utils import (
    extract_x_handle,
    normalize_host,
)


# ---------------------------------------------------------------------------
# url_utils — shared host normalization
# ---------------------------------------------------------------------------


class TestNormalizeHost:
    def test_strips_www_as_prefix_not_charset(self):
        # ``lstrip("www.")`` would mangle ``web.com`` into ``eb.com``.
        # Prefix removal must keep it intact.
        assert normalize_host("https://web.com/x") == "web.com"
        assert normalize_host("https://www.example.com/x") == "example.com"

    def test_strips_port_and_userinfo(self):
        assert normalize_host("https://user:pass@example.com:443/x") == "example.com"

    def test_lowercases(self):
        assert normalize_host("https://Anthropic.COM/news") == "anthropic.com"

    def test_returns_empty_on_bad_input(self):
        assert normalize_host("") == ""
        assert normalize_host("not-a-url") == ""


class TestExtractXHandle:
    def test_status_url(self):
        assert extract_x_handle("https://x.com/karpathy/status/12345") == "karpathy"
        assert extract_x_handle("https://twitter.com/sama/status/9") == "sama"

    def test_lowercases_handle(self):
        assert extract_x_handle("https://x.com/Karpathy/status/1") == "karpathy"

    def test_returns_none_for_non_status_url(self):
        assert extract_x_handle("https://x.com/karpathy") is None
        assert extract_x_handle("https://example.com/foo") is None
        assert extract_x_handle("") is None


# ---------------------------------------------------------------------------
# YAML override loaders
# ---------------------------------------------------------------------------


class TestDomainOverridesLoading:
    def test_missing_file_returns_empty(self, tmp_path):
        ov = DomainOverrides.load(tmp_path / "nonexistent.yaml")
        assert ov.domains == {}
        assert ov.excluded_hosts == set()

    def test_loads_valid_yaml(self, tmp_path):
        f = tmp_path / "overrides.yaml"
        f.write_text("""
domains:
  cloudflare.com:
    authority: 0.85
    bucket: canonical
    rationale: "Top-tier infra blog"
    source: manual
    added_at: "2026-05-03"
  some-blog.io:
    authority: 0.62
    bucket: mixed
excluded_hosts:
  - localhost
  - 127.0.0.1
""", encoding="utf-8")
        ov = DomainOverrides.load(f)
        assert "cloudflare.com" in ov.domains
        assert ov.domains["cloudflare.com"]["authority"] == 0.85
        assert ov.domains["cloudflare.com"]["bucket"] == "canonical"
        assert "localhost" in ov.excluded_hosts
        assert "127.0.0.1" in ov.excluded_hosts

    def test_skips_invalid_authority(self, tmp_path):
        f = tmp_path / "overrides.yaml"
        f.write_text("""
domains:
  bad-authority.com:
    authority: 1.5
  no-authority.com:
    bucket: mixed
  good.com:
    authority: 0.7
""", encoding="utf-8")
        ov = DomainOverrides.load(f)
        assert "bad-authority.com" not in ov.domains
        assert "no-authority.com" not in ov.domains
        assert "good.com" in ov.domains

    def test_malformed_yaml_returns_empty(self, tmp_path):
        f = tmp_path / "overrides.yaml"
        f.write_text("not: valid: yaml: at: all: [", encoding="utf-8")
        ov = DomainOverrides.load(f)
        assert ov.domains == {}


class TestAuthorOverridesLoading:
    def test_loads_valid_yaml(self, tmp_path):
        f = tmp_path / "authors.yaml"
        f.write_text("""
authors:
  - handle: "newperson"
    aliases: ["A. New Person"]
    authority: 0.78
    rationale: "manual"
""", encoding="utf-8")
        ov = AuthorOverrides.load(f)
        assert len(ov.authors) == 1
        assert ov.authors[0]["handle"] == "newperson"
        assert "a. new person" in ov.authors[0]["aliases"]

    def test_clips_authority_to_unit_interval(self, tmp_path):
        f = tmp_path / "authors.yaml"
        f.write_text("""
authors:
  - handle: "x"
    authority: 1.5
""", encoding="utf-8")
        ov = AuthorOverrides.load(f)
        assert ov.authors[0]["authority"] == 1.0


# ---------------------------------------------------------------------------
# Provider integration
# ---------------------------------------------------------------------------


class TestDomainProviderWithOverrides:
    def test_yaml_override_wins_over_hardcoded(self, tmp_path):
        f = tmp_path / "overrides.yaml"
        f.write_text("""
domains:
  github.com:
    authority: 0.95
    bucket: canonical
    rationale: "user explicitly upgraded to canonical"
""", encoding="utf-8")
        # Hardcoded github.com is 0.70 (mixed); override should win
        p = DomainRulesProvider(overrides_path=f)
        sig = p.score("https://github.com/foo/bar", {})
        assert sig is not None
        assert sig.value == 0.95
        assert sig.raw["source"] == "override"

    def test_excluded_host_returns_none(self, tmp_path):
        f = tmp_path / "overrides.yaml"
        f.write_text("""
excluded_hosts:
  - localhost
""", encoding="utf-8")
        p = DomainRulesProvider(overrides_path=f)
        sig = p.score("http://localhost/foo", {})
        assert sig is None  # excluded

    def test_hardcoded_still_works_without_override_file(self, tmp_path):
        p = DomainRulesProvider(overrides_path=tmp_path / "no-such-file.yaml")
        sig = p.score("https://www.anthropic.com/news/x", {})
        assert sig is not None
        assert sig.value >= 0.85

    def test_unknown_host_with_override_added(self, tmp_path):
        f = tmp_path / "overrides.yaml"
        f.write_text("""
domains:
  brand-new-blog.io:
    authority: 0.72
    bucket: mixed
""", encoding="utf-8")
        p = DomainRulesProvider(overrides_path=f)
        sig = p.score("https://brand-new-blog.io/post", {})
        assert sig is not None
        assert sig.value == 0.72


class TestAuthorProviderWithOverrides:
    def test_yaml_overrides_merged_with_jsonl(self, tmp_path):
        jsonl = tmp_path / "authors.jsonl"
        jsonl.write_text(
            json.dumps({"handle": "old_person", "authority": 0.7}) + "\n",
            encoding="utf-8",
        )
        yaml_path = tmp_path / "authors.yaml"
        yaml_path.write_text("""
authors:
  - handle: "new_person"
    authority: 0.8
""", encoding="utf-8")
        p = AuthorRulesProvider(authors_path=jsonl, overrides_path=yaml_path)
        sig_old = p.score("", {"author": "old_person"})
        sig_new = p.score("", {"author": "new_person"})
        assert sig_old.value == 0.7
        assert sig_new.value == 0.8

    def test_yaml_wins_on_handle_collision(self, tmp_path):
        jsonl = tmp_path / "authors.jsonl"
        jsonl.write_text(
            json.dumps({"handle": "shared", "authority": 0.5}) + "\n",
            encoding="utf-8",
        )
        yaml_path = tmp_path / "authors.yaml"
        yaml_path.write_text("""
authors:
  - handle: "shared"
    authority: 0.9
""", encoding="utf-8")
        p = AuthorRulesProvider(authors_path=jsonl, overrides_path=yaml_path)
        sig = p.score("", {"author": "shared"})
        assert sig.value == 0.9  # YAML wins


# ---------------------------------------------------------------------------
# Discovery dashboard (source_coverage)
# ---------------------------------------------------------------------------


def _seed_test_db(db_path: Path, sources: list[tuple[str, float]]) -> None:
    """Seed knowledge.db with source_authority rows for testing the
    coverage dashboard."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS source_authority (
                source_id TEXT PRIMARY KEY,
                authority REAL NOT NULL,
                signals_json TEXT NOT NULL,
                scored_at TEXT NOT NULL,
                scorer_version TEXT NOT NULL
            );
        """)
        for url, auth in sources:
            conn.execute(
                "INSERT OR REPLACE INTO source_authority VALUES (?, ?, '[]', '2026-05-03', 'v1')",
                (url, auth),
            )
        conn.commit()
    finally:
        conn.close()


class TestSourceCoverageDashboard:
    def test_collect_host_stats_basic(self, tmp_path):
        from ovp_pipeline.commands.source_coverage import collect_host_stats

        vault = tmp_path / "vault"
        (vault / "60-Logs").mkdir(parents=True)
        _seed_test_db(vault / "60-Logs" / "knowledge.db", [
            ("https://anthropic.com/news/1", 0.95),
            ("https://anthropic.com/news/2", 0.95),
            ("https://random.example.com/a", 0.45),
            ("https://random.example.com/b", 0.45),
            ("https://random.example.com/c", 0.45),
            ("https://github.com/foo/bar", 0.70),
        ])
        stats, buckets = collect_host_stats(vault)
        # random.example.com has most sources at default → top of triage
        assert stats[0].host == "random.example.com"
        assert stats[0].source_count == 3
        assert buckets["high"] == 2  # anthropic
        assert buckets["mid"] == 1   # github
        assert buckets["default"] == 3  # random

    def test_collect_host_stats_empty_vault(self, tmp_path):
        from ovp_pipeline.commands.source_coverage import collect_host_stats
        vault = tmp_path / "vault"
        vault.mkdir()
        stats, buckets = collect_host_stats(vault)
        assert stats == []
        assert sum(buckets.values()) == 0

    def test_unknown_x_handles_filtered_against_known(self, tmp_path):
        from ovp_pipeline.commands.source_coverage import (
            _load_known_authors,
            collect_unrecognized_x_handles,
        )
        vault = tmp_path / "vault"
        (vault / "60-Logs").mkdir(parents=True)
        _seed_test_db(vault / "60-Logs" / "knowledge.db", [
            ("https://x.com/karpathy/status/1", 0.75),
            ("https://x.com/unknown_handle/status/2", 0.45),
            ("https://x.com/unknown_handle/status/3", 0.45),
        ])
        # Seed authors.jsonl with karpathy only
        (vault / "60-Logs" / "authors.jsonl").write_text(
            json.dumps({"handle": "karpathy", "authority": 0.95}) + "\n",
            encoding="utf-8",
        )
        known = _load_known_authors(vault)
        unknowns = collect_unrecognized_x_handles(vault, known_authors=known)
        assert ("unknown_handle", 2) in unknowns
        assert all(h != "karpathy" for h, _ in unknowns)


# ---------------------------------------------------------------------------
# LLM-assisted scoring (mocked)
# ---------------------------------------------------------------------------


class TestScoreDomainCLI:
    def test_offline_mode_emits_heuristic_stub(self, tmp_path, capsys):
        from ovp_pipeline.commands.score_domain import main

        vault = tmp_path / "vault"
        (vault / "60-Logs").mkdir(parents=True)
        rc = main(["mp.weixin.qq.com", "--vault-dir", str(vault), "--offline"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "0.55" in out
        assert "mixed" in out
        assert "Heuristic" in out or "manual review" in out

    def test_offline_apply_writes_yaml(self, tmp_path):
        from ovp_pipeline.commands.score_domain import main

        vault = tmp_path / "vault"
        (vault / "60-Logs").mkdir(parents=True)
        main([
            "mp.weixin.qq.com", "--vault-dir", str(vault),
            "--offline", "--apply",
        ])
        yaml_path = vault / "60-Logs" / "domain_overrides.yaml"
        assert yaml_path.exists()
        text = yaml_path.read_text(encoding="utf-8")
        assert "mp.weixin.qq.com" in text
        assert "0.55" in text
        assert "heuristic" in text

    def test_existing_override_skipped_without_force(self, tmp_path, capsys):
        from ovp_pipeline.commands.score_domain import main

        vault = tmp_path / "vault"
        (vault / "60-Logs").mkdir(parents=True)
        (vault / "60-Logs" / "domain_overrides.yaml").write_text("""
domains:
  example.com:
    authority: 0.7
    bucket: mixed
    rationale: "already scored"
""", encoding="utf-8")
        rc = main(["example.com", "--vault-dir", str(vault), "--offline"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "already" in out.lower()
        assert "--force" in out
