"""BL-071: 03-Processed intake sources are absorb-eligible.

Before BL-071, ``_reject_intake_source_target`` blocked every path
under ``50-Inbox/`` so ``ovp-absorb --file 03-Processed/...md``
crashed with ``ValueError: absorb target is an intake source``.
The post-BL-029 intent (per BL-058: "absorb v2 reads the raw
directly") was never fully wired — only GitHub sources via
:func:`_is_github_source_markdown` were allowed through.

These tests pin the BL-071 contract:

* Clippings/, 01-Raw/, 02-Processing/ stay blocked (work-in-
  progress stages; running absorb here would race the lifecycle).
* 03-Processed/ is allowed when the file passes
  :func:`_is_intake_only_source_markdown` (has frontmatter +
  source URL + non-trivial body + not a deep-dive).
* Empty / no-source / deep-dive files under 03-Processed are
  filtered out so we don't waste a router LLM call on them.
* Directory scans + single-file absorb both honour the new policy.
"""

from __future__ import annotations

from pathlib import Path


def _make_clipping(path: Path, *, body_chars: int = 1000) -> None:
    """Write an intake-only clippings-style source file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "Real article body. " * (max(body_chars, 20) // 20)
    path.write_text(
        "---\n"
        'title: "Test Clipping"\n'
        'source: "https://example.com/article"\n'
        'created: 2026-05-10\n'
        "tags:\n"
        "  - clippings\n"
        "---\n"
        f"\n# Test Clipping\n\n{body}\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# _reject_intake_source_target — narrower in BL-071
# ---------------------------------------------------------------------------


def test_reject_blocks_clippings_dir(tmp_path):
    """Clippings/ is the web-clipper landing zone — files haven't
    been renamed or had images resolved yet.  Block."""
    import pytest
    from ovp_pipeline.auto_evergreen_extractor import _reject_intake_source_target
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(tmp_path)
    clipping = tmp_path / "Clippings" / "raw.md"
    _make_clipping(clipping)
    with pytest.raises(ValueError, match=r"intake source"):
        _reject_intake_source_target(layout, clipping)


def test_reject_blocks_raw_dir(tmp_path):
    """50-Inbox/01-Raw is the pre-processing stage — block."""
    import pytest
    from ovp_pipeline.auto_evergreen_extractor import _reject_intake_source_target
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(tmp_path)
    raw = tmp_path / "50-Inbox" / "01-Raw" / "x.md"
    _make_clipping(raw)
    with pytest.raises(ValueError, match=r"intake source"):
        _reject_intake_source_target(layout, raw)


def test_reject_blocks_processing_dir(tmp_path):
    """50-Inbox/02-Processing is mid-flight — block."""
    import pytest
    from ovp_pipeline.auto_evergreen_extractor import _reject_intake_source_target
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(tmp_path)
    processing = tmp_path / "50-Inbox" / "02-Processing" / "x.md"
    _make_clipping(processing)
    with pytest.raises(ValueError, match=r"intake source"):
        _reject_intake_source_target(layout, processing)


def test_reject_allows_processed_dir(tmp_path):
    """BL-071: 03-Processed is the absorb-input layer by design.
    The guard must NOT raise here — eligibility is decided by
    :func:`_is_intake_only_source_markdown` at the per-target check."""
    from ovp_pipeline.auto_evergreen_extractor import _reject_intake_source_target
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(tmp_path)
    processed = tmp_path / "50-Inbox" / "03-Processed" / "2026-05" / "x.md"
    _make_clipping(processed)
    # Pre-BL-071 this would raise; the fix narrows the guard.
    _reject_intake_source_target(layout, processed)  # no exception


# ---------------------------------------------------------------------------
# _is_intake_only_source_markdown — eligibility detector
# ---------------------------------------------------------------------------


def test_intake_source_eligible_with_source_and_body(tmp_path):
    from ovp_pipeline.auto_evergreen_extractor import _is_intake_only_source_markdown

    f = tmp_path / "ok.md"
    _make_clipping(f, body_chars=500)
    assert _is_intake_only_source_markdown(f) is True


def test_intake_source_rejects_empty_stub(tmp_path):
    """A 03-Processed file with frontmatter but a near-empty body
    is an intake stub (lifecycle moved the file but body resolution
    failed).  Not worth a router LLM call."""
    from ovp_pipeline.auto_evergreen_extractor import _is_intake_only_source_markdown

    f = tmp_path / "stub.md"
    f.write_text(
        "---\ntitle: Stub\nsource: https://example.com/a\n---\n\n# Stub\n",
        encoding="utf-8",
    )
    assert _is_intake_only_source_markdown(f) is False


def test_intake_source_rejects_no_source_url(tmp_path):
    """A hand-written note that happens to live in 03-Processed
    doesn't carry a ``source:`` URL — not an intake product."""
    from ovp_pipeline.auto_evergreen_extractor import _is_intake_only_source_markdown

    f = tmp_path / "handwritten.md"
    f.write_text(
        "---\ntitle: Hand-written\ntags:\n  - note\n---\n\n"
        "# Hand-written\n\n" + ("This is my own note. " * 20) + "\n",
        encoding="utf-8",
    )
    assert _is_intake_only_source_markdown(f) is False


def test_intake_source_rejects_deep_dive_filename(tmp_path):
    """Legacy deep-dive layer (``*_深度解读.md``) is handled by the
    deep-dive glob in :func:`_collect_absorb_targets`; the intake-
    only detector must NOT double-count them."""
    from ovp_pipeline.auto_evergreen_extractor import _is_intake_only_source_markdown

    f = tmp_path / "article_深度解读.md"
    _make_clipping(f, body_chars=1000)
    assert _is_intake_only_source_markdown(f) is False


def test_intake_source_rejects_extraction_status_skipped(tmp_path):
    """BL-066 audit-trail files for empty enrichments carry
    ``extraction_status: skipped`` and have no extractable body —
    same rule applies to the broader detector."""
    from ovp_pipeline.auto_evergreen_extractor import _is_intake_only_source_markdown

    f = tmp_path / "skipped.md"
    f.write_text(
        "---\ntitle: Skipped\nsource: https://example.com/a\n"
        "extraction_status: skipped\n---\n\n# Skipped\n\n"
        + ("Real body. " * 50) + "\n",
        encoding="utf-8",
    )
    assert _is_intake_only_source_markdown(f) is False


def test_intake_source_accepts_source_url_field_too(tmp_path):
    """BL-066 uses ``source_url:`` instead of ``source:``; the
    detector accepts both so github / paper intakes work."""
    from ovp_pipeline.auto_evergreen_extractor import _is_intake_only_source_markdown

    f = tmp_path / "github.md"
    f.write_text(
        "---\ntitle: Repo\nsource_url: https://github.com/x/y\n"
        "---\n\n# Repo\n\n" + ("Repo body. " * 50) + "\n",
        encoding="utf-8",
    )
    assert _is_intake_only_source_markdown(f) is True


# ---------------------------------------------------------------------------
# _collect_absorb_targets — directory mode picks up intake sources
# ---------------------------------------------------------------------------


def test_collect_targets_directory_picks_up_intake_and_deep_dive(tmp_path):
    """When ``--dir 03-Processed/2026-05/`` is passed, both legacy
    deep-dives AND BL-071 intake sources should be returned in
    one list — the operator can absorb a whole month with one call."""
    from ovp_pipeline.auto_evergreen_extractor import _collect_absorb_targets
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(tmp_path)
    month = tmp_path / "50-Inbox" / "03-Processed" / "2026-05"
    month.mkdir(parents=True)

    # Legacy deep-dive.
    deep_dive = month / "2026-05-10_article_深度解读.md"
    deep_dive.write_text(
        "---\ntitle: DD\ntype: deep_dive\n---\n\n# DD\n\n"
        + ("body " * 100) + "\n",
        encoding="utf-8",
    )
    # BL-071 intake-only source.
    intake = month / "2026-05-10_clipping.md"
    _make_clipping(intake, body_chars=500)
    # Stub that should be filtered out.
    stub = month / "2026-05-10_stub.md"
    stub.write_text(
        "---\ntitle: Stub\nsource: https://example.com\n---\n\n# Stub\n",
        encoding="utf-8",
    )

    targets = _collect_absorb_targets(layout, directory=month)
    names = sorted(t.name for t in targets)
    assert "2026-05-10_article_深度解读.md" in names
    assert "2026-05-10_clipping.md" in names
    assert "2026-05-10_stub.md" not in names


def test_collect_targets_directory_skips_intake_detection_outside_processed(tmp_path):
    """Intake-only detection is gated to 03-Processed only.  A
    ``--dir 20-Areas/AI-Research/Topics/2026-05/`` directory still
    follows the legacy deep-dive glob (the BL-071 broader detector
    isn't applied there)."""
    from ovp_pipeline.auto_evergreen_extractor import _collect_absorb_targets
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(tmp_path)
    topic = tmp_path / "20-Areas" / "AI-Research" / "Topics" / "2026-05"
    topic.mkdir(parents=True)
    deep_dive = topic / "2026-05-10_article_深度解读.md"
    deep_dive.write_text(
        "---\ntitle: DD\n---\n\n# DD\n\n" + ("body " * 100) + "\n",
        encoding="utf-8",
    )
    # A clippings-style file at this path is NOT picked up — it
    # shouldn't be in 20-Areas anyway.
    clipping = topic / "2026-05-10_misplaced_clipping.md"
    _make_clipping(clipping, body_chars=500)

    targets = _collect_absorb_targets(layout, directory=topic)
    names = sorted(t.name for t in targets)
    assert names == ["2026-05-10_article_深度解读.md"]


def test_collect_targets_file_path_under_processed_is_allowed(tmp_path):
    """Single-file mode: ``ovp-absorb --file 50-Inbox/03-Processed/.../X.md``
    no longer raises.  When the file passes the eligibility filter,
    it's returned as the single target."""
    from ovp_pipeline.auto_evergreen_extractor import _collect_absorb_targets
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(tmp_path)
    f = tmp_path / "50-Inbox" / "03-Processed" / "2026-05" / "x.md"
    _make_clipping(f)
    assert _collect_absorb_targets(layout, file_path=f) == [f]


def test_collect_targets_file_path_skips_ineligible_processed_stub(tmp_path):
    """Codex P2 #1 regression: single-file mode against an
    ineligible 03-Processed file (empty stub, no source URL,
    extraction_status:skipped) must return an empty target list
    so the caller doesn't waste a router LLM call.  Pre-fix the
    eligibility filter only ran in directory mode; single-file
    passed any 03-Processed path straight through."""
    from ovp_pipeline.auto_evergreen_extractor import _collect_absorb_targets
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(tmp_path)
    stub = tmp_path / "50-Inbox" / "03-Processed" / "2026-05" / "stub.md"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "---\ntitle: Stub\nsource: https://example.com\n---\n\n# Stub\n",
        encoding="utf-8",
    )
    assert _collect_absorb_targets(layout, file_path=stub) == []


def test_collect_targets_file_path_outside_processed_keeps_legacy_behaviour(tmp_path):
    """Files outside 03-Processed (e.g. legacy ``20-Areas/.../topic_深度解读.md``)
    don't go through the BL-071 eligibility filter — they're handled
    by the deep-dive globs as before.  Pre-fix passed everything
    through verbatim; post-fix this branch must still NOT filter
    deep-dives."""
    from ovp_pipeline.auto_evergreen_extractor import _collect_absorb_targets
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(tmp_path)
    dd = tmp_path / "20-Areas" / "AI-Research" / "Topics" / "2026-05" / "x_深度解读.md"
    dd.parent.mkdir(parents=True, exist_ok=True)
    dd.write_text("---\ntitle: DD\n---\n\n# DD\n\n" + ("body " * 100), encoding="utf-8")
    assert _collect_absorb_targets(layout, file_path=dd) == [dd]


def test_process_directory_picks_up_intake_sources(tmp_path):
    """Codex P2 #2 regression: ``run_absorb_workflow(directory=...)``
    with no ``progress_callback`` routes through
    ``AutoEvergreenExtractor.process_directory`` whose own scan
    (pre-BL-071 fix) only saw deep-dives + github.  ``--dir
    03-Processed/<month>/`` would silently process zero clippings.

    This test exercises ``process_directory`` directly with mocked
    ``process_file`` so we only assert the scan-set decision."""
    from ovp_pipeline.auto_evergreen_extractor import AutoEvergreenExtractor
    from ovp_pipeline.runtime import VaultLayout

    month = tmp_path / "50-Inbox" / "03-Processed" / "2026-05"
    month.mkdir(parents=True)
    deep_dive = month / "a_深度解读.md"
    deep_dive.write_text(
        "---\ntitle: DD\n---\n\n# DD\n\n" + ("body " * 100), encoding="utf-8",
    )
    intake = month / "b_clipping.md"
    _make_clipping(intake, body_chars=500)
    stub = month / "c_stub.md"
    stub.write_text(
        "---\ntitle: Stub\nsource: https://example.com\n---\n\n# Stub\n",
        encoding="utf-8",
    )

    class _Logger:
        def log(self, *_a, **_kw):
            pass

    extractor = AutoEvergreenExtractor(tmp_path, _Logger())
    scanned: list[str] = []

    def fake_process_file(file_path, *, dry_run, auto_promote, promote_threshold):
        scanned.append(file_path.name)
        return {
            "file": str(file_path),
            "concepts_extracted": 0,
            "candidates_added": 0,
            "concepts_promoted": 0,
            "concepts_created": 0,
            "concepts_skipped": 0,
        }

    extractor.process_file = fake_process_file  # type: ignore[assignment]
    extractor.process_directory(month)
    # Stub is filtered out; deep-dive + clipping are processed.
    assert sorted(scanned) == ["a_深度解读.md", "b_clipping.md"]


def test_collect_processed_sources_includes_intake_in_recent_scan(tmp_path):
    """Codex P2 #3 regression: ``_collect_processed_github_sources``
    (used by ``--recent N``) used to filter exclusively by the
    github detector.  Recent non-github intake sources were
    invisible to the recent scan.  Now both detectors apply."""
    from ovp_pipeline.auto_evergreen_extractor import _collect_processed_github_sources
    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(tmp_path)
    month = tmp_path / "50-Inbox" / "03-Processed" / "2026-05"
    month.mkdir(parents=True)

    github = month / "a_github.md"
    github.write_text(
        "---\nsource_type: github-project\nsource_url: https://github.com/x/y\n"
        "---\n\n# Repo\n\n" + ("body " * 200), encoding="utf-8",
    )
    clipping = month / "b_clipping.md"
    _make_clipping(clipping, body_chars=500)
    deep_dive = month / "c_深度解读.md"
    deep_dive.write_text(
        "---\ntitle: DD\n---\n\n# DD\n\n" + ("body " * 100), encoding="utf-8",
    )

    sources = _collect_processed_github_sources(layout)
    names = sorted(s.name for s in sources)
    # github source + intake-only clipping picked up.  Deep-dive
    # excluded — that's the legacy 20-Areas scan's job.
    assert "a_github.md" in names
    assert "b_clipping.md" in names
    assert "c_深度解读.md" not in names
