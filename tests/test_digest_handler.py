"""Tests for M23 / BL-095 daily digest handler.

Covers:

* Prompt v2 — four-question structure
* Input-hash idempotency gate (skip LLM when prior digest matches)
* No-data path (don't fabricate insight from old crystals)
* Schema v2 frontmatter (operator-local timestamps + preflight)
* Audit events (digest_generated, digest_skipped_no_change)

BL-094's input collector is exercised separately in
``test_digest_inputs.py``; this file builds on top of a real
``collect_digest_inputs`` against an in-memory ``knowledge.db``.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ovp_pipeline.commands.digest_handler import (
    DIGESTS_SUBDIR,
    SCHEMA_VERSION,
    _build_digest_user_prompt_v2,
    _enqueue_daily,
    _expected_output_path,
    _is_no_data,
    _latest_digest,
    _read_prior_digest,
    handle_digest,
    main,
)
from ovp_pipeline.commands.task_dispatch import TaskContext
from ovp_pipeline.digest_config import DigestConfig
from ovp_pipeline.digest_inputs import collect_digest_inputs


# ── Fixtures ──────────────────────────────────────────────────────


class _FakeLLM:
    def __init__(self, response: str = "stub digest body") -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def call(
        self, system_prompt: str, user_prompt: str, max_tokens: int = 0
    ) -> str:
        # max_tokens is part of the test-double's keyword API the
        # real LLM client exposes; keep the public name even though
        # this fake doesn't read the value (CodeRabbit nit reverted
        # because the handler calls ``llm.call(..., max_tokens=1200)``).
        del max_tokens
        self.calls.append((system_prompt, user_prompt))
        return self.response


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "50-Inbox" / "02-Tasks").mkdir(parents=True)
    (vault / "40-Resources" / "Generated" / "digests").mkdir(parents=True)
    (vault / "70-Archive" / "tasks").mkdir(parents=True)
    (vault / "60-Logs").mkdir(parents=True)
    return vault


def _make_knowledge_db(vault: Path) -> sqlite3.Connection:
    """Same schema BL-094 tests build — kept local so test_digest_handler
    can run without sharing fixtures across modules."""
    db_path = vault / "60-Logs" / "knowledge.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE audit_events (
            source_log TEXT NOT NULL,
            event_type TEXT NOT NULL,
            slug TEXT,
            session_id TEXT,
            timestamp TEXT NOT NULL,
            payload_json TEXT
        );
        CREATE TABLE evergreen_revisions (
            pack TEXT NOT NULL,
            object_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            content_md TEXT NOT NULL,
            change_type TEXT NOT NULL,
            changed_by TEXT NOT NULL,
            derived_at TEXT NOT NULL,
            change_note TEXT
        );
        CREATE TABLE objects (
            pack TEXT NOT NULL,
            object_id TEXT NOT NULL,
            object_kind TEXT NOT NULL,
            title TEXT NOT NULL,
            canonical_path TEXT,
            source_slug TEXT,
            source_url TEXT
        );
        CREATE TABLE graph_clusters (
            pack TEXT NOT NULL,
            cluster_id TEXT NOT NULL,
            cluster_kind TEXT NOT NULL,
            label TEXT,
            center_object_id TEXT,
            member_object_ids_json TEXT,
            score REAL
        );
        CREATE TABLE community_crystals (
            pack TEXT NOT NULL,
            cluster_id TEXT NOT NULL,
            body_md TEXT NOT NULL,
            source_evergreen_slugs_json TEXT,
            synthesized_at TEXT NOT NULL,
            llm_model TEXT,
            prompt_version TEXT,
            superseded_by_synthesized_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE contradiction_crystals (
            pack TEXT NOT NULL,
            contradiction_id TEXT NOT NULL,
            subject_key TEXT,
            body_md TEXT NOT NULL,
            source_object_ids_json TEXT,
            synthesized_at TEXT NOT NULL,
            superseded_by_synthesized_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE crystal_scores (
            pack TEXT NOT NULL,
            crystal_id TEXT NOT NULL,
            crystal_kind TEXT NOT NULL,
            score REAL NOT NULL
        );
    """)
    conn.commit()
    return conn


def _seed_window_with_signal(
    vault: Path, *, as_of: datetime, evergreens: int = 2
) -> None:
    """Seed enough rows that ``_is_no_data`` returns False."""
    conn = _make_knowledge_db(vault)
    for i in range(evergreens):
        ts = (as_of - timedelta(hours=2, minutes=i)).isoformat()
        conn.execute(
            "INSERT INTO evergreen_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "research-tech", f"evg-{i}", 1, "## content",
                "created", "absorber", ts, "lifecycle=promote",
            ),
        )
        conn.execute(
            "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("research-tech", f"evg-{i}", "evergreen", f"Title {i}", "", "", ""),
        )
    # One intake event in window.
    intake_ts = (as_of - timedelta(minutes=10)).isoformat()
    conn.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
        ("pipeline.jsonl", "article_processed", "slug-x", "s",
         intake_ts, json.dumps({"title": "Memory systems intro"})),
    )
    conn.commit()
    conn.close()


def _ctx(vault: Path, llm: _FakeLLM) -> TaskContext:
    task = vault / "50-Inbox" / "02-Tasks" / "DIGEST-daily.md"
    task.write_text("auto", encoding="utf-8")
    return TaskContext(
        vault_dir=vault, task_path=task, prefix="DIGEST",
        slug="daily", body="", pack="research-tech", llm_client=llm,
    )


# ── No-data path ───────────────────────────────────────────────────


def test_no_data_path_skips_llm(tmp_path: Path):
    """Empty vault: handler MUST NOT call the LLM (token waste)
    and renders an honest "no intake" body."""
    vault = _make_vault(tmp_path)
    # No knowledge.db at all — preflight degrades to unavailable.
    llm = _FakeLLM("must-not-be-called")
    result = handle_digest(_ctx(vault, llm))
    assert llm.calls == []
    # Without audit_events, the body says "Intake data unavailable"
    # rather than the misleading "No new intake" (CodeRabbit fix).
    # Both forms appear under "## Window's intake".
    assert "## Window's intake" in result.body_md
    assert (
        "No new intake in this window." in result.body_md
        or "Intake data unavailable" in result.body_md
    )
    assert result.metadata["skipped_llm"] is True
    assert result.metadata["reason"] == "no_data"
    assert result.subdir == DIGESTS_SUBDIR


def test_is_no_data_detects_real_signal(tmp_path: Path):
    """When any layer has signal, _is_no_data returns False so the
    handler enters the LLM branch.

    Use a pinned mid-day ``as_of`` so the test isn't flaky when run
    just after UTC midnight (where seed-2h would land before the
    window's local-day start).
    """
    vault = _make_vault(tmp_path)
    pinned_as_of = datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)
    seed_at = pinned_as_of - timedelta(minutes=30)
    _seed_window_with_signal(vault, as_of=seed_at)
    inputs = collect_digest_inputs(
        vault, "research-tech",
        as_of=pinned_as_of,
        config=DigestConfig(tz="UTC"),
    )
    assert _is_no_data(inputs) is False


# ── LLM call path ──────────────────────────────────────────────────


def test_calls_llm_with_v2_prompt_when_signal_present(tmp_path: Path):
    """With data, the handler calls the LLM exactly once with a
    prompt that mentions the v2 section headings."""
    vault = _make_vault(tmp_path)
    as_of = datetime.now(timezone.utc) - timedelta(minutes=30)
    _seed_window_with_signal(vault, as_of=as_of, evergreens=2)
    llm = _FakeLLM(
        "## Window's intake\nOne intake.\n\n## Worth doing next\nResolve.\n"
    )
    result = handle_digest(_ctx(vault, llm))
    assert len(llm.calls) == 1
    sys_prompt, user_prompt = llm.calls[0]
    assert "daily-feedback handler" in sys_prompt
    assert "Layer 0" in user_prompt
    assert "Layer 1" in user_prompt
    assert "Layer 3" in user_prompt
    # New body shape — operator-local timestamps in frontmatter.
    assert "schema_version: 2" in result.body_md
    assert "input_hash:" in result.body_md
    assert "Daily Knowledge Feedback" in result.body_md
    assert result.metadata["skipped_llm"] is False
    assert result.metadata["layer1_new"] == 2


def test_frontmatter_contains_preflight_block(tmp_path: Path):
    """Preflight degradation must show up in the frontmatter so the
    Reader / digest-health page can read per-section state."""
    vault = _make_vault(tmp_path)
    as_of = datetime.now(timezone.utc) - timedelta(minutes=30)
    _seed_window_with_signal(vault, as_of=as_of)
    llm = _FakeLLM("body")
    result = handle_digest(_ctx(vault, llm))
    assert "preflight:" in result.body_md
    assert "evergreen_revisions_table:" in result.body_md
    # Generic change_note (lifecycle=promote seed) → degraded.
    assert "change_note_quality: degraded" in result.body_md


def test_audit_event_emitted_on_generate(tmp_path: Path):
    """``digest_generated`` lands in pipeline.jsonl with input_hash + layer counts."""
    vault = _make_vault(tmp_path)
    as_of = datetime.now(timezone.utc) - timedelta(minutes=30)
    _seed_window_with_signal(vault, as_of=as_of)
    llm = _FakeLLM("body")
    handle_digest(_ctx(vault, llm))
    log_path = vault / "60-Logs" / "pipeline.jsonl"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    gen_events = [r for r in rows if r.get("event_type") == "digest_generated"]
    assert len(gen_events) == 1
    payload = gen_events[0]
    assert payload["skipped_llm"] is False
    assert "input_hash" in payload
    assert payload["layer1_new"] == 2


# ── Idempotency gate ───────────────────────────────────────────────


def test_idempotency_skips_llm_when_prior_hash_matches(tmp_path: Path):
    """First run writes a digest; second run with identical inputs
    must NOT call the LLM and must emit digest_skipped_no_change."""
    vault = _make_vault(tmp_path)
    as_of = datetime.now(timezone.utc) - timedelta(minutes=30)
    _seed_window_with_signal(vault, as_of=as_of)
    llm1 = _FakeLLM("first")
    result1 = handle_digest(_ctx(vault, llm1))
    # Persist result1 to disk at the path the gate will look for.
    output_path = _expected_output_path(vault)
    output_path.write_text(result1.body_md, encoding="utf-8")

    # Second run: same data → same hash → skip.
    llm2 = _FakeLLM("should-not-be-called")
    result2 = handle_digest(_ctx(vault, llm2))
    assert llm2.calls == []
    assert result2.metadata["skipped_llm"] is True
    assert result2.metadata["reason"] == "no_change"
    # Same body comes back so the dispatcher's overwrite is a no-op.
    assert result2.body_md == result1.body_md

    # The skip event lands in pipeline.jsonl.
    log_path = vault / "60-Logs" / "pipeline.jsonl"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    skips = [r for r in rows if r.get("event_type") == "digest_skipped_no_change"]
    assert len(skips) == 1


def test_idempotency_calls_llm_when_data_changes(tmp_path: Path):
    """Adding a new evergreen between runs changes the input_hash
    and forces the LLM call."""
    vault = _make_vault(tmp_path)
    as_of = datetime.now(timezone.utc) - timedelta(minutes=30)
    _seed_window_with_signal(vault, as_of=as_of, evergreens=1)
    llm1 = _FakeLLM("first body")
    result1 = handle_digest(_ctx(vault, llm1))
    _expected_output_path(vault).write_text(result1.body_md, encoding="utf-8")

    # New evergreen lands.
    conn = sqlite3.connect(vault / "60-Logs" / "knowledge.db")
    conn.execute(
        "INSERT INTO evergreen_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "research-tech", "evg-new", 1, "## new",
            "created", "absorber",
            (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            "lifecycle=promote",
        ),
    )
    conn.commit()
    conn.close()

    llm2 = _FakeLLM("second body")
    result2 = handle_digest(_ctx(vault, llm2))
    assert len(llm2.calls) == 1
    assert result2.metadata["skipped_llm"] is False


def test_skip_unchanged_disabled_forces_llm(tmp_path: Path):
    """``skip_unchanged: false`` in config disables the gate."""
    vault = _make_vault(tmp_path)
    as_of = datetime.now(timezone.utc) - timedelta(minutes=30)
    _seed_window_with_signal(vault, as_of=as_of)
    (vault / ".ovp").mkdir(exist_ok=True)
    (vault / ".ovp" / "digest.yaml").write_text(
        "skip_unchanged: false\n", encoding="utf-8"
    )

    # First run writes the file.
    llm1 = _FakeLLM("first")
    result1 = handle_digest(_ctx(vault, llm1))
    _expected_output_path(vault).write_text(result1.body_md, encoding="utf-8")

    # Second run: gate is OFF, LLM must be called even with same data.
    llm2 = _FakeLLM("second")
    result2 = handle_digest(_ctx(vault, llm2))
    assert len(llm2.calls) == 1
    assert result2.metadata["skipped_llm"] is False


# ── _read_prior_digest helper ──────────────────────────────────────


def test_read_prior_digest_returns_empty_for_missing_file(tmp_path: Path):
    h, body = _read_prior_digest(tmp_path / "nope.md")
    assert h == ""
    assert body == ""


def test_read_prior_digest_extracts_input_hash(tmp_path: Path):
    path = tmp_path / "d.md"
    path.write_text(
        "---\ntype: digest\ninput_hash: abc123\nfoo: bar\n---\n\n# Body\n",
        encoding="utf-8",
    )
    h, body = _read_prior_digest(path)
    assert h == "abc123"
    assert body.startswith("---\n")


# ── User-focus injection (preserves M20 behavior) ──────────────────


def test_user_focus_flows_into_v2_prompt(tmp_path: Path):
    vault = _make_vault(tmp_path)
    (vault / "00-Polaris").mkdir(exist_ok=True)
    (vault / "00-Polaris" / "USER.md").write_text(
        "# About Me\nFocus: memory systems.\n", encoding="utf-8",
    )
    as_of = datetime.now(timezone.utc) - timedelta(minutes=30)
    _seed_window_with_signal(vault, as_of=as_of)
    llm = _FakeLLM("body")
    handle_digest(_ctx(vault, llm))
    _sys_prompt, user_prompt = llm.calls[0]
    # USER.md → load_user_profile → injected into user prompt block.
    assert "Focus: memory systems" in user_prompt


# ── _build_digest_user_prompt_v2 ───────────────────────────────────


def test_build_user_prompt_omits_empty_data_gracefully(tmp_path: Path):
    """With no signal, the prompt still renders all four layer
    headings but each section honestly reports "no" — the LLM
    is told what's empty rather than having sections invisibly dropped."""
    vault = _make_vault(tmp_path)
    _make_knowledge_db(vault).close()
    inputs = collect_digest_inputs(
        vault, "research-tech",
        as_of=datetime.now(timezone.utc),
        config=DigestConfig(tz="UTC"),
    )
    prompt = _build_digest_user_prompt_v2(inputs, "")
    assert "Layer 0 — Today's intake" in prompt
    assert "Layer 1 — Evergreen delta" in prompt
    assert "Layer 2 — Connections" in prompt
    assert "Layer 3 — Pipeline state" in prompt
    assert "(no intake events in this window)" in prompt
    assert "(no new or updated evergreens in this window)" in prompt


# ── CLI surface (unchanged from M20) ───────────────────────────────


def test_enqueue_daily_creates_task_file(tmp_path: Path):
    vault = _make_vault(tmp_path)
    path = _enqueue_daily(vault)
    assert path.exists()
    assert path.name == "DIGEST-daily.md"


def test_latest_digest_returns_most_recent(tmp_path: Path):
    vault = _make_vault(tmp_path)
    folder = vault / "40-Resources" / "Generated" / "digests"
    older = folder / "2026-05-11-digest-daily.md"
    newer = folder / "2026-05-12-digest-daily.md"
    older.write_text("a", encoding="utf-8")
    newer.write_text("b", encoding="utf-8")
    assert _latest_digest(vault) == newer


def test_latest_digest_returns_none_when_empty(tmp_path: Path):
    vault = _make_vault(tmp_path)
    assert _latest_digest(vault) is None


def test_cli_enqueue_daily(tmp_path: Path, capsys):
    vault = _make_vault(tmp_path)
    rc = main(["--vault-dir", str(vault), "--enqueue-daily"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.endswith("DIGEST-daily.md")


def test_cli_show_latest_empty(tmp_path: Path, capsys):
    vault = _make_vault(tmp_path)
    rc = main(["--vault-dir", str(vault), "--show-latest"])
    assert rc == 1
    assert "(no digests yet)" in capsys.readouterr().out


# ── Schema version + filename surface ──────────────────────────────


def test_schema_version_bumped_to_2():
    assert SCHEMA_VERSION == 2


def test_expected_output_path_matches_dispatcher_filename(tmp_path: Path):
    path = _expected_output_path(tmp_path)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert path.name == f"{today}-digest-daily.md"
    assert path.parent.name == DIGESTS_SUBDIR
