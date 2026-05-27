"""Tests for M23 / BL-094 — digest input collector + preflight.

Each test builds a focused in-memory ``knowledge.db`` fixture so the
preflight + layer collectors are exercised against real SQL without
spinning up the full ``ovp-knowledge-index`` build.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ovp_pipeline.digest_config import DigestConfig
from ovp_pipeline.digest_inputs import (
    DigestInputs,
    collect_digest_inputs,
)


# ── Schema + fixtures ──────────────────────────────────────────


def _make_schema(conn: sqlite3.Connection) -> None:
    """Minimal schema covering every table the collector reads."""
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
            superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
          concept_id TEXT NOT NULL DEFAULT '',
          supersede_reason TEXT NOT NULL DEFAULT ''
        );
CREATE TABLE concept_identity_ledger (
  pack TEXT NOT NULL,
  concept_id TEXT NOT NULL,
  current_cluster_id TEXT NOT NULL DEFAULT '',
  last_matched_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT '',
  lineage_json TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (pack, concept_id)
);
CREATE TRIGGER IF NOT EXISTS trg_community_crystal_seed_ledger
AFTER INSERT ON community_crystals
WHEN NEW.concept_id = ''
BEGIN
  UPDATE community_crystals
     SET concept_id = NEW.cluster_id
   WHERE pack = NEW.pack
     AND cluster_id = NEW.cluster_id
     AND synthesized_at = NEW.synthesized_at;
  INSERT OR IGNORE INTO concept_identity_ledger
      (pack, concept_id, current_cluster_id,
       last_matched_at, created_at, lineage_json)
  VALUES (NEW.pack, NEW.cluster_id, NEW.cluster_id,
          NEW.synthesized_at, NEW.synthesized_at, '[]');
END;
CREATE TRIGGER IF NOT EXISTS trg_community_crystal_seed_ledger_explicit
AFTER INSERT ON community_crystals
WHEN NEW.concept_id <> ''
BEGIN
  INSERT OR IGNORE INTO concept_identity_ledger
      (pack, concept_id, current_cluster_id,
       last_matched_at, created_at, lineage_json)
  VALUES (NEW.pack, NEW.concept_id, NEW.cluster_id,
          NEW.synthesized_at, NEW.synthesized_at, '[]');
END;
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


def _make_vault(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    db_path = tmp_path / "60-Logs" / "knowledge.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    _make_schema(conn)
    conn.commit()
    conn.close()
    return tmp_path, sqlite3.connect(db_path)


@pytest.fixture
def vault(tmp_path: Path):
    """Yield (vault_dir, sqlite3.Connection).  Caller seeds rows
    then runs ``collect_digest_inputs``.

    Connection is closed in teardown so tests can stay short — they
    don't have to remember ``conn.close()`` themselves (gemini-code-
    assist resource-leak nit).  Closing an already-closed sqlite3
    connection is a no-op.
    """
    vault_dir, conn = _make_vault(tmp_path)
    try:
        yield vault_dir, conn
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — defensive teardown
            pass


@pytest.fixture
def utc_config() -> DigestConfig:
    """Pinned-UTC config so tests don't depend on the host system tz."""
    return DigestConfig(tz="UTC")


def _seed_intake(conn: sqlite3.Connection, *, count: int, base_ts: datetime) -> None:
    for i in range(count):
        ts = (base_ts + timedelta(minutes=i)).isoformat()
        payload = json.dumps({"title": f"Article on memory systems #{i}", "author": "alice"})
        conn.execute(
            "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
            ("pipeline.jsonl", "article_processed", f"slug-{i}", "s1", ts, payload),
        )
    conn.commit()


def _seed_evergreen_revision(
    conn: sqlite3.Connection,
    *,
    object_id: str,
    version: int,
    change_type: str,
    derived_at: datetime,
    change_note: str = "lifecycle=promote",
    title: str = "",
) -> None:
    conn.execute(
        "INSERT INTO evergreen_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "research-tech",
            object_id,
            version,
            "## " + object_id,
            change_type,
            "absorber",
            derived_at.isoformat(),
            change_note,
        ),
    )
    if title:
        conn.execute(
            "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("research-tech", object_id, "evergreen", title, "", "", ""),
        )
    conn.commit()


def _seed_cluster(
    conn: sqlite3.Connection,
    *,
    cluster_id: str,
    label: str,
    members: list[str],
) -> None:
    conn.execute(
        "INSERT INTO graph_clusters VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "research-tech",
            cluster_id,
            "community",
            label,
            members[0] if members else "",
            json.dumps(members),
            1.0,
        ),
    )
    conn.commit()


def _seed_community_crystal(
    conn: sqlite3.Connection,
    *,
    cluster_id: str,
    synthesized_at: datetime,
) -> None:
    conn.execute(
        "INSERT INTO community_crystals (pack, cluster_id, body_md, source_evergreen_slugs_json, synthesized_at, llm_model, prompt_version, superseded_by_synthesized_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "research-tech",
            cluster_id,
            "crystal body",
            "[]",
            synthesized_at.isoformat(),
            "test-model",
            "v1",
            "",
        ),
    )
    conn.commit()


# ── Empty / missing DB ─────────────────────────────────────────


def test_missing_db_returns_unavailable_preflight(tmp_path: Path, utc_config):
    """No knowledge.db → every preflight row is unavailable; layers
    are empty.  No exception."""
    result = collect_digest_inputs(tmp_path, "research-tech", config=utc_config)
    assert isinstance(result, DigestInputs)
    assert result.preflight.evergreen_revisions_table == "unavailable"
    assert result.preflight.community_crystals == "unavailable"
    assert result.preflight.any_degraded()
    assert result.intake.intake_events_processed == 0
    assert result.delta.new_evergreens == ()
    assert result.delta.updated_evergreens == ()


def test_empty_db_runs_preflight_without_crashing(vault, utc_config):
    vault_dir, conn = vault
    conn.close()
    result = collect_digest_inputs(vault_dir, "research-tech", config=utc_config)
    # Tables exist but empty → degraded, not unavailable.
    assert result.preflight.community_crystals == "degraded"
    assert result.preflight.graph_clusters == "degraded"


# ── Layer 0 — intake ───────────────────────────────────────────


def test_layer0_counts_allowlisted_events_in_window(vault, utc_config):
    vault_dir, conn = vault
    as_of = datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("UTC"))
    _seed_intake(conn, count=5, base_ts=as_of - timedelta(hours=2))
    conn.close()
    result = collect_digest_inputs(
        vault_dir, "research-tech", as_of=as_of, config=utc_config
    )
    assert result.intake.intake_events_processed == 5
    assert "memory" in dict(result.intake.topic_distribution)


def test_layer0_ignores_events_outside_window(vault, utc_config):
    """An event from 3 days ago is outside the local-day fallback
    window (which is midnight today UTC → now)."""
    vault_dir, conn = vault
    as_of = datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("UTC"))
    _seed_intake(conn, count=3, base_ts=as_of - timedelta(days=3))
    conn.close()
    result = collect_digest_inputs(
        vault_dir, "research-tech", as_of=as_of, config=utc_config
    )
    assert result.intake.intake_events_processed == 0


def test_layer0_cohort_counts_distinct_first_intake_sources(vault, utc_config):
    """BL-106: intake_cohort_sources counts DISTINCT sources whose
    EARLIEST intake is in the window — intake-time axis, de-duped —
    not raw event-window rows."""
    vault_dir, conn = vault
    as_of = datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("UTC"))
    # 3 fresh sources first saved in-window.
    _seed_intake(conn, count=3, base_ts=as_of - timedelta(hours=2))
    conn.close()
    result = collect_digest_inputs(
        vault_dir, "research-tech", as_of=as_of, config=utc_config
    )
    assert result.intake.intake_cohort_sources == 3


def test_layer0_cohort_excludes_resaved_old_source(vault, utc_config):
    """A source first intaken 3 days ago, re-touched in-window:
    event-window count sees the in-window row, but the cohort must
    NOT count it (its FIRST intake predates the window)."""
    vault_dir, conn = vault
    as_of = datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("UTC"))
    old = (as_of - timedelta(days=3)).isoformat()
    inwin = (as_of - timedelta(hours=1)).isoformat()
    for ts in (old, inwin):
        conn.execute(
            "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
            ("pipeline.jsonl", "article_processed", "slug-old",
             "s1", ts, json.dumps({"title": "Old source"})),
        )
    conn.commit()
    conn.close()
    result = collect_digest_inputs(
        vault_dir, "research-tech", as_of=as_of, config=utc_config
    )
    # in-window event row is counted by the event-time layer …
    assert result.intake.intake_events_processed == 1
    # … but the cohort (intake-time, earliest) excludes it.
    assert result.intake.intake_cohort_sources == 0


def test_layer0_window_handles_mixed_timestamp_formats(vault, utc_config):
    """BL-109 (defensive): a space+offset in-window row and a UTC-Z
    in-window row must BOTH be counted.  The pre-BL-109 raw SQL
    `timestamp >= 'YYYY-MM-DDT..+00:00'` string compare drops the
    space+offset row (' ' < 'T') even though it is in-window — the
    latent lexicographic hazard `_utc_iso` documents.  (This is
    correctness hardening / consistency with /ops/today; it was NOT
    the cause of the backdated-probe 0 seen dogfooding BL-106.)"""
    vault_dir, conn = vault
    as_of = datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("UTC"))
    # All tz-explicit (deterministic on any machine).  The
    # space+offset form is lexicographically < the `...T..+00:00`
    # SQL bound (' ' < 'T'), so the pre-BL-109 string compare
    # dropped it even though it is in-window — the exact bug.
    space_offset = "2026-05-13 10:00:00+00:00"   # < 'T' bound → was dropped
    iso_z = "2026-05-13T10:30:00Z"               # event_emitter style
    out_of_window = "2026-05-10T10:00:00Z"       # 3 days before
    for slug, ts in (
        ("slug-space", space_offset),
        ("slug-z", iso_z),
        ("slug-old", out_of_window),
    ):
        conn.execute(
            "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
            ("pipeline.jsonl", "article_processed", slug, "s1", ts,
             json.dumps({"title": f"T {slug}"})),
        )
    conn.commit()
    conn.close()
    result = collect_digest_inputs(
        vault_dir, "research-tech", as_of=as_of, config=utc_config
    )
    # Both in-window rows counted regardless of format; old row excluded.
    assert result.intake.intake_events_processed == 2
    assert result.preflight.audit_events_layer0 == "ok"


def test_layer0_ignores_non_allowlist_event_types(vault, utc_config):
    """An audit row with event_type outside the allowlist doesn't
    inflate the Layer 0 count."""
    vault_dir, conn = vault
    as_of = datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("UTC"))
    ts = (as_of - timedelta(minutes=10)).isoformat()
    conn.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
        ("pipeline.jsonl", "task_dispatched", "s", "x", ts, "{}"),
    )
    conn.commit()
    conn.close()
    result = collect_digest_inputs(
        vault_dir, "research-tech", as_of=as_of, config=utc_config
    )
    assert result.intake.intake_events_processed == 0


# ── Layer 1 — evergreen delta ──────────────────────────────────


def test_layer1_classifies_new_vs_updated(vault, utc_config):
    """version=1 + change_type=created → new; version>1 → updated."""
    vault_dir, conn = vault
    as_of = datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("UTC"))
    _seed_evergreen_revision(
        conn, object_id="evg-a", version=1, change_type="created",
        derived_at=as_of - timedelta(hours=1), title="A",
    )
    _seed_evergreen_revision(
        conn, object_id="evg-b", version=3, change_type="updated",
        derived_at=as_of - timedelta(minutes=30), title="B",
    )
    conn.close()
    result = collect_digest_inputs(
        vault_dir, "research-tech", as_of=as_of, config=utc_config
    )
    assert len(result.delta.new_evergreens) == 1
    assert result.delta.new_evergreens[0].object_id == "evg-a"
    assert len(result.delta.updated_evergreens) == 1
    assert result.delta.updated_evergreens[0].object_id == "evg-b"


def test_layer1_falls_back_on_generic_change_note(vault, utc_config):
    """Default ``lifecycle=promote`` note fails the quality check;
    summary falls back to ``v{n}: {change_type}``."""
    vault_dir, conn = vault
    as_of = datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("UTC"))
    for i in range(10):
        _seed_evergreen_revision(
            conn,
            object_id=f"evg-{i}",
            version=1,
            change_type="promote",
            derived_at=as_of - timedelta(hours=1, minutes=i),
            change_note="lifecycle=promote",
            title=f"Title {i}",
        )
    conn.close()
    result = collect_digest_inputs(
        vault_dir, "research-tech", as_of=as_of, config=utc_config
    )
    assert result.preflight.change_note_quality == "degraded"
    for delta in result.delta.new_evergreens:
        assert delta.change_summary.startswith("v1:")


def test_layer1_uses_meaningful_change_note(vault, utc_config):
    """When the absorb router DOES emit prose, use it verbatim."""
    vault_dir, conn = vault
    as_of = datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("UTC"))
    for i in range(10):
        _seed_evergreen_revision(
            conn,
            object_id=f"evg-{i}",
            version=1,
            change_type="created",
            derived_at=as_of - timedelta(hours=1, minutes=i),
            change_note=f"Added evidence from {i} new sources on memory systems",
        )
    conn.close()
    result = collect_digest_inputs(
        vault_dir, "research-tech", as_of=as_of, config=utc_config
    )
    assert result.preflight.change_note_quality == "ok"
    summary = result.delta.new_evergreens[0].change_summary
    assert "evidence" in summary or "sources" in summary


# ── Layer 2 — connections ──────────────────────────────────────


def test_layer2_attaches_cluster_to_evergreen_revision(vault, utc_config):
    """Cluster membership index joins evergreen_revisions to
    community_crystals via cluster_id."""
    vault_dir, conn = vault
    as_of = datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("UTC"))
    _seed_evergreen_revision(
        conn, object_id="evg-mem-1", version=1, change_type="created",
        derived_at=as_of - timedelta(hours=1), title="Memory note",
    )
    _seed_cluster(
        conn,
        cluster_id="cluster::memory",
        label="memory-systems",
        members=["evg-mem-1"],
    )
    _seed_community_crystal(
        conn,
        cluster_id="cluster::memory",
        synthesized_at=as_of - timedelta(days=8),
    )
    conn.close()
    result = collect_digest_inputs(
        vault_dir, "research-tech", as_of=as_of, config=utc_config
    )
    assert ("cluster::memory", "memory-systems") in result.connections.connected_community_crystals
    delta = result.delta.new_evergreens[0]
    assert delta.cluster_id == "cluster::memory"


# ── Layer 3 — pipeline state ───────────────────────────────────


def test_layer3_flags_stale_crystal_as_unsynthesized(vault, utc_config):
    """A cluster with a crystal but newer evergreens → stale flag,
    counts as unsynthesized.  The bug Codex flagged."""
    vault_dir, conn = vault
    as_of = datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("UTC"))
    # Cluster synthesized 8 days ago.
    _seed_cluster(
        conn,
        cluster_id="cluster::stale",
        label="stale-topic",
        members=[f"evg-s-{i}" for i in range(6)],
    )
    _seed_community_crystal(
        conn,
        cluster_id="cluster::stale",
        synthesized_at=as_of - timedelta(days=8),
    )
    # But 6 new evergreens landed since.
    for i in range(6):
        _seed_evergreen_revision(
            conn, object_id=f"evg-s-{i}", version=1, change_type="created",
            derived_at=as_of - timedelta(hours=2),
        )
    conn.close()
    result = collect_digest_inputs(
        vault_dir, "research-tech", as_of=as_of, config=utc_config
    )
    assert result.pipeline_state.unsynthesized_evergreens == 6
    threshold_clusters = result.pipeline_state.clusters_at_threshold
    assert len(threshold_clusters) == 1
    cid, _label, count, stale = threshold_clusters[0]
    assert cid == "cluster::stale"
    assert stale is True
    assert count == 6


def test_layer3_no_crystal_also_counts_as_unsynthesized(vault, utc_config):
    """A cluster with no crystal at all is unsynthesized regardless
    of staleness."""
    vault_dir, conn = vault
    as_of = datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("UTC"))
    _seed_cluster(
        conn, cluster_id="cluster::fresh", label="fresh-topic",
        members=[f"evg-f-{i}" for i in range(5)],
    )
    for i in range(5):
        _seed_evergreen_revision(
            conn, object_id=f"evg-f-{i}", version=1, change_type="created",
            derived_at=as_of - timedelta(hours=2),
        )
    conn.close()
    result = collect_digest_inputs(
        vault_dir, "research-tech", as_of=as_of, config=utc_config
    )
    assert result.pipeline_state.unsynthesized_evergreens == 5
    cid, _, _, stale = result.pipeline_state.clusters_at_threshold[0]
    assert cid == "cluster::fresh"
    assert stale is False  # no crystal, not "stale"


def test_layer3_threshold_respects_config(vault):
    """A cluster with 3 evergreens is below the default 5 but above
    a custom threshold of 2."""
    vault_dir, conn = vault
    as_of = datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("UTC"))
    _seed_cluster(
        conn, cluster_id="c", label="tiny",
        members=[f"evg-{i}" for i in range(3)],
    )
    for i in range(3):
        _seed_evergreen_revision(
            conn, object_id=f"evg-{i}", version=1, change_type="created",
            derived_at=as_of - timedelta(hours=2),
        )
    conn.close()
    default_cfg = DigestConfig(tz="UTC", cluster_threshold=5)
    custom_cfg = DigestConfig(tz="UTC", cluster_threshold=2)
    r_default = collect_digest_inputs(vault_dir, "research-tech", as_of=as_of, config=default_cfg)
    r_custom = collect_digest_inputs(vault_dir, "research-tech", as_of=as_of, config=custom_cfg)
    assert r_default.pipeline_state.clusters_at_threshold == ()
    assert len(r_custom.pipeline_state.clusters_at_threshold) == 1


# ── Window resolution ──────────────────────────────────────────


def test_window_falls_back_to_local_day_when_no_prior_digest(vault, utc_config):
    """First-ever run: window_start = local-day midnight."""
    vault_dir, conn = vault
    conn.close()
    as_of = datetime(2026, 5, 13, 14, 0, tzinfo=ZoneInfo("UTC"))
    result = collect_digest_inputs(
        vault_dir, "research-tech", as_of=as_of, config=utc_config
    )
    assert result.window_start.hour == 0
    assert result.window_start.minute == 0
    assert result.window_end == as_of


def test_window_uses_last_successful_digest_when_recent(vault, utc_config):
    """Prior digest from this morning → window starts from there
    (mid-day regenerate covers only the gap)."""
    vault_dir, conn = vault
    as_of = datetime(2026, 5, 13, 16, 0, tzinfo=ZoneInfo("UTC"))
    earlier = as_of - timedelta(hours=8)  # 08:00 same day
    conn.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
        (
            "pipeline.jsonl",
            "digest_generated",
            "daily",
            "s",
            earlier.isoformat(),
            "{}",
        ),
    )
    conn.commit()
    conn.close()
    result = collect_digest_inputs(
        vault_dir, "research-tech", as_of=as_of, config=utc_config
    )
    # window_start should equal earlier (within the same day).
    assert result.window_start.replace(microsecond=0) == earlier.replace(microsecond=0)


# ── input_hash ─────────────────────────────────────────────────


def test_input_hash_stable_across_repeated_collects(vault, utc_config):
    """Two collects against unchanged data → same hash.  The
    idempotency gate that BL-095 will rely on."""
    vault_dir, conn = vault
    as_of = datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("UTC"))
    _seed_evergreen_revision(
        conn, object_id="evg-x", version=1, change_type="created",
        derived_at=as_of - timedelta(hours=1),
    )
    conn.close()
    r1 = collect_digest_inputs(vault_dir, "research-tech", as_of=as_of, config=utc_config)
    r2 = collect_digest_inputs(vault_dir, "research-tech", as_of=as_of, config=utc_config)
    assert r1.input_hash() == r2.input_hash()


def test_input_hash_differs_across_windows(vault, utc_config):
    """Same data, different as_of → different hash because window
    boundaries are part of the hash payload."""
    vault_dir, conn = vault
    as_of_1 = datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("UTC"))
    as_of_2 = datetime(2026, 5, 14, 12, 0, tzinfo=ZoneInfo("UTC"))
    conn.close()
    r1 = collect_digest_inputs(vault_dir, "research-tech", as_of=as_of_1, config=utc_config)
    r2 = collect_digest_inputs(vault_dir, "research-tech", as_of=as_of_2, config=utc_config)
    assert r1.input_hash() != r2.input_hash()


def test_input_hash_changes_when_data_changes(vault, utc_config):
    """Adding an evergreen revision should change the hash even
    within the same window."""
    vault_dir, conn = vault
    as_of = datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("UTC"))
    r1 = collect_digest_inputs(vault_dir, "research-tech", as_of=as_of, config=utc_config)

    # Reuse the fixture's open connection rather than opening a
    # second handle that callers must remember to close (gemini-code-
    # assist nit on resource leak).
    _seed_evergreen_revision(
        conn, object_id="evg-new", version=1, change_type="created",
        derived_at=as_of - timedelta(hours=1),
    )
    r2 = collect_digest_inputs(vault_dir, "research-tech", as_of=as_of, config=utc_config)
    assert r1.input_hash() != r2.input_hash()
