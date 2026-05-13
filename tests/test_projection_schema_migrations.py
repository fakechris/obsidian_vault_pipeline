"""Projection schema migration registry — policy + behaviour tests.

These are the rules that stop "撞版本号 → 用户冷启动等几分钟" from
being the default outcome of a BL that adds a new projection.

Policy rules (also documented in ARCHITECTURE.md):

* Every bump of ``KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION`` registers
  a ``SchemaMigration`` for ``from_version = old`` in
  :data:`ovp_pipeline.knowledge_index.SCHEMA_MIGRATIONS`.
* The migration declares its bucket: ``additive`` / ``recompute`` /
  ``breaking``.  Only ``breaking`` is allowed to drop into a full
  rebuild — and even then the registry entry is what forces the
  contributor to declare it.
* CI fails when a version bump lands without a registry entry —
  see :func:`test_every_version_bump_has_a_migration` below.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ovp_pipeline.knowledge_index import (
    KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION,
    SCHEMA_MIGRATIONS,
    SchemaMigration,
    SchemaMigrationKind,
    _can_delta_migrate,
    _plan_schema_upgrade,
)

# ── policy: every version bump has a migration ─────────────────


# Migration registry is required for versions strictly less than
# the current ``KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION``.  Versions
# 1-5 predate the registry (BL-061 introduced the schema-versioning
# discipline at v7); they're considered "bootstrap" and the policy
# starts enforcing at the first version that landed *after* the
# registry was added.  Adjust this constant only when restating
# the bootstrap window for a future repo split.
_REGISTRY_ENFORCEMENT_FLOOR = 6


def test_every_version_bump_has_a_migration():
    """A schema-version bump without a registered migration is a
    hard error.  The contributor must declare which bucket
    (additive / recompute / breaking) the change is in so the
    operator pays seconds rather than minutes on upgrade.
    """
    expected = set(range(_REGISTRY_ENFORCEMENT_FLOOR, KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION))
    registered = set(SCHEMA_MIGRATIONS.keys())
    missing = sorted(expected - registered)
    assert not missing, (
        f"projection_schema_version is at "
        f"{KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION} but no migration "
        f"is registered for from_version(s) {missing}.  Add an entry "
        f"to ovp_pipeline.knowledge_index.SCHEMA_MIGRATIONS — pick "
        f"the bucket (additive / recompute / breaking) that matches "
        f"the change.  See ARCHITECTURE.md § Projection schema "
        f"changes for the policy."
    )


def test_registered_migrations_have_matching_from_version_keys():
    """Registry key must match the migration's own ``from_version``
    field — otherwise the planner walks the wrong step."""
    mismatches = [(k, m.from_version) for k, m in SCHEMA_MIGRATIONS.items() if k != m.from_version]
    assert not mismatches, (
        f"SCHEMA_MIGRATIONS dict key must equal migration.from_version; "
        f"mismatches: {mismatches}"
    )


def test_registered_migration_kinds_are_canonical():
    for v, m in SCHEMA_MIGRATIONS.items():
        assert isinstance(m, SchemaMigration), f"v{v} entry is not a SchemaMigration"
        assert m.kind in {
            SchemaMigrationKind.ADDITIVE,
            SchemaMigrationKind.RECOMPUTE,
            SchemaMigrationKind.BREAKING,
        }, f"v{v} entry has non-canonical kind {m.kind!r}"
        assert m.reason.strip(), (
            f"v{v} entry must carry a non-empty ``reason`` (use the BL id "
            "+ a short summary, e.g. 'BL-085 — chats projection table')"
        )


# ── planner ────────────────────────────────────────────────────


def test_plan_schema_upgrade_returns_steps_in_order():
    steps, missing = _plan_schema_upgrade(from_version=6, to_version=8)
    assert missing == []
    assert [s.from_version for s in steps] == [6, 7]


def test_plan_schema_upgrade_flags_unregistered_versions():
    # Version 99 doesn't exist in the registry → must show up
    # as missing so the caller falls back to full rebuild.
    steps, missing = _plan_schema_upgrade(from_version=99, to_version=100)
    assert missing == [99]
    assert steps == []


def test_can_delta_migrate_rejects_breaking_steps():
    """A BREAKING step poisons the chain — caller must fall back
    to a full rebuild."""
    additive = SchemaMigration(
        from_version=10,
        kind=SchemaMigrationKind.ADDITIVE,
        reason="test",
        runner=lambda _conn, _vault: None,
    )
    breaking = SchemaMigration(
        from_version=11,
        kind=SchemaMigrationKind.BREAKING,
        reason="test",
        runner=lambda _conn, _vault: None,
    )
    assert _can_delta_migrate([additive])
    assert not _can_delta_migrate([additive, breaking])


def test_can_delta_migrate_rejects_empty_step_list():
    """Empty step list → fall back to full rebuild rather than
    silently succeed without doing anything."""
    assert not _can_delta_migrate([])


# ── end-to-end: 7 → 8 chats migration runs cleanly ─────────────


def _make_v7_db(db_path: Path) -> None:
    """Build a minimal v7-shaped knowledge.db (no chats table) so
    the delta-migration path has something to upgrade."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE pages_index (
              slug TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              note_type TEXT NOT NULL,
              path TEXT NOT NULL,
              day_id TEXT NOT NULL,
              frontmatter_json TEXT NOT NULL,
              body TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE page_fts USING fts5(
              slug UNINDEXED, title, body, tokenize='trigram'
            );
            CREATE TABLE projection_metadata (
              projection_kind TEXT PRIMARY KEY,
              authority_schema_version INTEGER NOT NULL,
              projection_schema_version INTEGER NOT NULL,
              built_at TEXT NOT NULL
            );
            INSERT INTO projection_metadata VALUES
              ('knowledge_db', 1, 7, '2026-04-01T00:00:00Z');
            """)


def test_7_to_8_migration_creates_chats_table(tmp_path: Path):
    """End-to-end: a v7 DB upgrades to v8 via the registered
    migration without a full rebuild — chats table appears, no
    file rescan."""
    from ovp_pipeline.knowledge_index import _migrate_7_to_8_chats

    db_path = tmp_path / "60-Logs" / "knowledge.db"
    _make_v7_db(db_path)

    # Run the migration directly (the same way _run_delta_migrations
    # would invoke it).  ``vault_dir`` doesn't need real chats on
    # disk — the projection rebuild best-effort handles an empty
    # corpus.
    vault_dir = tmp_path
    with sqlite3.connect(db_path) as conn:
        _migrate_7_to_8_chats(conn, vault_dir)
        conn.commit()

    with sqlite3.connect(db_path) as conn:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='chats'"
            ).fetchall()
        ]
        assert tables == ["chats"]
        # Indexes also created.
        indexes = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='chats'"
            ).fetchall()
        ]
        assert "idx_chats_pack_last" in indexes
        assert "idx_chats_visibility" in indexes
        assert "idx_chats_status" in indexes


# ── _ensure_knowledge_db prefers delta over full rebuild ──────


def test_ensure_knowledge_db_takes_delta_path_on_version_bump(tmp_path: Path, monkeypatch):
    """Pinned regression: a v7 DB with no chats table must NOT
    trigger ``rebuild_knowledge_index`` — that's the slow
    full-rescan path that caused the BL-085 1-2 minute regression.
    The version-only mismatch goes through the delta-migration
    fast path instead."""
    import ovp_pipeline.knowledge_index as ki

    # Build a v7-shaped vault layout the resolver will accept.
    vault = tmp_path / "vault"
    (vault / "10-Knowledge" / "Atlas").mkdir(parents=True)
    (vault / "10-Knowledge" / "Evergreen").mkdir(parents=True)
    (vault / "20-Areas").mkdir()
    (vault / "50-Inbox").mkdir()
    (vault / "60-Logs").mkdir()
    db_path = vault / "60-Logs" / "knowledge.db"
    _make_v7_db(db_path)

    # Loud sentinel: if anything in _ensure_knowledge_db falls
    # through to the full-rebuild path, this monkeypatch fires and
    # the test fails with a clear message.
    rebuild_calls: list[str] = []

    def _explode(*_args, **_kwargs):
        rebuild_calls.append("rebuild_knowledge_index_invoked")
        raise AssertionError(
            "rebuild_knowledge_index() should NOT run when every "
            "schema-version step has an additive/recompute "
            "migration registered — the delta path skips it"
        )

    monkeypatch.setattr(ki, "rebuild_knowledge_index", _explode)

    # Pretend the schema-incompatible early-out + authority bump
    # don't fire so we're testing the version-only path.
    monkeypatch.setattr(ki, "_ensure_authority_schema_version", lambda _vault: 1)
    monkeypatch.setattr(
        ki,
        "_knowledge_db_supports_pack_schema",
        lambda _db_path: True,
    )

    ki._ensure_knowledge_db(vault)

    # rebuild was NOT called; chats table now exists.
    assert rebuild_calls == []
    with sqlite3.connect(db_path) as conn:
        chats_table = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='chats'"
        ).fetchone()[0]
        assert chats_table == 1
        new_version = conn.execute(
            "SELECT projection_schema_version FROM projection_metadata "
            "WHERE projection_kind = 'knowledge_db'"
        ).fetchone()[0]
        assert new_version == KNOWLEDGE_DB_PROJECTION_SCHEMA_VERSION


# ── codex/CodeRabbit fixes — marker kind + connection hygiene ─


def test_delta_migration_writes_metadata_only_marker(tmp_path: Path, monkeypatch):
    """Pinned regression for codex P2 — the marker written for the
    delta path must use a kind that ``ProjectionRepairMarker.from_dict``
    accepts, otherwise the marker is silently dropped on replay
    and ``close_projection_repair_marker`` no-ops, leaving the
    marker to re-fire on every subsequent start."""
    import ovp_pipeline.knowledge_index as ki
    from ovp_pipeline.projection_lifecycle import (
        list_projection_repair_markers,
    )

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    db_path = vault / "60-Logs" / "knowledge.db"
    _make_v7_db(db_path)

    monkeypatch.setattr(ki, "_ensure_authority_schema_version", lambda _vault: 1)
    monkeypatch.setattr(ki, "_knowledge_db_supports_pack_schema", lambda _db_path: True)
    monkeypatch.setattr(
        ki,
        "rebuild_knowledge_index",
        lambda *_a, **_kw: pytest_fail("full rebuild path should not run"),
    )
    # ``rebuild_knowledge_index`` shouldn't run; we add a sentinel
    # via the helper below since pytest is intentionally not imported.

    ki._ensure_knowledge_db(vault)

    # The marker was visible to projection_lifecycle (not silently
    # dropped on parse) AND was closed by the surrounding
    # ``try``/``finally``.  Closed markers stay in the audit log
    # but ``status='closed'``.
    markers = list_projection_repair_markers(vault)
    delta_markers = [
        m
        for m in markers
        if m.kind == "metadata_only" and m.caused_by == "ensure_knowledge_db_current"
    ]
    assert delta_markers, (
        "delta migration should have written a metadata_only marker "
        "that ProjectionRepairMarker.from_dict can parse"
    )
    assert all(m.status == "closed" for m in delta_markers), (
        "the surrounding try/finally must close the marker — "
        "open markers re-fire on every subsequent ensure_knowledge_db"
    )


def pytest_fail(msg: str):
    raise AssertionError(msg)


def test_chats_seed_failure_keeps_projection_metadata_at_old_version(
    tmp_path: Path, monkeypatch
):
    """CodeRabbit Major regression — if ``rebuild_chats_projection``
    raises during a 7→8 migration, the failure must propagate.
    ``_run_delta_migrations`` only writes the new
    ``projection_schema_version`` after every step's runner
    returns, so the version stays at the old value and the next
    start retries cleanly."""
    import ovp_pipeline.chats_projection as cp
    import ovp_pipeline.knowledge_index as ki

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    db_path = vault / "60-Logs" / "knowledge.db"
    _make_v7_db(db_path)

    monkeypatch.setattr(ki, "_ensure_authority_schema_version", lambda _vault: 1)
    monkeypatch.setattr(ki, "_knowledge_db_supports_pack_schema", lambda _db_path: True)
    monkeypatch.setattr(
        ki,
        "rebuild_knowledge_index",
        lambda *_a, **_kw: pytest_fail("delta path should fail, not full-rebuild fallback"),
    )

    def _boom(*_args, **_kwargs):
        raise RuntimeError("synthetic disk failure during chats seed")

    monkeypatch.setattr(cp, "rebuild_chats_projection", _boom)

    # The runner re-raises; ensure_knowledge_db propagates.
    import pytest

    with pytest.raises(RuntimeError, match="synthetic disk failure"):
        ki._ensure_knowledge_db(vault)

    # projection_schema_version stays at v7 — next start retries.
    with sqlite3.connect(db_path) as conn:
        version = conn.execute(
            "SELECT projection_schema_version FROM projection_metadata "
            "WHERE projection_kind = 'knowledge_db'"
        ).fetchone()[0]
        assert version == 7
