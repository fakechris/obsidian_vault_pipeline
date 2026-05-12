"""Tests for ``_build_latest_digest_info`` regressions.

Two regressions caught by review against PR #208:

* rev-bot 206.1: ``latest.stem`` returned the whole filename like
  ``2026-05-11-digest-daily`` (dispatcher writes
  ``{date}-{prefix}-{slug}.md``), so the home banner showed the
  raw filename instead of a date.  Fix: take ``latest.name[:10]``.
* Codex P2: the teaser scanner skipped any line starting with ``#``
  or ``---`` individually, which kept it *inside* the YAML
  frontmatter block and returned ``type: digest`` as the teaser
  for every newly generated digest.  Fix: strip the leading
  frontmatter block first, then scan for the first body paragraph.
"""

from __future__ import annotations

from pathlib import Path

from ovp_pipeline.ui.view_models import _build_latest_digest_info


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "40-Resources" / "Generated" / "digests").mkdir(parents=True)
    return vault


def test_empty_returns_empty_dict(tmp_path: Path):
    vault = _make_vault(tmp_path)
    assert _build_latest_digest_info(vault, requested_pack="") == {}


def test_date_label_strips_dispatcher_suffix(tmp_path: Path):
    """Dispatcher writes ``YYYY-MM-DD-digest-daily.md``; the home
    banner needs the ``YYYY-MM-DD`` part only."""
    vault = _make_vault(tmp_path)
    path = (
        vault / "40-Resources" / "Generated" / "digests"
        / "2026-05-11-digest-daily.md"
    )
    path.write_text(
        "---\ntype: digest\n---\n\n# Digest — 2026-05-11\n\nReal body.\n",
        encoding="utf-8",
    )
    info = _build_latest_digest_info(vault, requested_pack="")
    assert info["date"] == "2026-05-11"
    assert info["date"] != "2026-05-11-digest-daily"


def test_teaser_skips_frontmatter(tmp_path: Path):
    """The teaser must come from the body, not from
    ``type: digest`` in the frontmatter block."""
    vault = _make_vault(tmp_path)
    path = (
        vault / "40-Resources" / "Generated" / "digests"
        / "2026-05-12-digest-daily.md"
    )
    path.write_text(
        "---\n"
        "type: digest\n"
        "schema_version: 1\n"
        "generated_at: 2026-05-12\n"
        "pack: research-tech\n"
        "---\n\n"
        "# Digest — 2026-05-12\n\n"
        "## Tensions worth sitting with\n\n"
        "The first real sentence the reader should see.\n",
        encoding="utf-8",
    )
    info = _build_latest_digest_info(vault, requested_pack="")
    assert "type: digest" not in info["teaser"]
    assert "schema_version" not in info["teaser"]
    assert info["teaser"].startswith("The first real sentence")


def test_teaser_truncates_long_first_paragraph(tmp_path: Path):
    vault = _make_vault(tmp_path)
    long_line = "A" * 400
    path = (
        vault / "40-Resources" / "Generated" / "digests"
        / "2026-05-13-digest-daily.md"
    )
    path.write_text(
        f"---\ntype: digest\n---\n\n# Digest\n\n{long_line}\n",
        encoding="utf-8",
    )
    info = _build_latest_digest_info(vault, requested_pack="")
    assert len(info["teaser"]) <= 220
    assert info["teaser"].endswith("…")


def test_teaser_handles_missing_frontmatter(tmp_path: Path):
    """Legacy digests without a frontmatter block still work."""
    vault = _make_vault(tmp_path)
    path = (
        vault / "40-Resources" / "Generated" / "digests"
        / "2026-05-14-digest-daily.md"
    )
    path.write_text(
        "# Digest — 2026-05-14\n\nLegacy body, no frontmatter.\n",
        encoding="utf-8",
    )
    info = _build_latest_digest_info(vault, requested_pack="")
    assert info["teaser"] == "Legacy body, no frontmatter."
    assert info["date"] == "2026-05-14"
