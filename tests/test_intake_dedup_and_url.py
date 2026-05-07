"""E2E tests for the BL-058 / BL-029 intake hardening:

1. ``AutoArticleProcessor`` is intake-only post-BL-029 — no LLM
   call.  ``process_single_file`` returns ``intake_only`` and the
   lifecycle layer archives the raw to ``50-Inbox/03-Processed/``
   so absorb v2 can pick it up.

2. URL-based intake dedup.
   When the same source URL is intaken twice, the second pass
   emits ``source_dedup_skipped`` and leaves the new clipping
   in place rather than producing a duplicate raw.

3. ``source_dedup`` index correctness.
   The URL→raw map handles the recognized frontmatter keys,
   ignores quoted variants, skips files outside the active
   staging set, and surfaces dup groups for the cleanup CLI.

Why E2E (not unit-only):
   The original BL-058 abstraction-inflation bug shipped because
   no test ran the full intake → archive chain together; the
   LLM-rewrites-frontmatter path stayed invisible until users saw
   URLs disappearing months later.  These tests pin the contract
   end-to-end so a future refactor that drops ``source`` URL
   preservation breaks here, not in production.
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


def _make_processor(temp_vault: Path):
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
    return AutoArticleProcessor(layout.vault_dir, logger, txn)


# ---------------------------------------------------------------------------
# 2. C2: deep-dive skipped by default
# ---------------------------------------------------------------------------


class TestC2DeepDiveSkippedByDefault:
    """Post-BL-029: intake-only is the only behaviour.

    ``process_single_file`` does no LLM work — just image download
    + frontmatter parse + ``intake_only`` return.  The lifecycle
    layer still archives the raw into 03-Processed so absorb v2
    can pick it up on the next run.  No write to
    ``20-Areas/<area>/Topics/`` ever happens.
    """

    def test_intake_only_status_skips_llm(self, temp_vault):
        proc = _make_processor(temp_vault)
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
        proc = _make_processor(temp_vault)
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
        proc = _make_processor(temp_vault)
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
        proc = _make_processor(temp_vault)
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

        proc = _make_processor(temp_vault)
        clip = _write_clipping(
            temp_vault, "2026-05-04_fresh.md",
            url="https://example.com/archived",
        )
        result = proc.process_single_source(clip, dry_run=False)
        # Re-clip went through — archived copies don't block.
        assert result["status"] == "intake_only"

    def test_dedup_emits_audit_event(self, temp_vault):
        proc = _make_processor(temp_vault)
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


# ---------------------------------------------------------------------------
# 5. Global active-staging URL dedup
#
# The 2026-05-07 v0.12.0 incremental run produced 12 new dups because
# the URL gate scanned 03-Processed only.  Clippings/ files holding
# the same URL as already-processed items walked through the pipeline
# unchecked.  These tests pin the broader contract: dedup looks at
# the entire active-staging set (Clippings, 02-Pinboard, 01-Raw,
# 02-Processing, 03-Processed) — but explicitly NOT 70-Archive.
# ---------------------------------------------------------------------------


def _write_md(path: Path, *, url: str, body: str = "body\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\nsource: "{url}"\n---\n\n{body}', encoding="utf-8",
    )
    return path


class TestActiveStagingDedupIndex:
    """``build_active_url_index`` walks all 5 staging dirs."""

    def test_index_includes_all_active_dirs(self, tmp_path):
        from ovp_pipeline.source_dedup import build_active_url_index
        _write_md(tmp_path / "Clippings/c.md", url="https://x.com/c")
        _write_md(tmp_path / "50-Inbox/02-Pinboard/p.md", url="https://x.com/p")
        _write_md(tmp_path / "50-Inbox/01-Raw/r.md", url="https://x.com/r")
        _write_md(tmp_path / "50-Inbox/02-Processing/proc.md", url="https://x.com/proc")
        _write_md(tmp_path / "50-Inbox/03-Processed/2026-05/done.md", url="https://x.com/done")
        idx = build_active_url_index(tmp_path)
        assert {"https://x.com/c", "https://x.com/p", "https://x.com/r",
                "https://x.com/proc", "https://x.com/done"} <= idx.keys()

    def test_archive_is_excluded(self, tmp_path):
        from ovp_pipeline.source_dedup import build_active_url_index
        _write_md(tmp_path / "70-Archive/old.md", url="https://x.com/archived")
        idx = build_active_url_index(tmp_path)
        assert "https://x.com/archived" not in idx

    def test_downstream_stage_wins_on_collision(self, tmp_path):
        """When the same URL exists in 01-Raw AND 03-Processed,
        the index points to 03-Processed.  This is what makes the
        self-match check in ``_check_url_dedup`` correctly flag the
        01-Raw file as a duplicate of the 03-Processed one.
        """
        from ovp_pipeline.source_dedup import build_active_url_index
        url = "https://x.com/staged-and-processed"
        _write_md(tmp_path / "50-Inbox/01-Raw/r.md", url=url)
        _write_md(tmp_path / "50-Inbox/03-Processed/2026-05/p.md", url=url)
        idx = build_active_url_index(tmp_path)
        # 03-Processed wins per ACTIVE_INTAKE_DIRS ordering
        assert idx[url].parts[-2] == "2026-05"

    def test_build_url_index_processed_only(self, tmp_path):
        """The narrow ``build_url_index`` keeps its 03-Processed-only
        scope so the cleanup CLI doesn't archive in-flight files.
        """
        from ovp_pipeline.source_dedup import build_url_index
        _write_md(tmp_path / "Clippings/c.md", url="https://x.com/c")
        _write_md(tmp_path / "50-Inbox/01-Raw/r.md", url="https://x.com/r")
        _write_md(tmp_path / "50-Inbox/03-Processed/2026-05/p.md", url="https://x.com/p")
        idx = build_url_index(tmp_path)
        assert "https://x.com/p" in idx
        assert "https://x.com/c" not in idx
        assert "https://x.com/r" not in idx


class TestProcessInboxUrlDedup:
    """``process_inbox`` (the clippings-flow consumer) must catch
    URLs already living in 03-Processed under a different basename
    — the v0.12.0 bug shape.
    """

    def test_inbox_skips_clipping_already_in_03_processed(self, temp_vault):
        # Pre-existing 03-Processed copy under last-month's basename
        url = "https://anthropic.com/engineering/dup-test"
        _write_md(
            temp_vault / "50-Inbox/03-Processed/2026-04/2026-04-09_X.md",
            url=url,
        )
        # New raw lands in 01-Raw with a 2026-05 basename — same URL
        new_raw = _write_clipping(
            temp_vault, "2026-05-07_X.md", url=url,
        )
        proc = _make_processor(temp_vault)
        results = proc.process_inbox(dry_run=False)
        # Exactly one file processed, and it was skipped via the URL
        # gate (status=skipped_dedup), NOT processed via intake_only.
        statuses = [f["status"] for f in results["files"]]
        assert statuses == ["skipped_dedup"], statuses
        assert results["skipped"] == 1
        assert results["completed"] == 0
        # Audit event records the gate fired at process_inbox stage
        log = (temp_vault / "60-Logs" / "pipeline.jsonl").read_text("utf-8")
        events = [json.loads(line) for line in log.splitlines() if line.strip()]
        gate_events = [
            e for e in events
            if e.get("event_type") == "source_dedup_skipped"
            and e.get("stage") == "process_inbox"
        ]
        assert len(gate_events) == 1
        assert gate_events[0]["url"] == url
        # The new raw was NOT clobbered — it stays for the user to
        # delete or re-process explicitly.
        assert new_raw.exists()

    def test_inbox_self_match_is_not_a_dup(self, temp_vault):
        """A 01-Raw file whose URL also lives at the same path in
        the index must not flag itself.
        """
        url = "https://example.com/only-once"
        _write_clipping(temp_vault, "single.md", url=url)
        proc = _make_processor(temp_vault)
        results = proc.process_inbox(dry_run=False)
        statuses = [f["status"] for f in results["files"]]
        assert statuses == ["intake_only"], statuses


def _make_clippings_processor(temp_vault):
    from ovp_pipeline.clippings_processor import ClippingsProcessor
    from ovp_pipeline.auto_article_processor import (
        PipelineLogger, TransactionManager,
    )
    from ovp_pipeline.runtime import VaultLayout
    layout = VaultLayout.from_vault(temp_vault)
    layout.logs_dir.mkdir(parents=True, exist_ok=True)
    layout.transactions_dir.mkdir(parents=True, exist_ok=True)
    logger = PipelineLogger(layout.pipeline_log)
    txn = TransactionManager(layout.transactions_dir)
    return ClippingsProcessor(temp_vault, logger, txn)


class TestClippingsProcessorUrlDedup:
    """``ClippingsProcessor.process_clippings`` must skip a clipping
    whose URL already lives anywhere downstream — the case the
    basename-only ``_target_already_exists`` check missed.

    These tests bypass ``obsidian_move`` (which shells out to the
    Obsidian CLI) by setting the ``OVP_TEST_USE_FS_MOVE`` env so
    the processor takes the filesystem-fallback branch.  Even if
    the env hook isn't honored we still validate the dedup
    decision — the file may or may not move, but the gate's
    skipped/migrated verdict is what we assert.
    """

    def test_clipping_skipped_when_url_already_in_03_processed(self, temp_vault):
        url = "https://anthropic.com/engineering/already-processed"
        # Pre-existing copy under a 2026-04 basename
        _write_md(
            temp_vault / "50-Inbox/03-Processed/2026-04/old_name.md",
            url=url,
        )
        # User re-clips under a totally different basename — no
        # filename collision, so the legacy guard wouldn't fire.
        clip = temp_vault / "Clippings" / "Brand New Article.md"
        _write_md(clip, url=url)

        proc = _make_clippings_processor(temp_vault)
        # dry_run=True is enough — the dedup gate runs before the move
        results = proc.process_clippings(dry_run=True)
        statuses = [f["status"] for f in results["files"]]
        assert "skipped_url_dedup" in statuses
        # File stays in Clippings — the user gets to see and decide.
        assert clip.exists()
        # Audit event has stage=clippings_intake
        log = (temp_vault / "60-Logs" / "pipeline.jsonl").read_text("utf-8")
        events = [json.loads(line) for line in log.splitlines() if line.strip()]
        intake_events = [
            e for e in events
            if e.get("event_type") == "source_dedup_skipped"
            and e.get("stage") == "clippings_intake"
        ]
        assert len(intake_events) == 1

    def test_two_clippings_same_url_dedup_in_batch_dry_run(self, temp_vault):
        """Dry-run sanity: two Clippings entries with the same URL
        in the same run — at least one is flagged via the URL gate.

        In dry-run the ``staged_urls`` post-migration set isn't
        exercised because no migration happens; the gate fires via
        the on-disk active index instead (both Clippings files
        exist on disk and the index walks ``Clippings/``).  The
        non-dry-run sibling below pins the ``staged_urls`` path.
        """
        url = "https://x.com/same-url-twice"
        clip1 = temp_vault / "Clippings" / "AAA.md"
        clip2 = temp_vault / "Clippings" / "BBB.md"
        _write_md(clip1, url=url)
        _write_md(clip2, url=url)

        proc = _make_clippings_processor(temp_vault)
        results = proc.process_clippings(dry_run=True)
        statuses = sorted(f["status"] for f in results["files"])
        assert "skipped_url_dedup" in statuses
        assert "dry_run" in statuses

    def test_two_clippings_same_url_in_batch_real_migration(self, temp_vault):
        """Real-migration sibling: stub ``obsidian_move`` to skip the
        Obsidian CLI shell-out so we can run with ``dry_run=False``
        and exercise the ``staged_urls.add(...)`` post-migration
        path that the dry-run test can't reach.

        Asserts: first clipping migrates, second skips via in-batch
        gate (``existing == "in_batch"`` in the audit event), and
        the gate's pre-move check still passes for the first one.
        """
        url = "https://x.com/same-url-real"
        clip1 = temp_vault / "Clippings" / "AAA.md"
        clip2 = temp_vault / "Clippings" / "BBB.md"
        _write_md(clip1, url=url)
        _write_md(clip2, url=url)

        proc = _make_clippings_processor(temp_vault)
        # Replace the Obsidian-CLI move with a noop so the test
        # doesn't depend on the binary being on PATH; record calls.
        moved: list[str] = []

        def fake_move(source, dest_dir, new_name=None):
            moved.append(str(source.name))
            # Mirror the real ``obsidian_move`` log.
            proc.logger.log("file_moved", {
                "source": str(source.relative_to(proc.vault_dir)),
                "destination": str((dest_dir / (new_name or source.name)).relative_to(proc.vault_dir)),
                "method": "test_stub",
            })
            return True

        proc.obsidian_move = fake_move  # type: ignore[assignment]

        results = proc.process_clippings(dry_run=False)
        statuses = sorted(f["status"] for f in results["files"])
        assert statuses == ["migrated", "skipped_url_dedup"], statuses
        assert moved == ["AAA.md"], moved  # only first file migrates

        log = (temp_vault / "60-Logs" / "pipeline.jsonl").read_text("utf-8")
        events = [json.loads(line) for line in log.splitlines() if line.strip()]
        gate_events = [
            e for e in events
            if e.get("event_type") == "source_dedup_skipped"
            and e.get("stage") == "clippings_intake"
        ]
        assert len(gate_events) == 1
        assert gate_events[0]["existing"] == "in_batch"


class TestActiveIntakeDirsPriorityLadder:
    """Pin the full pairwise priority ordering of
    ``ACTIVE_INTAKE_DIRS``.  When the same URL exists in two
    staging dirs, ``build_active_url_index`` must point to the
    one earlier in the tuple.  This guards against a future
    re-ordering breaking the self-match logic in
    ``_check_url_dedup`` that relies on downstream-stage-wins.
    """

    def test_priority_ladder_matches_expected_order(self):
        from ovp_pipeline.source_dedup import ACTIVE_INTAKE_DIRS
        # The downstream-first ladder is part of the contract; if
        # this assertion ever needs editing, ``_check_url_dedup``'s
        # self-match logic must be re-verified at the same time.
        assert ACTIVE_INTAKE_DIRS == (
            "50-Inbox/03-Processed",
            "50-Inbox/02-Processing",
            "50-Inbox/01-Raw",
            "50-Inbox/02-Pinboard",
            "Clippings",
        )

    def test_pairwise_priority_collisions(self, tmp_path):
        """For each adjacent pair (A, B) where A is downstream of
        B, a URL appearing in both must resolve to A's location.
        """
        from ovp_pipeline.source_dedup import (
            ACTIVE_INTAKE_DIRS,
            build_active_url_index,
        )
        for downstream, upstream in zip(ACTIVE_INTAKE_DIRS, ACTIVE_INTAKE_DIRS[1:]):
            tag = f"{downstream}__vs__{upstream}".replace("/", "_")
            url = f"https://x.com/pair/{tag}"
            # Use distinct tmp_paths per pair so the indices don't bleed
            this_pair = tmp_path / tag
            _write_md(this_pair / downstream / "downstream.md", url=url)
            _write_md(this_pair / upstream / "upstream.md", url=url)
            idx = build_active_url_index(this_pair)
            won = idx[url]
            # Use Path.parts to assert the winning file lives under
            # the downstream dir (substring-match would be fragile
            # for the nested ``50-Inbox/03-Processed`` case).
            assert downstream in str(won.relative_to(this_pair)), (
                f"Pair ({downstream} downstream of {upstream}) — winner was {won}"
            )
