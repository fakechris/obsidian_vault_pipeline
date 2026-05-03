"""SQLite-backed entity store.

Two tables:

``entities``
    Latest snapshot per ``(entity_type, identity_key)``.  ``signals_json``
    is the raw fetched payload (twitterapi.io response, GitHub API
    response, etc.).  ``derived_authority`` is our best 0-1 estimate
    computed from those signals — separated from the raw data so a
    formula change can be re-derived without re-fetching.

``entity_signals_history``
    Append-only time series.  One row per fetch.  Keeps the audit
    trail and makes it possible to compute trends later (followers
    growth rate, star velocity, "active vs going dormant" detection).

Both tables use ``CREATE TABLE IF NOT EXISTS`` so they coexist with
the other knowledge.db schemas.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    entity_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type      TEXT NOT NULL,
    identity_key     TEXT NOT NULL,
    canonical_name   TEXT,
    signals_json     TEXT NOT NULL,
    derived_authority REAL,
    fetch_source     TEXT NOT NULL,
    first_seen_at    TEXT NOT NULL,
    last_fetched_at  TEXT NOT NULL,
    UNIQUE(entity_type, identity_key)
);

CREATE INDEX IF NOT EXISTS idx_entities_type
    ON entities(entity_type);

CREATE INDEX IF NOT EXISTS idx_entities_authority
    ON entities(entity_type, derived_authority DESC);

CREATE TABLE IF NOT EXISTS entity_signals_history (
    history_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id     INTEGER NOT NULL,
    observed_at   TEXT NOT NULL,
    signals_json  TEXT NOT NULL,
    fetch_source  TEXT NOT NULL,
    FOREIGN KEY (entity_id) REFERENCES entities(entity_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_signals_history_entity
    ON entity_signals_history(entity_id, observed_at DESC);
"""


@dataclass(frozen=True, slots=True)
class Entity:
    """A merged-view row from the ``entities`` table."""

    entity_id: int
    entity_type: str
    identity_key: str
    canonical_name: str | None
    signals: dict[str, Any]
    derived_authority: float | None
    fetch_source: str
    first_seen_at: str
    last_fetched_at: str


def init_schema(db_path: Path) -> None:
    """Create the entity tables if they don't exist.

    Idempotent — safe to call on every CLI invocation.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_entity(row: sqlite3.Row) -> Entity:
    try:
        signals = json.loads(row["signals_json"]) if row["signals_json"] else {}
    except json.JSONDecodeError:
        signals = {}
    return Entity(
        entity_id=row["entity_id"],
        entity_type=row["entity_type"],
        identity_key=row["identity_key"],
        canonical_name=row["canonical_name"],
        signals=signals,
        derived_authority=row["derived_authority"],
        fetch_source=row["fetch_source"],
        first_seen_at=row["first_seen_at"],
        last_fetched_at=row["last_fetched_at"],
    )


@dataclass
class EntityStore:
    """Thin DAO over the ``entities`` + ``entity_signals_history`` tables.

    Read paths (``get``, ``list_by_type``, ``history``) are *side-effect
    free*: they will NOT create the SQLite file, NOT initialize the
    schema, and silently return empty results when ``db_path`` doesn't
    exist.  This matters for source-signal providers that consult the
    entity table as a fast path during ingestion: a fresh vault must
    not gain a knowledge.db just because we tried to score a URL.

    Write paths (``upsert``, ``upsert_many``, ``delete``) call
    ``init_schema`` at the top so a first-write to a fresh DB still
    sets up the tables.  Idempotent.
    """

    db_path: Path

    # ---- read ----------------------------------------------------------

    def _open_read(self) -> sqlite3.Connection | None:
        """Open a read-only connection.  Returns ``None`` if the DB file
        doesn't exist yet (fresh vault) — read paths treat that as
        "no entities" rather than "create the schema for me".
        """
        if not Path(self.db_path).exists():
            return None
        try:
            conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro",
                uri=True,
            )
        except sqlite3.OperationalError:
            return None
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, entity_type: str, identity_key: str) -> Entity | None:
        conn = self._open_read()
        if conn is None:
            return None
        try:
            try:
                row = conn.execute(
                    "SELECT * FROM entities WHERE entity_type=? AND identity_key=?",
                    (entity_type, identity_key),
                ).fetchone()
            except sqlite3.OperationalError:
                # Schema not initialized yet — same shape as no-entity.
                return None
            return _row_to_entity(row) if row else None
        finally:
            conn.close()

    def delete(self, entity_type: str, identity_key: str) -> bool:
        """Remove the latest-snapshot row for an entity.

        Returns True if a row was deleted, False if the entity wasn't
        present.  Used by identity_merge's reclassification when a
        ``person`` should be re-filed as ``organization`` (or vice
        versa) — the unique constraint on (entity_type, identity_key)
        means we can't just upsert across types.

        **Preserves ``entity_signals_history`` rows.**  The history
        table is documented as an append-only audit trail at the top
        of this module.  Deleting it would lose the "what did we know
        about langchain when we still classified it as a person"
        record.  The orphan history rows still query cleanly via
        ``history(entity_id)`` and serve as a forensic trail for
        future migrations.
        """
        if not Path(self.db_path).exists():
            return False
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT entity_id FROM entities "
                "WHERE entity_type=? AND identity_key=?",
                (entity_type, identity_key),
            ).fetchone()
            if row is None:
                return False
            entity_id = row[0]
            # NOTE: history rows for this entity_id are deliberately
            # preserved — see docstring above for the append-only
            # invariant rationale.
            conn.execute(
                "DELETE FROM entities WHERE entity_id=?",
                (entity_id,),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def list_by_type(
        self, entity_type: str, *, limit: int | None = None,
    ) -> list[Entity]:
        conn = self._open_read()
        if conn is None:
            return []
        try:
            sql = (
                "SELECT * FROM entities WHERE entity_type=? "
                "ORDER BY derived_authority DESC NULLS LAST"
            )
            if limit is not None:
                sql += f" LIMIT {int(limit)}"
            try:
                rows = conn.execute(sql, (entity_type,)).fetchall()
            except sqlite3.OperationalError:
                return []
            return [_row_to_entity(r) for r in rows]
        finally:
            conn.close()

    def history(
        self, entity_id: int, *, limit: int = 50,
    ) -> Iterator[tuple[str, dict[str, Any], str]]:
        """Yield (observed_at, signals_dict, fetch_source) most-recent first."""
        conn = self._open_read()
        if conn is None:
            return
        try:
            # Tiebreak on history_id DESC so two rows that share an
            # observed_at to the second still come back newest-first.
            try:
                rows = conn.execute(
                    "SELECT observed_at, signals_json, fetch_source "
                    "FROM entity_signals_history WHERE entity_id=? "
                    "ORDER BY observed_at DESC, history_id DESC LIMIT ?",
                    (entity_id, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return
            for observed_at, signals_json, fetch_source in rows:
                try:
                    signals = json.loads(signals_json) if signals_json else {}
                except json.JSONDecodeError:
                    signals = {}
                yield observed_at, signals, fetch_source
        finally:
            conn.close()

    # ---- write ---------------------------------------------------------

    def _upsert_in(
        self,
        conn: sqlite3.Connection,
        *,
        entity_type: str,
        identity_key: str,
        canonical_name: str | None,
        signals: dict[str, Any],
        derived_authority: float | None,
        fetch_source: str,
    ) -> Entity:
        """Atomic UPSERT against an open connection.

        Uses SQLite's native ``INSERT ... ON CONFLICT(...) DO UPDATE``
        (3.24+, available since 2018) so the insert-vs-update decision
        is one round trip, atomically applied, and ``first_seen_at`` is
        preserved on update without a separate SELECT.
        """
        now = _iso_now()
        signals_json = json.dumps(signals, ensure_ascii=False, sort_keys=True)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO entities (entity_type, identity_key, canonical_name, "
            "signals_json, derived_authority, fetch_source, first_seen_at, "
            "last_fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(entity_type, identity_key) DO UPDATE SET "
            "canonical_name=excluded.canonical_name, "
            "signals_json=excluded.signals_json, "
            "derived_authority=excluded.derived_authority, "
            "fetch_source=excluded.fetch_source, "
            "last_fetched_at=excluded.last_fetched_at",
            (entity_type, identity_key, canonical_name, signals_json,
             derived_authority, fetch_source, now, now),
        )
        row = conn.execute(
            "SELECT * FROM entities WHERE entity_type=? AND identity_key=?",
            (entity_type, identity_key),
        ).fetchone()
        conn.execute(
            "INSERT INTO entity_signals_history "
            "(entity_id, observed_at, signals_json, fetch_source) "
            "VALUES (?, ?, ?, ?)",
            (row["entity_id"], now, signals_json, fetch_source),
        )
        return _row_to_entity(row)

    def upsert(
        self,
        *,
        entity_type: str,
        identity_key: str,
        canonical_name: str | None,
        signals: dict[str, Any],
        derived_authority: float | None,
        fetch_source: str,
    ) -> Entity:
        """Insert or update a single entity, plus append a history row.

        Schema is initialized lazily on first write, not on instance
        construction — keeps the read paths side-effect free.
        """
        init_schema(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            entity = self._upsert_in(
                conn,
                entity_type=entity_type, identity_key=identity_key,
                canonical_name=canonical_name, signals=signals,
                derived_authority=derived_authority,
                fetch_source=fetch_source,
            )
            conn.commit()
            return entity
        finally:
            conn.close()

    def upsert_many(
        self, records: Iterable[dict[str, Any]],
    ) -> list[Entity]:
        """Bulk version — single connection + transaction for the whole batch.

        Faster than calling ``upsert`` in a loop (one connection vs N,
        one fsync at the end vs N), and the whole batch is committed
        atomically: a mid-batch failure rolls back, no partial state.
        """
        init_schema(self.db_path)
        conn = sqlite3.connect(self.db_path)
        out: list[Entity] = []
        try:
            for rec in records:
                out.append(self._upsert_in(conn, **rec))
            conn.commit()
            return out
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
