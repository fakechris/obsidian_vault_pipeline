"""Tests for the recursive Clippings scan + collision dedupe.

Pre-2026-05 the scan was non-recursive ``glob("*.md")`` and silently
ignored ``Clippings/Twitter/*.md`` (Pinboard's Twitter clip target),
leaving 18 Twitter clips unprocessed for weeks.  These tests pin the
fix so the bug can't reappear.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ovp_pipeline.clippings_processor import ClippingsProcessor
from ovp_pipeline.unified_pipeline_enhanced import (
    PipelineLogger,
    TransactionManager,
)


def _make_processor(temp_vault: Path) -> ClippingsProcessor:
    logger = PipelineLogger(temp_vault / "60-Logs" / "pipeline.jsonl")
    txn_dir = temp_vault / "60-Logs" / "transactions"
    txn_dir.mkdir(parents=True, exist_ok=True)
    txn = TransactionManager(txn_dir)
    return ClippingsProcessor(temp_vault, logger, txn)


class TestRecursiveScan:
    def test_picks_up_twitter_subdirectory(self, temp_vault):
        """The pre-fix non-recursive ``glob("*.md")`` left Twitter clips
        in the unprocessed limbo this test is the regression guard for."""
        clippings = temp_vault / "Clippings"
        clippings.mkdir(parents=True, exist_ok=True)
        twitter = clippings / "Twitter"
        twitter.mkdir()

        # Top-level clip
        (clippings / "top_level.md").write_text("---\ntitle: top\n---\nbody\n", encoding="utf-8")
        # Twitter subdir clips
        (twitter / "BTCdayu - AI investing.md").write_text(
            "---\ntitle: AI investing\nsource: https://x.com/BTCdayu/status/123\n---\nbody\n",
            encoding="utf-8",
        )
        (twitter / "@HiTw93 - GEO.md").write_text(
            "---\ntitle: GEO\nsource: https://x.com/HiTw93/status/456\n---\nbody\n",
            encoding="utf-8",
        )

        processor = _make_processor(temp_vault)
        found = processor.scan_clippings()

        names = {p.name for p in found}
        assert "top_level.md" in names
        assert "BTCdayu - AI investing.md" in names
        assert "@HiTw93 - GEO.md" in names
        assert len(found) == 3

    def test_empty_clippings_dir_returns_empty(self, temp_vault):
        # No Clippings dir at all
        if (temp_vault / "Clippings").exists():
            import shutil
            shutil.rmtree(temp_vault / "Clippings")
        processor = _make_processor(temp_vault)
        assert processor.scan_clippings() == []


class TestCollisionDedupe:
    def test_skips_when_target_already_in_raw(self, temp_vault, monkeypatch):
        clippings = temp_vault / "Clippings"
        clippings.mkdir(parents=True, exist_ok=True)
        clip = clippings / "duplicate_article.md"
        clip.write_text(
            "---\ntitle: dup\nsource: https://example.com/a\n---\nbody\n",
            encoding="utf-8",
        )
        # Pre-existing target in 01-Raw with the SAME sanitized name
        raw_dir = temp_vault / "50-Inbox" / "01-Raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        # ClippingsProcessor sanitizes the name and the date prefix
        # might shift between runs, so we plant whatever
        # ``clipping_raw_name`` would produce.
        from ovp_pipeline.clippings_processor import clipping_raw_name
        processor = _make_processor(temp_vault)
        target_name = clipping_raw_name(clip, processor.sanitize_filename)
        (raw_dir / target_name).write_text("# pre-existing copy\n", encoding="utf-8")

        # Mock obsidian_move so we can detect whether the processor
        # tried to overwrite (it shouldn't).
        called = {"count": 0}
        def fake_move(*_args, **_kwargs):
            called["count"] += 1
            return True
        monkeypatch.setattr(processor, "obsidian_move", fake_move)

        result = processor.process_clippings(dry_run=False)

        assert result["scanned"] == 1
        assert result["skipped"] == 1
        assert result["migrated"] == 0
        # obsidian_move must NOT have been invoked for the colliding file.
        assert called["count"] == 0
        # Pre-existing target file is still there, untouched.
        assert (raw_dir / target_name).read_text(encoding="utf-8") == "# pre-existing copy\n"

    def test_skips_when_target_already_in_processed_month(self, temp_vault, monkeypatch):
        clippings = temp_vault / "Clippings"
        clippings.mkdir(parents=True, exist_ok=True)
        clip = clippings / "already_processed.md"
        clip.write_text(
            "---\ntitle: x\nsource: https://example.com/x\n---\nbody\n",
            encoding="utf-8",
        )
        # Pre-existing target in a 03-Processed month folder
        processed_month = temp_vault / "50-Inbox" / "03-Processed" / "2026-04"
        processed_month.mkdir(parents=True, exist_ok=True)
        from ovp_pipeline.clippings_processor import clipping_raw_name
        processor = _make_processor(temp_vault)
        target_name = clipping_raw_name(clip, processor.sanitize_filename)
        (processed_month / target_name).write_text("# already absorbed\n", encoding="utf-8")

        called = {"count": 0}
        monkeypatch.setattr(processor, "obsidian_move", lambda *a, **k: (called.update({"count": called["count"] + 1}), True)[1])

        result = processor.process_clippings(dry_run=False)
        assert result["skipped"] == 1
        assert called["count"] == 0
