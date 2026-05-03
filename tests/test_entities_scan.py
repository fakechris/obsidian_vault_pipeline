"""Tests for entities/scan.py — vault traversal + handle/repo extraction."""

from __future__ import annotations

from pathlib import Path

from ovp_pipeline.entities.scan import (
    scan_github_mentions,
    scan_twitter_handles,
)


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


class TestScanTwitterHandles:
    def test_picks_up_status_url(self, tmp_path):
        _write(tmp_path / "a.md",
               "Saw [tweet](https://x.com/karpathy/status/12345) today.")
        result = scan_twitter_handles(tmp_path)
        assert any(m.handle == "karpathy" for m in result)

    def test_picks_up_bare_profile_url(self, tmp_path):
        _write(tmp_path / "a.md",
               "Profile: https://twitter.com/sama")
        result = scan_twitter_handles(tmp_path)
        assert any(m.handle == "sama" for m in result)

    def test_lowercases_handle(self, tmp_path):
        _write(tmp_path / "a.md", "https://x.com/Karpathy/status/1")
        result = scan_twitter_handles(tmp_path)
        assert result[0].handle == "karpathy"

    def test_terminator_stops_at_subpath(self, tmp_path):
        # Without the URL-end lookahead, x.com/karpathy/settings would
        # capture "karpathy" but the regex would keep walking into the
        # subpath.  Pin: the captured handle must be exactly "karpathy".
        _write(tmp_path / "a.md",
               "Linked to https://x.com/karpathy/settings/account "
               "and https://x.com/sama)")
        result = scan_twitter_handles(tmp_path)
        handles = {m.handle for m in result}
        assert "karpathy" in handles
        assert "sama" in handles
        # Settings sub-segments must not become handles
        assert "settings" not in handles
        assert "account" not in handles

    def test_filters_reserved_paths(self, tmp_path):
        # /home, /search etc. aren't real handles — drop them.
        _write(tmp_path / "a.md",
               "Visit https://x.com/home and https://x.com/search "
               "and https://twitter.com/i/lists")
        result = scan_twitter_handles(tmp_path)
        handles = {m.handle for m in result}
        assert "home" not in handles
        assert "search" not in handles
        assert "i" not in handles

    def test_counts_mentions_and_files(self, tmp_path):
        _write(tmp_path / "a.md", "https://x.com/karpathy/status/1")
        _write(tmp_path / "b.md",
               "https://x.com/karpathy/status/2 and https://x.com/karpathy")
        result = scan_twitter_handles(tmp_path)
        kar = next(m for m in result if m.handle == "karpathy")
        assert kar.mention_count == 3
        assert kar.file_count == 2

    def test_skips_files_without_twitter(self, tmp_path):
        _write(tmp_path / "a.md", "no twitter here, just plain text")
        _write(tmp_path / "b.md", "https://x.com/sama/status/1")
        result = scan_twitter_handles(tmp_path)
        assert len(result) == 1
        assert result[0].handle == "sama"

    def test_skips_pycache_and_backup_dirs(self, tmp_path):
        _write(tmp_path / "__pycache__" / "x.md", "https://x.com/leak/status/1")
        _write(tmp_path / "_backup" / "y.md", "https://x.com/leak2/status/1")
        _write(tmp_path / "real.md", "https://x.com/real/status/1")
        result = scan_twitter_handles(tmp_path)
        handles = {m.handle for m in result}
        assert "real" in handles
        assert "leak" not in handles
        assert "leak2" not in handles

    def test_returns_descending_by_count(self, tmp_path):
        _write(tmp_path / "a.md",
               "https://x.com/once/status/1 "
               "https://x.com/many/status/1 "
               "https://x.com/many/status/2 "
               "https://x.com/many/status/3")
        result = scan_twitter_handles(tmp_path)
        assert result[0].handle == "many"
        assert result[1].handle == "once"


class TestScanGithubMentions:
    def test_picks_up_repo_url(self, tmp_path):
        _write(tmp_path / "a.md",
               "Check [karpathy/nanoGPT](https://github.com/karpathy/nanoGPT)")
        result = scan_github_mentions(tmp_path)
        assert any(m.owner == "karpathy" and m.repo == "nanogpt" for m in result)

    def test_drops_git_suffix(self, tmp_path):
        _write(tmp_path / "a.md", "https://github.com/foo/bar.git")
        result = scan_github_mentions(tmp_path)
        assert any(m.repo == "bar" for m in result)

    def test_drops_non_repo_owner_paths(self, tmp_path):
        # /orgs/anthropic isn't a repo
        _write(tmp_path / "a.md",
               "https://github.com/orgs/anthropic/people "
               "https://github.com/topics/llm")
        result = scan_github_mentions(tmp_path)
        owners = {m.owner for m in result}
        assert "orgs" not in owners
        assert "topics" not in owners

    def test_strips_trailing_path(self, tmp_path):
        # /issues/12 should not become part of the repo name
        _write(tmp_path / "a.md",
               "https://github.com/karpathy/nanoGPT/issues/12")
        result = scan_github_mentions(tmp_path)
        nano = next(m for m in result if m.repo == "nanogpt")
        assert nano.owner == "karpathy"
