"""BL-119 — home banner labels the latest digest honestly.

Pre-BL-119 the renderer hardcoded "Today's digest" regardless of
whether the latest digest file's date matched today.  A vault
browsed at 04:00 PDT — before the 06:00 LaunchAgent fires today's
file — showed yesterday's digest mislabelled as today's.  BL-119
distinguishes "today's digest is here" from "today's hasn't landed
yet, here's the most recent".

These tests pin both branches: the today-file-present case keeps
the original header; the no-today-yet case switches to "Latest
digest" + a small muted explanatory note.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from textwrap import dedent

from ovp_pipeline.ui.view_models._layer1 import _build_latest_digest_info


def _make_digest(folder: Path, date_str: str, body: str = "today body") -> Path:
    p = folder / f"{date_str}-digest-daily.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        dedent(f"""\
            ---
            type: digest
            date: {date_str}
            ---

            {body}
        """),
        encoding="utf-8",
    )
    return p


# ── view-model layer (is_today flag) ─────────────────────────────


def test_is_today_true_when_latest_digest_date_matches_today(tmp_path: Path):
    vault = tmp_path / "vault"
    folder = vault / "40-Resources" / "Generated" / "digests"
    today = datetime.now().strftime("%Y-%m-%d")
    _make_digest(folder, today)

    info = _build_latest_digest_info(vault, requested_pack="")
    assert info["date"] == today
    assert info["is_today"] is True


def test_is_today_false_when_latest_digest_is_yesterday(tmp_path: Path):
    vault = tmp_path / "vault"
    folder = vault / "40-Resources" / "Generated" / "digests"
    # Pick a date guaranteed to differ from today.
    not_today = "1999-01-01"
    _make_digest(folder, not_today)

    info = _build_latest_digest_info(vault, requested_pack="")
    assert info["date"] == not_today
    assert info["is_today"] is False


def test_empty_folder_returns_empty_dict(tmp_path: Path):
    """No digests on disk — the home banner stays hidden.  The
    renderer's ``if digest_info.get("href")`` guard handles the
    empty case, so the view-model just returns ``{}``."""
    vault = tmp_path / "vault"
    (vault / "40-Resources" / "Generated" / "digests").mkdir(
        parents=True, exist_ok=True,
    )
    info = _build_latest_digest_info(vault, requested_pack="")
    assert info == {}


# ── renderer (heading + note) ────────────────────────────────────


def test_renderer_says_today_when_is_today_true():
    """The home renderer is a pure HTML composer — easier to test
    the string directly than spin up an HTTP server.  This test
    pins the conditional header so a future copy-tweak can't
    silently regress it."""
    # Import via the re-export so the package-level circular-import
    # break (commands/_ui_renderers/__init__.py:56) lands before we
    # ask for ``_render_reader_home``.  Importing the function
    # directly from ``commands.reader_home`` triggers a partial-init
    # import error.
    import ovp_pipeline.commands._ui_renderers  # noqa: F401
    from ovp_pipeline.commands.reader_home import _render_reader_home

    payload = {
        "requested_pack": "",
        "pack": "research-tech",
        "top_topics": [],
        "see_all_href": "/topics",
        "recent_crystals": [],
        "recent_total_active": 0,
        "recent_newest_at": "",
        "recent_days": 7,
        "map_supported": False,
        "atlas_total_chains": 0,
        "atlas_generated_at": "",
        "digest": {
            "date": "2026-05-26",
            "href": "/note?path=x.md",
            "teaser": "the morning brief",
            "is_today": True,
        },
    }
    html = _render_reader_home(payload)
    assert "<h2>Today's digest" in html
    # No "Latest digest" fallback heading.
    assert "<h2>Latest digest" not in html
    # No "hasn't been generated yet" note either.
    assert "hasn&#x27;t been generated yet" not in html
    assert "hasn't been generated yet" not in html


def test_renderer_says_latest_when_is_today_false():
    # Import via the re-export so the package-level circular-import
    # break (commands/_ui_renderers/__init__.py:56) lands before we
    # ask for ``_render_reader_home``.  Importing the function
    # directly from ``commands.reader_home`` triggers a partial-init
    # import error.
    import ovp_pipeline.commands._ui_renderers  # noqa: F401
    from ovp_pipeline.commands.reader_home import _render_reader_home

    payload = {
        "requested_pack": "",
        "pack": "research-tech",
        "top_topics": [],
        "see_all_href": "/topics",
        "recent_crystals": [],
        "recent_total_active": 0,
        "recent_newest_at": "",
        "recent_days": 7,
        "map_supported": False,
        "atlas_total_chains": 0,
        "atlas_generated_at": "",
        "digest": {
            "date": "2026-05-25",
            "href": "/note?path=x.md",
            "teaser": "yesterday's brief",
            "is_today": False,
        },
    }
    html = _render_reader_home(payload)
    assert "<h2>Latest digest" in html
    assert "<h2>Today's digest" not in html
    # The explanatory note tells the operator the cron hasn't
    # fired yet — covers both with and without the HTML-escaped
    # apostrophe so a future renderer swap doesn't break the test.
    assert ("hasn't been generated yet" in html
            or "hasn&#x27;t been generated yet" in html)
