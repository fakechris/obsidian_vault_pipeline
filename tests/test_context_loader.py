"""Tests for M20 / BL-075 context_loader."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ovp_pipeline.context_loader import (
    MAX_BYTES,
    RULES_REL,
    USER_PROFILE_REL,
    clear_cache,
    load_llm_context,
    load_rules,
    load_user_profile,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_cache()
    yield
    clear_cache()


def test_returns_empty_when_both_files_missing(tmp_path: Path):
    """Graceful degradation: vaults without USER.md / OVP_RULES.md
    must keep working — load_llm_context returns ``""`` and existing
    LLM call sites concatenate it without effect."""
    assert load_llm_context(tmp_path) == ""
    assert load_user_profile(tmp_path) == ""
    assert load_rules(tmp_path) == ""


def test_returns_user_profile_only(tmp_path: Path):
    (tmp_path / "00-Polaris").mkdir()
    (tmp_path / USER_PROFILE_REL).write_text(
        "# About Me\nI am a researcher.\n", encoding="utf-8",
    )
    out = load_llm_context(tmp_path)
    assert "# User Profile" in out
    assert "I am a researcher." in out
    assert "# Autonomous Action Rules" not in out


def test_returns_rules_only(tmp_path: Path):
    (tmp_path / RULES_REL).write_text(
        "# Rules\nNever delete.\n", encoding="utf-8",
    )
    out = load_llm_context(tmp_path)
    assert "# Autonomous Action Rules" in out
    assert "Never delete." in out
    assert "# User Profile" not in out


def test_returns_both_when_present(tmp_path: Path):
    (tmp_path / "00-Polaris").mkdir()
    (tmp_path / USER_PROFILE_REL).write_text(
        "# About Me\nFocus: agents.\n", encoding="utf-8",
    )
    (tmp_path / RULES_REL).write_text(
        "# Rules\nAlways log writes.\n", encoding="utf-8",
    )
    out = load_llm_context(tmp_path)
    # User profile must precede rules so the autonomous-action
    # contract is the last thing the LLM sees before its own prompt.
    assert out.index("# User Profile") < out.index("# Autonomous Action Rules")
    assert "Focus: agents." in out
    assert "Always log writes." in out


def test_caches_until_mtime_changes(tmp_path: Path):
    (tmp_path / RULES_REL).write_text("v1", encoding="utf-8")
    first = load_rules(tmp_path)
    assert first == "v1"

    # Same path, no mtime change → cached return.
    assert load_rules(tmp_path) == "v1"

    # Edit file with a forward mtime.  Some platforms (notably APFS
    # on stress'd CI runners) round mtime to the nearest second, so
    # bump it by a full second to make the change observable.
    time.sleep(1.1)
    (tmp_path / RULES_REL).write_text("v2", encoding="utf-8")
    assert load_rules(tmp_path) == "v2"


def test_truncates_oversize_file(tmp_path: Path):
    """A runaway file can't blow LLM token budgets — the loader
    truncates beyond MAX_BYTES and appends a marker."""
    big = "line\n" * (MAX_BYTES // 4)
    (tmp_path / RULES_REL).write_text(big, encoding="utf-8")
    out = load_rules(tmp_path)
    assert len(out.encode("utf-8")) <= MAX_BYTES + 100  # marker padding
    assert "[truncated" in out


def test_unreadable_file_returns_empty(tmp_path: Path):
    """A binary or non-utf8 file degrades to empty rather than
    crashing the LLM call."""
    (tmp_path / RULES_REL).write_bytes(b"\xff\xfe\x00\x00")
    out = load_rules(tmp_path)
    # UnicodeDecodeError → empty
    assert out == ""
