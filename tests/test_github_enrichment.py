"""Tests for BL-066 — GitHub enrichment chain + 03-Processed integration.

Covers:

* The 3-tier fallback in ``enrich_github_source``
* Parser robustness in ``parse_github_url``
* That ``auto_github_processor.process_single_repo`` writes to
  ``50-Inbox/03-Processed/<YYYY-MM>/`` with the right frontmatter
  (no more ``_深度解读`` files for github sources)
* That absorb's ``_collect_absorb_targets`` picks up the new path
  via the ``source_type: github-project`` frontmatter marker
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ovp_pipeline.github_enrichment import (
    EnrichedSource,
    enrich_github_source,
    parse_github_url,
)
from ovp_pipeline.auto_github_processor import (
    _build_frontmatter,
    _build_output_filename,
    _safe_segment,
    process_single_repo,
)
from ovp_pipeline.auto_evergreen_extractor import (
    _collect_absorb_targets,
    _is_github_source_markdown,
)
from ovp_pipeline.runtime import VaultLayout


# ---------------------------------------------------------------------------
# parse_github_url
# ---------------------------------------------------------------------------


class TestParseGithubURL:
    @pytest.mark.parametrize("url, expected", [
        ("https://github.com/owner/repo", ("owner", "repo")),
        ("https://github.com/owner/repo.git", ("owner", "repo")),
        ("https://github.com/owner/repo/tree/main", ("owner", "repo")),
        ("https://github.com/owner/repo/blob/main/file.py", ("owner", "repo")),
        ("https://www.github.com/owner/repo", ("owner", "repo")),
    ])
    def test_valid_urls(self, url, expected):
        assert parse_github_url(url) == expected

    @pytest.mark.parametrize("url", [
        "https://gitlab.com/owner/repo",
        "https://github.com/owner",            # missing repo
        "https://example.com/owner/repo",
        "not-a-url",
        "",
        # CodeRabbit finding: lstrip("www.") is a character-class
        # strip that incorrectly accepted URLs like "wwwgithub.com"
        # because it would strip the leading "www" as individual chars.
        # removeprefix("www.") fixes this.
        "https://wwwgithub.com/owner/repo",
        "https://github.com.evil.com/owner/repo",
    ])
    def test_rejects_non_github(self, url):
        assert parse_github_url(url) is None


# ---------------------------------------------------------------------------
# enrich_github_source — 3-tier fallback
# ---------------------------------------------------------------------------


class TestEnrichmentChain:
    """Verify each tier wins when the higher tier fails.

    We mock at the module-internal helpers so we exercise the actual
    fallback logic in ``enrich_github_source``, not just the helpers.
    """

    def test_tier1_deepwiki_wins(self):
        with (
            patch(
                "ovp_pipeline.github_enrichment.fetch_deepwiki",
                return_value=("# DeepWiki body", {"deepwiki_section_count": 5, "deepwiki_last_indexed": "2026-01-20"}),
            ),
            patch("ovp_pipeline.github_enrichment.fetch_readme", return_value=("# README", 42)),
            patch("ovp_pipeline.github_enrichment.fetch_gitingest") as gitingest_mock,
        ):
            result = enrich_github_source("owner", "repo")
        assert result.tier == "deepwiki"
        assert result.body == "# DeepWiki body"
        assert result.metadata["github_stars"] == 42
        assert result.metadata["deepwiki_last_indexed"] == "2026-01-20"
        # Tier 2 must NOT be invoked when Tier 1 hits
        gitingest_mock.assert_not_called()

    def test_tier2_gitingest_when_deepwiki_misses(self):
        with (
            patch("ovp_pipeline.github_enrichment.fetch_deepwiki", return_value=None),
            patch(
                "ovp_pipeline.github_enrichment.fetch_gitingest",
                return_value=("# GitIngest body", {"gitingest_commit": "abc123" + "0" * 34}),
            ),
            patch("ovp_pipeline.github_enrichment.fetch_readme", return_value=("# README", 7)),
        ):
            result = enrich_github_source("owner", "repo")
        assert result.tier == "gitingest"
        assert result.body == "# GitIngest body"
        assert result.metadata["github_stars"] == 7

    def test_tier3_readme_when_higher_fail(self):
        with (
            patch("ovp_pipeline.github_enrichment.fetch_deepwiki", return_value=None),
            patch("ovp_pipeline.github_enrichment.fetch_gitingest", return_value=None),
            patch(
                "ovp_pipeline.github_enrichment.fetch_readme",
                return_value=("# Just the README\n\nbody", 999),
            ),
        ):
            result = enrich_github_source("owner", "repo")
        assert result.tier == "readme"
        assert result.body == "# Just the README\n\nbody"
        assert result.metadata["github_stars"] == 999

    def test_all_tiers_fail_returns_empty_body(self):
        """Every tier returns empty/None — should still return a
        valid EnrichedSource with empty body, NOT raise.

        Caller (``process_single_repo``) decides what to do with
        empty body — it writes a stub-only markdown so the source is
        still tracked.
        """
        with (
            patch("ovp_pipeline.github_enrichment.fetch_deepwiki", return_value=None),
            patch("ovp_pipeline.github_enrichment.fetch_gitingest", return_value=None),
            patch("ovp_pipeline.github_enrichment.fetch_readme", return_value=("", 0)),
        ):
            result = enrich_github_source("ghost", "ghost")
        assert result.tier == "readme"
        assert result.body == ""


class TestFetchReadmeUsesDefaultBranch:
    """CodeRabbit finding: README probe was hardcoded to main/master/develop
    and missed repos with non-standard default branches.  Verify the new
    implementation queries GitHub API for ``default_branch`` first.
    """

    def test_uses_api_default_branch_first(self):
        from ovp_pipeline.github_enrichment import fetch_readme
        from unittest.mock import patch

        captured_urls: list[str] = []

        def fake_get(url, timeout=10.0):
            captured_urls.append(url)
            if "api.github.com/repos/" in url:
                return '{"default_branch": "trunk", "stargazers_count": 42}'
            if "/trunk/README.md" in url:
                return "# Real README from trunk\n\nbody"
            return None

        with patch("ovp_pipeline.github_enrichment._http_get", side_effect=fake_get):
            body, stars = fetch_readme("o", "r")

        assert "Real README from trunk" in body
        assert stars == 42
        # API call must precede branch probes
        assert captured_urls[0].startswith("https://api.github.com/repos/")
        # default_branch was used
        assert any("/trunk/README.md" in u for u in captured_urls)

    def test_falls_back_to_hardcoded_branches_when_api_fails(self):
        """If the GitHub API returns None (rate-limited, blocked,
        offline), we still try main/master/develop."""
        from ovp_pipeline.github_enrichment import fetch_readme
        from unittest.mock import patch

        def fake_get(url, timeout=10.0):
            if "api.github.com" in url:
                return None  # API down
            if "/main/README.md" in url:
                return "# fallback worked"
            return None

        with patch("ovp_pipeline.github_enrichment._http_get", side_effect=fake_get):
            body, stars = fetch_readme("o", "r")

        assert "fallback worked" in body
        assert stars == 0  # API gave us nothing


# ---------------------------------------------------------------------------
# Filename + frontmatter
# ---------------------------------------------------------------------------


class TestFilenameAndFrontmatter:
    def test_filename_no_深度解读_suffix(self):
        """BL-066: github sources are no longer deep-dives.  The
        filename MUST NOT carry ``_深度解读``.  This test is the
        regression guard against accidental reintroduction.
        """
        name = _build_output_filename("2026-04-28", "neuphonic", "neutts")
        assert name == "2026-04-28_neuphonic_neutts.md"
        assert "深度解读" not in name

    @pytest.mark.parametrize("input_name, expected", [
        ("ok-name", "ok-name"),
        ("name with spaces", "name-with-spaces"),
        ("name/with/slashes", "name-with-slashes"),
        ("name.with.dots", "name-with-dots"),
        ("---", "unknown"),  # all stripped → fallback
    ])
    def test_safe_segment(self, input_name, expected):
        assert _safe_segment(input_name) == expected

    def test_frontmatter_records_tier(self):
        enriched = EnrichedSource(
            owner="neuphonic", repo="neutts", tier="deepwiki",
            body="# stuff",
            metadata={
                "tier": "deepwiki",
                "deepwiki_last_indexed": "2026-01-20",
                "deepwiki_section_count": 12,
                "github_stars": 1543,
            },
        )
        fm = _build_frontmatter(
            title="neuphonic/neutts",
            url="https://github.com/neuphonic/neutts",
            owner="neuphonic", repo="neutts",
            date="2026-04-28",
            tags=["github", "tts"],
            enriched=enriched,
            fetched_at="2026-05-05T11:48:23Z",
        )
        # Required marker for absorb's frontmatter scanner
        assert "source_type: github-project" in fm
        assert "source_tier: deepwiki" in fm
        assert "github_owner: neuphonic" in fm
        assert "github_repo: neutts" in fm
        assert "github_stars: 1543" in fm
        assert "source_indexed_at:" in fm  # only present on deepwiki tier
        assert "deepwiki_section_count: 12" in fm
        assert "tags: [github, tts]" in fm

    def test_frontmatter_omits_deepwiki_fields_for_other_tiers(self):
        enriched = EnrichedSource(
            owner="o", repo="r", tier="readme",
            body="readme body",
            metadata={"tier": "readme", "github_stars": 5},
        )
        fm = _build_frontmatter(
            title="o/r", url="https://github.com/o/r",
            owner="o", repo="r", date="2026-05-05",
            tags=[], enriched=enriched,
            fetched_at="2026-05-05T00:00:00Z",
        )
        assert "source_tier: readme" in fm
        assert "source_indexed_at:" not in fm
        assert "deepwiki_" not in fm


# ---------------------------------------------------------------------------
# process_single_repo — output location + content
# ---------------------------------------------------------------------------


class TestProcessSingleRepo:
    def test_writes_to_03_processed_not_20_areas(self, tmp_path):
        """The pre-BL-066 implementation wrote to
        ``20-Areas/Tools/Topics/<YYYY-MM>/<slug>_深度解读.md``.
        After BL-066 the path is
        ``50-Inbox/03-Processed/<YYYY-MM>/<slug>.md``.

        This test pins both ends of that change so a regression to
        the deep-dive layer fails immediately.
        """
        layout = self._init_vault(tmp_path)
        out_dir = layout.processed_dir / "2026-04"
        out_dir.mkdir(parents=True, exist_ok=True)

        with patch(
            "ovp_pipeline.auto_github_processor.enrich_github_source",
            return_value=EnrichedSource(
                owner="o", repo="r", tier="deepwiki",
                body="# enriched body\n\nspecific 42 fact",
                metadata={"tier": "deepwiki", "github_stars": 100,
                          "deepwiki_last_indexed": "2026-01-20"},
            ),
        ):
            result = process_single_repo(
                url="https://github.com/o/r",
                date="2026-04-28",
                tags=["github"],
                description="o/r",
                output_dir=out_dir,
                dry_run=False,
            )

        assert result["status"] == "completed"
        out_path = Path(result["output_file"])
        # Lives in 50-Inbox/03-Processed
        assert out_path.parent == out_dir
        assert out_path.parent.parent.name == "03-Processed"
        # Filename has no 深度解读
        assert "_深度解读" not in out_path.name
        # Body includes the enriched content
        body = out_path.read_text(encoding="utf-8")
        assert "specific 42 fact" in body
        # And carries the marker absorb scans for
        assert "source_type: github-project" in body
        assert "source_tier: deepwiki" in body

    def test_empty_body_status_skipped(self, tmp_path):
        """When all 3 tiers return empty, the file is still written
        (frontmatter only) but status is 'skipped' so absorb won't
        try to extract from a stub.

        Frontmatter MUST contain ``extraction_status: skipped`` so the
        absorb scanner rejects it (CodeRabbit finding).
        """
        layout = self._init_vault(tmp_path)
        out_dir = layout.processed_dir / "2026-04"
        out_dir.mkdir(parents=True, exist_ok=True)

        with patch(
            "ovp_pipeline.auto_github_processor.enrich_github_source",
            return_value=EnrichedSource(
                owner="o", repo="r", tier="readme",
                body="",  # all tiers empty
                metadata={"tier": "readme", "github_stars": 0},
            ),
        ):
            result = process_single_repo(
                url="https://github.com/o/r",
                date="2026-04-28",
                tags=[],
                description="",
                output_dir=out_dir,
                dry_run=False,
            )
        assert result["status"] == "skipped"
        assert result["error"] == "empty_body"
        # Frontmatter must explicitly mark this as skipped so absorb
        # ignores it during evergreen extraction.
        body = Path(result["output_file"]).read_text(encoding="utf-8")
        assert "extraction_status: skipped" in body

    @staticmethod
    def _init_vault(tmp_path: Path) -> VaultLayout:
        # Minimal vault skeleton VaultLayout expects
        for d in [
            "10-Knowledge/Evergreen", "20-Areas", "50-Inbox/03-Processed",
            "60-Logs", "70-Archive",
        ]:
            (tmp_path / d).mkdir(parents=True, exist_ok=True)
        (tmp_path / ".obsidian").mkdir(exist_ok=True)
        return VaultLayout.from_vault(tmp_path)


# ---------------------------------------------------------------------------
# absorb integration — _is_github_source_markdown + collection
# ---------------------------------------------------------------------------


class TestAbsorbPicksUpGithubSources:
    def test_is_github_source_markdown_detects_marker(self, tmp_path):
        github_md = tmp_path / "github.md"
        github_md.write_text(
            "---\n"
            "title: o/r\n"
            "source: https://github.com/o/r\n"
            "source_type: github-project\n"
            "source_tier: deepwiki\n"
            "---\n\n"
            "body\n",
            encoding="utf-8",
        )
        article_md = tmp_path / "article.md"
        article_md.write_text(
            "---\n"
            "title: An article\n"
            "type: article\n"
            "---\n\n"
            "body\n",
            encoding="utf-8",
        )
        no_fm = tmp_path / "no_fm.md"
        no_fm.write_text("just body\n", encoding="utf-8")

        assert _is_github_source_markdown(github_md) is True
        assert _is_github_source_markdown(article_md) is False
        assert _is_github_source_markdown(no_fm) is False

    def test_skipped_extraction_status_rejects_file(self, tmp_path):
        """CodeRabbit finding: github sources with empty enrichment
        bodies write ``extraction_status: skipped`` to frontmatter.
        ``_is_github_source_markdown`` must return False for those so
        absorb doesn't try to extract from a stub."""
        skipped_md = tmp_path / "skipped.md"
        skipped_md.write_text(
            "---\n"
            "title: o/r\n"
            "source: https://github.com/o/r\n"
            "source_type: github-project\n"
            "source_tier: readme\n"
            "extraction_status: skipped\n"
            "---\n\n"
            "_All enrichment tiers returned empty content._\n",
            encoding="utf-8",
        )
        completed_md = tmp_path / "completed.md"
        completed_md.write_text(
            "---\n"
            "title: o/r\n"
            "source: https://github.com/o/r\n"
            "source_type: github-project\n"
            "source_tier: deepwiki\n"
            "extraction_status: completed\n"
            "---\n\n"
            "# real body\n",
            encoding="utf-8",
        )

        # Skipped file is rejected; completed file is accepted.
        assert _is_github_source_markdown(skipped_md) is False
        assert _is_github_source_markdown(completed_md) is True

    def test_marker_must_be_in_frontmatter_not_body(self, tmp_path):
        """The marker check must NOT match if the literal text
        appears only in the body — that would let an article quoting
        ``source_type: github-project`` accidentally trigger absorb's
        github-source path."""
        md = tmp_path / "tricky.md"
        md.write_text(
            "---\n"
            "title: An article that mentions github stuff\n"
            "type: article\n"
            "---\n\n"
            "Some body text mentioning source_type: github-project as a string.\n",
            encoding="utf-8",
        )
        assert _is_github_source_markdown(md) is False

    def test_collect_absorb_targets_recent_picks_up_github_processed(self, tmp_path):
        """The recent-window scan must find github-source markdowns
        in 50-Inbox/03-Processed/<YYYY-MM>/, not just deep-dives in
        20-Areas/.../Topics/<YYYY-MM>/.
        """
        layout = TestProcessSingleRepo._init_vault(tmp_path)
        from datetime import datetime, timezone
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        gh_dir = layout.processed_dir / month
        gh_dir.mkdir(parents=True, exist_ok=True)
        gh_path = gh_dir / "2026-05-04_o_r.md"
        gh_path.write_text(
            "---\n"
            "source: https://github.com/o/r\n"
            "source_type: github-project\n"
            "source_tier: deepwiki\n"
            "---\n\n"
            "# body\n",
            encoding="utf-8",
        )

        targets = _collect_absorb_targets(layout, recent=7)
        assert gh_path.resolve() in {t.resolve() for t in targets}

    def test_collect_absorb_targets_directory_picks_up_github_md(self, tmp_path):
        """When invoked with ``--directory <some-dir>``, absorb must
        pick up both legacy ``_深度解读.md`` AND new github-source
        markdowns in the same directory."""
        layout = TestProcessSingleRepo._init_vault(tmp_path)
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        legacy = scratch / "2026-04-01_legacy_深度解读.md"
        legacy.write_text("---\ntitle: legacy\n---\nbody\n", encoding="utf-8")

        github = scratch / "2026-04-28_o_r.md"
        github.write_text(
            "---\n"
            "source: https://github.com/o/r\n"
            "source_type: github-project\n"
            "---\nbody\n",
            encoding="utf-8",
        )

        unrelated = scratch / "2026-04-28_random.md"
        unrelated.write_text("---\ntitle: random\n---\nbody\n", encoding="utf-8")

        targets = _collect_absorb_targets(layout, directory=scratch)
        target_set = {t.resolve() for t in targets}
        assert legacy.resolve() in target_set
        assert github.resolve() in target_set
        assert unrelated.resolve() not in target_set
