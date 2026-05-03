"""Tests for entities/store.py — schema + upsert + history + reads."""

from __future__ import annotations

import sqlite3
import time

from ovp_pipeline.entities.store import EntityStore, init_schema


class TestInitSchema:
    def test_creates_both_tables(self, tmp_path):
        db = tmp_path / "k.db"
        init_schema(db)
        conn = sqlite3.connect(db)
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "entities" in tables
            assert "entity_signals_history" in tables
        finally:
            conn.close()

    def test_idempotent(self, tmp_path):
        db = tmp_path / "k.db"
        init_schema(db)
        # Second call must not error or wipe data — exercises the
        # CREATE TABLE IF NOT EXISTS contract.
        init_schema(db)
        # Insert a row, re-init, row must still be there.
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                "INSERT INTO entities (entity_type, identity_key, "
                "signals_json, fetch_source, first_seen_at, last_fetched_at) "
                "VALUES ('twitter_author', 'foo', '{}', 'test', 'now', 'now')"
            )
            conn.commit()
        finally:
            conn.close()
        init_schema(db)
        conn = sqlite3.connect(db)
        try:
            assert conn.execute(
                "SELECT COUNT(*) FROM entities"
            ).fetchone()[0] == 1
        finally:
            conn.close()


class TestUpsert:
    def test_first_insert(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        e = store.upsert(
            entity_type="twitter_author",
            identity_key="karpathy",
            canonical_name="Andrej Karpathy",
            signals={"followers": 500_000, "partial_author_weight": 67},
            derived_authority=0.67,
            fetch_source="twitterapi.io",
        )
        assert e.entity_id > 0
        assert e.identity_key == "karpathy"
        assert e.derived_authority == 0.67
        assert e.signals["followers"] == 500_000

    def test_second_upsert_updates_in_place(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        e1 = store.upsert(
            entity_type="twitter_author", identity_key="x",
            canonical_name="Mr X", signals={"followers": 100},
            derived_authority=0.10, fetch_source="twitterapi.io",
        )
        first_seen = e1.first_seen_at
        time.sleep(0.01)
        e2 = store.upsert(
            entity_type="twitter_author", identity_key="x",
            canonical_name="Mr X", signals={"followers": 200},
            derived_authority=0.20, fetch_source="twitterapi.io",
        )
        assert e2.entity_id == e1.entity_id     # same row, updated
        assert e2.first_seen_at == first_seen   # original first_seen kept
        assert e2.last_fetched_at >= first_seen
        assert e2.signals["followers"] == 200
        assert e2.derived_authority == 0.20

    def test_upsert_appends_history_row(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        store.upsert(
            entity_type="twitter_author", identity_key="x",
            canonical_name="X", signals={"followers": 100},
            derived_authority=0.1, fetch_source="twitterapi.io",
        )
        time.sleep(0.01)
        e = store.upsert(
            entity_type="twitter_author", identity_key="x",
            canonical_name="X", signals={"followers": 200},
            derived_authority=0.2, fetch_source="twitterapi.io",
        )
        history = list(store.history(e.entity_id))
        # Two upserts → two history rows, newest first.
        assert len(history) == 2
        assert history[0][1]["followers"] == 200
        assert history[1][1]["followers"] == 100

    def test_different_identity_keys_create_separate_entities(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        e_a = store.upsert(
            entity_type="twitter_author", identity_key="a",
            canonical_name="A", signals={}, derived_authority=0.5,
            fetch_source="t",
        )
        e_b = store.upsert(
            entity_type="twitter_author", identity_key="b",
            canonical_name="B", signals={}, derived_authority=0.6,
            fetch_source="t",
        )
        assert e_a.entity_id != e_b.entity_id

    def test_same_key_different_type_are_separate_entities(self, tmp_path):
        # @karpathy on Twitter and karpathy on GitHub are different
        # platform accounts — must not collide.
        store = EntityStore(db_path=tmp_path / "k.db")
        e_tw = store.upsert(
            entity_type="twitter_author", identity_key="karpathy",
            canonical_name="Andrej Karpathy (Twitter)", signals={},
            derived_authority=0.7, fetch_source="twitterapi.io",
        )
        e_gh = store.upsert(
            entity_type="github_user", identity_key="karpathy",
            canonical_name="Andrej Karpathy (GitHub)", signals={},
            derived_authority=0.8, fetch_source="github_rest",
        )
        assert e_tw.entity_id != e_gh.entity_id

    def test_unicode_signals_roundtrip(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        store.upsert(
            entity_type="twitter_author", identity_key="松_老虎",
            canonical_name="松老虎",
            signals={"description": "中文 bio with emoji 🐯"},
            derived_authority=0.5, fetch_source="t",
        )
        round_trip = store.get("twitter_author", "松_老虎")
        assert round_trip is not None
        assert round_trip.canonical_name == "松老虎"
        assert "🐯" in round_trip.signals["description"]


class TestUpsertMany:
    def test_bulk_writes_in_one_transaction(self, tmp_path):
        # Verifies the review-fix invariant: upsert_many opens one
        # connection, runs the whole batch, commits at end.  We can't
        # observe transactions directly via the public API, but we can
        # observe that an exception mid-batch rolls back the previously-
        # inserted rows in the same call.
        store = EntityStore(db_path=tmp_path / "k.db")
        good = [
            {"entity_type": "twitter_author", "identity_key": f"u{i}",
             "canonical_name": f"U{i}", "signals": {"i": i},
             "derived_authority": 0.5, "fetch_source": "t"}
            for i in range(3)
        ]
        bad = [{"entity_type": "twitter_author", "identity_key": "ok",
                "canonical_name": "OK", "signals": {}, "derived_authority": 0.5,
                "fetch_source": "t"},
               # Wrong key triggers TypeError → rollback.
               {"entity_type": "twitter_author", "wrong_field": "boom"}]
        store.upsert_many(good)
        # Three rows after the good batch.
        assert len(store.list_by_type("twitter_author")) == 3
        # Bad batch raises.  Rollback must keep total row count at 3,
        # not 4 (the "ok" row inserted before the failure).
        import pytest as _pytest
        with _pytest.raises(TypeError):
            store.upsert_many(bad)
        assert len(store.list_by_type("twitter_author")) == 3
        assert store.get("twitter_author", "ok") is None


class TestRead:
    def test_get_missing_returns_none(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        assert store.get("twitter_author", "ghost") is None

    def test_list_by_type_sorted_by_authority_desc(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        for h, a in [("low", 0.2), ("high", 0.9), ("mid", 0.5)]:
            store.upsert(
                entity_type="twitter_author", identity_key=h,
                canonical_name=h, signals={}, derived_authority=a,
                fetch_source="t",
            )
        rows = store.list_by_type("twitter_author")
        assert [r.identity_key for r in rows] == ["high", "mid", "low"]

    def test_list_by_type_excludes_other_types(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        store.upsert(
            entity_type="twitter_author", identity_key="a",
            canonical_name="A", signals={}, derived_authority=0.5,
            fetch_source="t",
        )
        store.upsert(
            entity_type="github_project", identity_key="x/y",
            canonical_name="X/Y", signals={}, derived_authority=0.7,
            fetch_source="g",
        )
        rows = store.list_by_type("twitter_author")
        assert len(rows) == 1
        assert rows[0].identity_key == "a"

    def test_history_limit(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        for i in range(5):
            store.upsert(
                entity_type="twitter_author", identity_key="x",
                canonical_name="X", signals={"i": i},
                derived_authority=0.1, fetch_source="t",
            )
            time.sleep(0.005)
        e = store.get("twitter_author", "x")
        assert e is not None
        rows = list(store.history(e.entity_id, limit=3))
        assert len(rows) == 3
        # newest first
        assert rows[0][1]["i"] == 4
