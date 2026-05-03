"""Tests for PR-D3: discovery + LLM-assisted scoring + yaml overrides."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

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

    def test_skips_negative_authority(self, tmp_path):
        # DomainOverrides skips out-of-range values rather than clipping
        # (AuthorOverrides clips — different design choice for each surface).
        f = tmp_path / "overrides.yaml"
        f.write_text("""
domains:
  negative.com:
    authority: -0.5
  good.com:
    authority: 0.6
""", encoding="utf-8")
        ov = DomainOverrides.load(f)
        assert "negative.com" not in ov.domains
        assert "good.com" in ov.domains

    def test_malformed_yaml_returns_empty(self, tmp_path):
        f = tmp_path / "overrides.yaml"
        f.write_text("not: valid: yaml: at: all: [", encoding="utf-8")
        ov = DomainOverrides.load(f)
        assert ov.domains == {}

    def test_non_dict_domains_section_logged_and_skipped(self, tmp_path):
        # `domains:` accidentally written as a list — must not crash.
        f = tmp_path / "overrides.yaml"
        f.write_text("""
domains:
  - cloudflare.com
excluded_hosts:
  - localhost
""", encoding="utf-8")
        ov = DomainOverrides.load(f)
        assert ov.domains == {}
        assert "localhost" in ov.excluded_hosts

    def test_normalizes_host_keys(self, tmp_path):
        # YAML keys with www / scheme / mixed case must collapse to the
        # same normalized form runtime lookups use.
        f = tmp_path / "overrides.yaml"
        f.write_text("""
domains:
  www.Cloudflare.com:
    authority: 0.85
    bucket: canonical
  HTTPS://Foo.com/:
    authority: 0.7
    bucket: mixed
excluded_hosts:
  - www.LOCAL.test
""", encoding="utf-8")
        ov = DomainOverrides.load(f)
        assert "cloudflare.com" in ov.domains
        assert "foo.com" in ov.domains
        assert "local.test" in ov.excluded_hosts


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

    def test_clips_negative_authority_to_zero(self, tmp_path):
        f = tmp_path / "authors.yaml"
        f.write_text("""
authors:
  - handle: "y"
    authority: -0.3
""", encoding="utf-8")
        ov = AuthorOverrides.load(f)
        assert ov.authors[0]["authority"] == 0.0


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


def _seed_pages_index(db_path: Path, units: list[tuple[str, str]]) -> None:
    """Seed pages_index with evergreen rows pointing at source_url, used
    to drive the units-per-host weight in collect_host_stats."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pages_index (
                slug TEXT PRIMARY KEY,
                note_type TEXT NOT NULL,
                frontmatter_json TEXT NOT NULL
            );
        """)
        for slug, source_url in units:
            conn.execute(
                "INSERT OR REPLACE INTO pages_index VALUES (?, ?, ?)",
                (slug, "evergreen", json.dumps({"source_url": source_url})),
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

    def test_units_per_source_reorders_top_hosts(self, tmp_path):
        # Two hosts, equal source counts + authorities.  Without the
        # pages_index weight they tie; with the weight, the host whose
        # one source produced 50 evergreens beats the host whose two
        # sources produced 1 each.
        from ovp_pipeline.commands.source_coverage import collect_host_stats

        vault = tmp_path / "vault"
        (vault / "60-Logs").mkdir(parents=True)
        db = vault / "60-Logs" / "knowledge.db"
        _seed_test_db(db, [
            ("https://low-yield.com/a", 0.45),
            ("https://low-yield.com/b", 0.45),
            ("https://high-yield.com/x", 0.45),
            ("https://high-yield.com/y", 0.45),
        ])
        # high-yield's pages produced 50 evergreens each; low-yield 1 each
        _seed_pages_index(db, [
            (f"hy-{i}", "https://high-yield.com/x") for i in range(50)
        ] + [
            (f"hy2-{i}", "https://high-yield.com/y") for i in range(50)
        ] + [
            ("ly-1", "https://low-yield.com/a"),
            ("ly-2", "https://low-yield.com/b"),
        ])
        stats, _ = collect_host_stats(vault)
        # high-yield should dominate even though source_count is equal
        assert stats[0].host == "high-yield.com"
        assert stats[1].host == "low-yield.com"

    def test_json_output_mode(self, tmp_path, capsys):
        from ovp_pipeline.commands.source_coverage import main

        vault = tmp_path / "vault"
        (vault / "60-Logs").mkdir(parents=True)
        _seed_test_db(vault / "60-Logs" / "knowledge.db", [
            ("https://anthropic.com/news/1", 0.95),
            ("https://random.example.com/a", 0.45),
        ])
        rc = main(["--vault-dir", str(vault), "--json"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert "authority_distribution" in data
        assert "top_hosts" in data
        assert "unknown_x_handles" in data
        hosts = {h["host"] for h in data["top_hosts"]}
        assert "random.example.com" in hosts

    def test_triage_output_mode(self, tmp_path, capsys):
        from ovp_pipeline.commands.source_coverage import main

        vault = tmp_path / "vault"
        (vault / "60-Logs").mkdir(parents=True)
        _seed_test_db(vault / "60-Logs" / "knowledge.db", [
            ("https://unknown.example.com/a", 0.45),
        ])
        rc = main(["--vault-dir", str(vault), "--triage", "--top", "5"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "domains:" in out
        assert "unknown.example.com" in out
        assert "authority:" in out
        assert "bucket:" in out

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

    def test_load_known_authors_merges_yaml_overrides(self, tmp_path):
        from ovp_pipeline.commands.source_coverage import _load_known_authors

        vault = tmp_path / "vault"
        (vault / "60-Logs").mkdir(parents=True)
        (vault / "60-Logs" / "authors.jsonl").write_text(
            json.dumps({"handle": "alice", "authority": 0.9}) + "\n",
            encoding="utf-8",
        )
        # Same surface AuthorRulesProvider reads from at runtime — must
        # be unioned into the "known" set so YAML-curated handles aren't
        # flagged as unknown by the dashboard.
        (vault / "60-Logs" / "author_overrides.yaml").write_text("""
authors:
  - handle: "bob"
    aliases: ["Bob Smith"]
    authority: 0.8
""", encoding="utf-8")
        known = _load_known_authors(vault)
        assert "alice" in known
        assert "bob" in known
        assert "bob smith" in known


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
        # Offline mode always emits the exact heuristic-default rationale
        # from score_domain.py — pin it so future copy-edits don't silently
        # break the contract that "offline" means "stub for manual review".
        assert "Heuristic default" in out
        assert "manual review pending" in out

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
        # Parse + assert structure, not substrings — substring matches
        # would pass on malformed YAML or wrong field names.
        ov = DomainOverrides.load(yaml_path)
        assert "mp.weixin.qq.com" in ov.domains
        entry = ov.domains["mp.weixin.qq.com"]
        assert entry["authority"] == 0.55
        assert entry["bucket"] == "mixed"
        assert entry["source"] == "heuristic"

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

    def test_sample_for_host_filter_then_cap(self, tmp_path):
        # LIKE '%foo.com%' matches both 'foo.com' and 'matrixfoo.com'.
        # Old code sliced to max_samples BEFORE the host check, so when
        # the unrelated rows came first it returned [] instead of the
        # real matches.  Filter-then-cap fixes this.
        from ovp_pipeline.commands.score_domain import _sample_for_host

        vault = tmp_path / "vault"
        (vault / "60-Logs").mkdir(parents=True)
        db = vault / "60-Logs" / "knowledge.db"
        conn = sqlite3.connect(db)
        try:
            conn.executescript("""
                CREATE TABLE pages_index (
                    slug TEXT PRIMARY KEY,
                    note_type TEXT,
                    frontmatter_json TEXT NOT NULL
                );
            """)
            # Three unrelated rows that satisfy LIKE '%foo.com%' first…
            for i, url in enumerate([
                "https://matrixfoo.com/a",
                "https://matrixfoo.com/b",
                "https://matrixfoo.com/c",
            ]):
                conn.execute(
                    "INSERT INTO pages_index VALUES (?, 'evergreen', ?)",
                    (f"u{i}", json.dumps({"source_url": url, "title": "x"})),
                )
            # …then the actual host we want
            conn.execute(
                "INSERT INTO pages_index VALUES ('hit', 'evergreen', ?)",
                (json.dumps({"source_url": "https://foo.com/real", "title": "real"}),),
            )
            conn.commit()
        finally:
            conn.close()

        samples = _sample_for_host(vault, "foo.com", max_samples=3)
        assert len(samples) == 1
        assert samples[0]["url"] == "https://foo.com/real"

    def test_apply_refuses_to_overwrite_unparseable_file(self, tmp_path):
        # Pre-PR-D3 bug: a corrupt overrides file would silently parse
        # to {} and --apply would clobber it.  We now abort instead.
        from ovp_pipeline.commands.score_domain import main

        vault = tmp_path / "vault"
        (vault / "60-Logs").mkdir(parents=True)
        bad = vault / "60-Logs" / "domain_overrides.yaml"
        bad.write_text("not: valid: yaml: at: all: [", encoding="utf-8")

        with pytest.raises(SystemExit):
            main(["example.com", "--vault-dir", str(vault),
                  "--offline", "--apply"])
        # File must be untouched.
        assert bad.read_text(encoding="utf-8").startswith("not: valid:")

    def test_existing_override_skipped_for_normalized_host_collision(
        self, tmp_path, capsys,
    ):
        # User-curated YAML uses 'www.cloudflare.com'; CLI invokes
        # 'cloudflare.com'.  After normalization these must collide so
        # we don't silently re-score an already-curated host.
        from ovp_pipeline.commands.score_domain import main

        vault = tmp_path / "vault"
        (vault / "60-Logs").mkdir(parents=True)
        (vault / "60-Logs" / "domain_overrides.yaml").write_text("""
domains:
  www.cloudflare.com:
    authority: 0.85
    bucket: canonical
    rationale: "manual"
""", encoding="utf-8")
        rc = main(["cloudflare.com", "--vault-dir", str(vault), "--offline"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "already" in out.lower()
