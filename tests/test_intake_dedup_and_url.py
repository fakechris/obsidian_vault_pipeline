"""E2E tests for the BL-058 follow-up intake hardening:

1. URL preservation through the deep-dive layer (C1).
   When the legacy ``--keep-deep-dive`` flow runs, the resulting
   markdown's frontmatter ``source:`` MUST be the raw clipping's
   URL — not whatever the LLM wrote.

2. Deep-dive layer is skipped by default (C2).
   ``AutoArticleProcessor`` short-circuits before the LLM call,
   leaves intake-only output, and the lifecycle still archives
   the raw to ``50-Inbox/03-Processed/``.

3. URL-based intake dedup.
   When the same source URL is intaken twice, the second pass
   emits ``source_dedup_skipped`` and leaves the new clipping
   in place rather than producing a duplicate raw.

4. ``source_dedup`` index correctness.
   The URL→raw map handles the recognized frontmatter keys,
   ignores quoted variants, skips files outside 03-Processed,
   and surfaces dup groups for the cleanup CLI.

Why E2E (not unit-only):
   The original BL-058 abstraction-inflation bug shipped because
   no test ran the full intake → deep-dive → frontmatter chain
   together; the LLM-rewrites-frontmatter path stayed invisible
   until users saw URLs disappearing months later.  These tests
   pin the contract end-to-end so a future refactor that drops
   ``raw_source_url`` again breaks here, not in production.
"""

from __future__ import annotations

import json
from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_clipping(
    vault: Path, name: str, *, url: str, body: str = "Article body.\n",
) -> Path:
    """Drop a raw clipping under 50-Inbox/01-Raw/ with the standard
    Reader-style frontmatter.  Returns the file path."""
    raw_dir = vault / "50-Inbox" / "01-Raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    f = raw_dir / name
    f.write_text(
        f"""---
title: "Article Title"
source: "{url}"
author: "[[clip-author]]"
published: 2026-05-04
created: 2026-05-04
tags:
  - "clippings"
---

{body}
""",
        encoding="utf-8",
    )
    return f


def _make_processor(temp_vault: Path, *, skip_deep_dive: bool = True):
    from ovp_pipeline.auto_article_processor import (
        AutoArticleProcessor,
        PipelineLogger,
        TransactionManager,
    )
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    layout.logs_dir.mkdir(parents=True, exist_ok=True)
    layout.transactions_dir.mkdir(parents=True, exist_ok=True)
    logger = PipelineLogger(layout.pipeline_log)
    txn = TransactionManager(layout.transactions_dir)
    return AutoArticleProcessor(
        layout.vault_dir, logger, txn,
        skip_deep_dive=skip_deep_dive,
    )


# ---------------------------------------------------------------------------
# 1. C1: URL preservation through legacy deep-dive
# ---------------------------------------------------------------------------


class TestC1UrlPreservedInDeepDive:
    """When the operator opts back into the legacy LLM deep-dive
    layer (``--keep-deep-dive`` / ``skip_deep_dive=False``), the
    resulting markdown's ``source:`` field MUST be the raw URL,
    not whatever the LLM picked from the article subtitle.

    Pre-fix the LLM regenerated the entire frontmatter freely and
    often replaced the URL with a non-URL identifier (article
    series name, institution name, …).  ``_augment_frontmatter``
    now overwrites both ``source:`` and ``source_url:`` with the
    raw URL, treating the LLM's value as untrusted.
    """

    def test_augment_overwrites_llm_source_with_raw_url(self):
        from ovp_pipeline.auto_article_processor import (
            AutoArticleProcessor, PipelineLogger, TransactionManager,
        )
        from ovp_pipeline.runtime import VaultLayout
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "60-Logs").mkdir()
            (tmp_path / "10-Knowledge" / "Atlas").mkdir(parents=True)
            layout = VaultLayout.from_vault(tmp_path)
            logger = PipelineLogger(layout.pipeline_log)
            txn = TransactionManager(layout.transactions_dir)
            proc = AutoArticleProcessor(
                tmp_path, logger, txn, skip_deep_dive=False,
            )

            # Mimic the LLM-generated deep-dive markdown — the
            # bug shape is "source: <subtitle>" instead of the URL.
            llm_output = (
                "---\n"
                'title: "硅基原子"\n'
                'source: 硅基时间系列 · 1+1原生组织(一)\n'
                'author: 蒋涛\n'
                'date: 2026-05-04\n'
                "---\n\n"
                "# Body\n"
            )

            result = proc._augment_frontmatter(
                llm_output,
                decisions=[],
                area="AI-Research",
                txn_id="test-txn",
                raw_source_url="https://mp.weixin.qq.com/s/GSTKPlO_tnIkzSJKgphC-g",
            )
            # Both ``source`` and the new ``source_url`` carry the URL.
            assert "source: https://mp.weixin.qq.com/s/GSTKPlO_tnIkzSJKgphC-g" in result
            assert "source_url: https://mp.weixin.qq.com/s/GSTKPlO_tnIkzSJKgphC-g" in result
            # The subtitle no longer poses as ``source``.
            assert "source: 硅基时间系列" not in result

    def test_no_clobber_when_raw_has_no_url(self):
        # If the raw didn't have a URL (rare, but possible for
        # hand-written notes), don't overwrite — the LLM's
        # best-effort guess is still better than empty.
        from ovp_pipeline.auto_article_processor import (
            AutoArticleProcessor, PipelineLogger, TransactionManager,
        )
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "60-Logs").mkdir()
            (tmp_path / "10-Knowledge" / "Atlas").mkdir(parents=True)
            from ovp_pipeline.runtime import VaultLayout
            layout = VaultLayout.from_vault(tmp_path)
            logger = PipelineLogger(layout.pipeline_log)
            txn = TransactionManager(layout.transactions_dir)
            proc = AutoArticleProcessor(
                tmp_path, logger, txn, skip_deep_dive=False,
            )
            llm_output = (
                "---\n"
                'title: "Note"\n'
                'source: Anthropic Engineering Blog\n'
                "---\n\n# Body\n"
            )
            result = proc._augment_frontmatter(
                llm_output, decisions=[], area="AI-Research",
                txn_id="t", raw_source_url="",
            )
            # LLM's best-effort ``source`` survives.
            assert "source: Anthropic Engineering Blog" in result
            # ``source_url`` not added when we have nothing to add.
            assert "source_url:" not in result


# ---------------------------------------------------------------------------
# 2. C2: deep-dive skipped by default
# ---------------------------------------------------------------------------


class TestC2DeepDiveSkippedByDefault:
    """Default behaviour: ``skip_deep_dive=True``.

    ``process_single_file`` short-circuits before the LLM call.
    The lifecycle layer still archives the raw into 03-Processed
    so absorb v2 can pick it up on the next run.  No write to
    ``20-Areas/<area>/Topics/`` happens.
    """

    def test_intake_only_status_skips_llm(self, temp_vault):
        proc = _make_processor(temp_vault, skip_deep_dive=True)
        # No init_llm — verifies the LLM path is never reached.
        clip = _write_clipping(
            temp_vault, "2026-05-04_test.md",
            url="https://example.com/article",
        )
        # Stage manually since process_single_file expects a file
        # under processing_dir (the lifecycle would do this in the
        # full process_single_source path).
        from ovp_pipeline.runtime import VaultLayout
        layout = VaultLayout.from_vault(temp_vault)
        layout.processing_dir.mkdir(parents=True, exist_ok=True)
        target = layout.processing_dir / clip.name
        clip.rename(target)

        result = proc.process_single_file(target, dry_run=False)
        assert result["status"] == "intake_only"
        # No 20-Areas write.
        topics_dir = temp_vault / "20-Areas" / "AI-Research" / "Topics"
        if topics_dir.exists():
            assert not list(topics_dir.rglob("*_深度解读.md"))

    def test_intake_only_archives_raw_to_03_processed(self, temp_vault):
        # Goes through the full ``process_single_source`` path —
        # which moves clipping → raw → processing → archives the
        # processing copy to 03-Processed.
        proc = _make_processor(temp_vault, skip_deep_dive=True)
        clip = _write_clipping(
            temp_vault, "2026-05-04_clip.md",
            url="https://example.com/clip",
        )
        result = proc.process_single_source(clip, dry_run=False)
        assert result["status"] == "intake_only"
        # Raw landed in 03-Processed under the right month dir.
        archived = list(
            (temp_vault / "50-Inbox" / "03-Processed").rglob(
                "2026-05-04_clip.md"
            )
        )
        assert len(archived) == 1, (
            f"expected raw to be archived to 03-Processed, found {archived}"
        )

    def test_intake_only_preserves_source_url_in_archived_raw(self, temp_vault):
        """Critical contract: the URL the user clipped MUST survive
        the lifecycle move.  Otherwise the BL-058 abstraction
        recurs from a different angle (we'd lose URLs at archive
        time instead of at deep-dive time)."""
        proc = _make_processor(temp_vault, skip_deep_dive=True)
        url = "https://mp.weixin.qq.com/s/test123"
        clip = _write_clipping(
            temp_vault, "2026-05-04_wechat.md", url=url,
        )
        proc.process_single_source(clip, dry_run=False)
        archived = next(
            (temp_vault / "50-Inbox" / "03-Processed").rglob("2026-05-04_wechat.md")
        )
        text = archived.read_text(encoding="utf-8")
        assert f'source: "{url}"' in text


# ---------------------------------------------------------------------------
# 3. URL-based dedup
# ---------------------------------------------------------------------------


class TestUrlIntakeDedup:
    def test_same_url_second_intake_skipped(self, temp_vault):
        proc = _make_processor(temp_vault, skip_deep_dive=True)
        url = "https://example.com/dup-article"

        # First intake — succeeds, raw lands in 03-Processed.
        clip1 = _write_clipping(
            temp_vault, "2026-05-04_first.md", url=url,
        )
        r1 = proc.process_single_source(clip1, dry_run=False)
        assert r1["status"] == "intake_only"

        # Second intake of the same URL — ``process_single_source``
        # detects the existing 03-Processed copy and skips before
        # any lifecycle moves.
        clip2 = _write_clipping(
            temp_vault, "2026-05-05_second.md", url=url,
        )
        r2 = proc.process_single_source(clip2, dry_run=False)
        assert r2["status"] == "skipped_dedup"
        assert r2["dedup"]["url"] == url
        assert r2["dedup"]["existing"].endswith("2026-05-04_first.md")
        # The would-be-duplicate clip stayed in 01-Raw — caller
        # decides whether to delete or keep.
        assert clip2.exists()

    def test_archived_url_can_be_re_added(self, temp_vault):
        """A user who archived a prior copy and explicitly wants
        a fresh take must be able to re-clip the same URL.

        The dedup index only checks active 03-Processed files,
        not 70-Archive — so an archived URL is invisible to the
        dedup check by design.
        """
        # Pretend an old copy was archived.
        archive_dir = temp_vault / "70-Archive" / "old"
        archive_dir.mkdir(parents=True)
        archived = archive_dir / "old.md"
        archived.write_text(
            '---\nsource: "https://example.com/archived"\n---\n\nbody\n',
            encoding="utf-8",
        )

        proc = _make_processor(temp_vault, skip_deep_dive=True)
        clip = _write_clipping(
            temp_vault, "2026-05-04_fresh.md",
            url="https://example.com/archived",
        )
        result = proc.process_single_source(clip, dry_run=False)
        # Re-clip went through — archived copies don't block.
        assert result["status"] == "intake_only"

    def test_dedup_emits_audit_event(self, temp_vault):
        proc = _make_processor(temp_vault, skip_deep_dive=True)
        url = "https://example.com/audit-log-test"
        proc.process_single_source(
            _write_clipping(temp_vault, "first.md", url=url), dry_run=False,
        )
        proc.process_single_source(
            _write_clipping(temp_vault, "second.md", url=url), dry_run=False,
        )
        log = (temp_vault / "60-Logs" / "pipeline.jsonl").read_text(encoding="utf-8")
        events = [json.loads(line) for line in log.splitlines() if line.strip()]
        dedup_events = [
            e for e in events if e.get("event_type") == "source_dedup_skipped"
        ]
        assert len(dedup_events) == 1
        assert dedup_events[0]["url"] == url


# ---------------------------------------------------------------------------
# 4. source_dedup module — pure unit tests
# ---------------------------------------------------------------------------


class TestSourceDedupIndex:
    def test_extract_source_url_handles_quoted_and_unquoted(self, tmp_path):
        from ovp_pipeline.source_dedup import _extract_source_url
        # Quoted
        text = '---\nsource: "https://x.com/foo"\ntitle: t\n---\n\nbody\n'
        assert _extract_source_url(text) == "https://x.com/foo"
        # Unquoted
        text = "---\nsource: https://x.com/bar\n---\n\nbody\n"
        assert _extract_source_url(text) == "https://x.com/bar"
        # Non-URL value → None
        text = "---\nsource: Anthropic Engineering Blog\n---\n\nbody\n"
        assert _extract_source_url(text) is None

    def test_extract_recognizes_alt_keys(self):
        from ovp_pipeline.source_dedup import _extract_source_url
        for key in ("source_url", "url", "github", "twitter", "arxiv"):
            text = f"---\n{key}: https://x.com/{key}\n---\n\nbody\n"
            assert _extract_source_url(text) == f"https://x.com/{key}", \
                f"failed for key: {key}"

    def test_build_url_index_dedupes_by_first_seen(self, tmp_path):
        from ovp_pipeline.source_dedup import build_url_index
        processed = tmp_path / "50-Inbox" / "03-Processed" / "2026-05"
        processed.mkdir(parents=True)
        # Same URL in two files
        (processed / "a.md").write_text(
            '---\nsource: "https://x.com/dup"\n---\n\nbody\n', encoding="utf-8",
        )
        (processed / "b.md").write_text(
            '---\nsource: "https://x.com/dup"\n---\n\nbody\n', encoding="utf-8",
        )
        idx = build_url_index(tmp_path)
        # First-write-wins, ``a.md`` (sorted alphabetically) wins.
        assert idx["https://x.com/dup"].name == "a.md"

    def test_find_duplicate_groups_returns_only_groups(self, tmp_path):
        from ovp_pipeline.source_dedup import find_duplicate_groups
        processed = tmp_path / "50-Inbox" / "03-Processed" / "2026-05"
        processed.mkdir(parents=True)
        (processed / "a.md").write_text(
            '---\nsource: "https://dup.com/x"\n---\n\nbody\n', encoding="utf-8",
        )
        (processed / "b.md").write_text(
            '---\nsource: "https://dup.com/x"\n---\n\nbody\n', encoding="utf-8",
        )
        (processed / "unique.md").write_text(
            '---\nsource: "https://unique.com/y"\n---\n\nbody\n', encoding="utf-8",
        )
        groups = find_duplicate_groups(tmp_path)
        assert "https://dup.com/x" in groups
        assert len(groups["https://dup.com/x"]) == 2
        # Singletons are excluded from the cleanup target list.
        assert "https://unique.com/y" not in groups
