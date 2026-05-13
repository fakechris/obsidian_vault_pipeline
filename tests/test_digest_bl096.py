"""Tests for M23 / BL-096 — Reader integration + mid-day regenerate.

Scope:
* Schema-v2 frontmatter exposes layer counts (``intake_events`` etc.)
  so ``/digests`` can render chips without parsing the body.
* Prompt v2 instructs the LLM to surface concrete maintainer links
  in the "Worth doing next" section.
* The maintainer ``/ops/today`` page renders a "Regenerate digest"
  button (plain HTML check — full POST flow is BL-097 territory).
"""

from __future__ import annotations

import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# Import paths under test
from ovp_pipeline.commands._ui_renderers import _render_digest_regenerate_button
from ovp_pipeline.commands.digest_handler import (
    _DIGEST_SYSTEM_PROMPT_V2,
    _build_frontmatter,
    handle_digest,
)
from ovp_pipeline.commands.task_dispatch import TaskContext
from ovp_pipeline.digest_config import DigestConfig
from ovp_pipeline.digest_inputs import collect_digest_inputs


# ── Reusable fixtures ──────────────────────────────────────────────


class _FakeLLM:
    def __init__(self, response: str = "stub") -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def call(self, system_prompt: str, user_prompt: str, max_tokens: int = 0) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.response


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "50-Inbox" / "02-Tasks").mkdir(parents=True)
    (root / "40-Resources" / "Generated" / "digests").mkdir(parents=True)
    (root / "60-Logs").mkdir(parents=True)
    return root


def _seed_minimal_db(vault: Path) -> None:
    db_path = vault / "60-Logs" / "knowledge.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE audit_events (
            source_log TEXT, event_type TEXT, slug TEXT, session_id TEXT,
            timestamp TEXT, payload_json TEXT
        );
        CREATE TABLE evergreen_revisions (
            pack TEXT, object_id TEXT, version INTEGER, content_md TEXT,
            change_type TEXT, changed_by TEXT, derived_at TEXT, change_note TEXT
        );
        CREATE TABLE objects (
            pack TEXT, object_id TEXT, object_kind TEXT, title TEXT,
            canonical_path TEXT, source_slug TEXT, source_url TEXT
        );
        CREATE TABLE graph_clusters (
            pack TEXT, cluster_id TEXT, cluster_kind TEXT, label TEXT,
            center_object_id TEXT, member_object_ids_json TEXT, score REAL
        );
        CREATE TABLE community_crystals (
            pack TEXT, cluster_id TEXT, body_md TEXT,
            source_evergreen_slugs_json TEXT, synthesized_at TEXT,
            llm_model TEXT, prompt_version TEXT,
            superseded_by_synthesized_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE contradiction_crystals (
            pack TEXT, contradiction_id TEXT, subject_key TEXT, body_md TEXT,
            source_object_ids_json TEXT, synthesized_at TEXT,
            superseded_by_synthesized_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE crystal_scores (
            pack TEXT, crystal_id TEXT, crystal_kind TEXT, score REAL
        );
    """)
    # Seed one evergreen revision + one intake event so _is_no_data
    # returns False.
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO evergreen_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "research-tech", "evg-a", 1, "content",
            "created", "absorber",
            (now - timedelta(hours=1)).isoformat(),
            "Real change description with prose over 20 chars",
        ),
    )
    conn.execute(
        "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("research-tech", "evg-a", "evergreen", "Evergreen A", "", "", ""),
    )
    conn.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
        ("pipeline.jsonl", "article_processed", "slug-1", "s",
         (now - timedelta(minutes=10)).isoformat(),
         '{"title": "Memory systems intro"}'),
    )
    conn.commit()
    conn.close()


# ── Schema v2 frontmatter — layer counts ───────────────────────────


def test_frontmatter_includes_layer_count_fields(tmp_path: Path):
    """``/digests`` reads these directly without parsing the body."""
    vault = _vault(tmp_path)
    _seed_minimal_db(vault)
    inputs = collect_digest_inputs(
        vault, "research-tech",
        as_of=datetime.now(timezone.utc),
        config=DigestConfig(tz="UTC"),
    )
    fm = _build_frontmatter(inputs, "abc123")
    assert re.search(r"^intake_events: \d+$", fm, re.MULTILINE)
    assert re.search(r"^new_evergreens: \d+$", fm, re.MULTILINE)
    assert re.search(r"^updated_evergreens: \d+$", fm, re.MULTILINE)
    assert re.search(r"^unsynthesized: \d+$", fm, re.MULTILINE)
    assert re.search(r"^open_contradictions: \d+$", fm, re.MULTILINE)


def test_frontmatter_layer_counts_match_actual_inputs(tmp_path: Path):
    """The numbers in the frontmatter are the same numbers the
    /digests page will scan against — not a hand-rolled summary."""
    vault = _vault(tmp_path)
    _seed_minimal_db(vault)
    inputs = collect_digest_inputs(
        vault, "research-tech",
        as_of=datetime.now(timezone.utc),
        config=DigestConfig(tz="UTC"),
    )
    fm = _build_frontmatter(inputs, "abc123")
    intake_match = re.search(r"^intake_events: (\d+)$", fm, re.MULTILINE)
    assert intake_match
    assert int(intake_match.group(1)) == inputs.intake.intake_events_processed
    new_match = re.search(r"^new_evergreens: (\d+)$", fm, re.MULTILINE)
    assert int(new_match.group(1)) == len(inputs.delta.new_evergreens)


# ── Prompt v2 includes maintainer-link guidance ───────────────────


def test_prompt_v2_instructs_concrete_maintainer_links():
    """Prompt nudges the LLM to surface clickable maintainer links
    in 'Worth doing next' instead of vague advice."""
    assert "/ops/queue/contradictions" in _DIGEST_SYSTEM_PROMPT_V2
    assert "/ops/cluster?id=" in _DIGEST_SYSTEM_PROMPT_V2
    assert "do not invent ids" in _DIGEST_SYSTEM_PROMPT_V2


def test_prompt_v2_shows_example_form():
    """The prompt includes at least one fully-formed example so the
    LLM has a concrete template to copy."""
    assert "[Resolve contradiction" in _DIGEST_SYSTEM_PROMPT_V2
    assert "[Open cluster" in _DIGEST_SYSTEM_PROMPT_V2


# ── Regenerate button rendering ────────────────────────────────────


def test_regenerate_button_renders_post_form():
    """Button is a real POST form, not a JS-driven link — works
    with no-JS browsers."""
    html = _render_digest_regenerate_button("")
    assert "method='post'" in html
    assert "action='/ops/digest/regenerate'" in html
    assert "<button" in html
    assert "Regenerate" in html


def test_regenerate_button_carries_pack_when_present():
    """The maintainer's current pack flows through as a hidden input
    so the dispatched task runs against the right pack."""
    html = _render_digest_regenerate_button("research-tech")
    assert "name='pack'" in html
    assert "value='research-tech'" in html


def test_regenerate_button_omits_pack_hidden_when_empty():
    """No pack scope → no hidden field (the dispatcher applies its
    default of ``research-tech``)."""
    html = _render_digest_regenerate_button("")
    assert "name='pack'" not in html


# ── End-to-end: handler frontmatter via real handler ───────────────


def test_handler_emits_layer_counts_in_real_run(tmp_path: Path):
    """Run the full handler and assert layer counts land in the
    file the dispatcher would write."""
    vault = _vault(tmp_path)
    _seed_minimal_db(vault)
    task = vault / "50-Inbox" / "02-Tasks" / "DIGEST-daily.md"
    task.write_text("auto", encoding="utf-8")
    ctx = TaskContext(
        vault_dir=vault, task_path=task, prefix="DIGEST", slug="daily",
        body="", pack="research-tech", llm_client=_FakeLLM("body"),
    )
    result = handle_digest(ctx)
    body = result.body_md
    assert "intake_events: 1" in body
    assert "new_evergreens: 1" in body
