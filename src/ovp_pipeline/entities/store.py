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
    """Thin DAO over the ``entities`` + ``entity_signals_history`` tables."""

    db_path: Path

    def __post_init__(self) -> None:
        init_schema(self.db_path)

    # ---- read ----------------------------------------------------------

    def get(self, entity_type: str, identity_key: str) -> Entity | None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM entities WHERE entity_type=? AND identity_key=?",
                (entity_type, identity_key),
            ).fetchone()
            return _row_to_entity(row) if row else None
        finally:
            conn.close()

    def list_by_type(
        self, entity_type: str, *, limit: int | None = None,
    ) -> list[Entity]:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            sql = (
                "SELECT * FROM entities WHERE entity_type=? "
                "ORDER BY derived_authority DESC NULLS LAST"
            )
            if limit is not None:
                sql += f" LIMIT {int(limit)}"
            rows = conn.execute(sql, (entity_type,)).fetchall()
            return [_row_to_entity(r) for r in rows]
        finally:
            conn.close()

    def history(
        self, entity_id: int, *, limit: int = 50,
    ) -> Iterator[tuple[str, dict[str, Any], str]]:
        """Yield (observed_at, signals_dict, fetch_source) most-recent first."""
        conn = sqlite3.connect(self.db_path)
        try:
            # Tiebreak on history_id DESC so two rows that share an
            # observed_at to the second still come back newest-first.
            rows = conn.execute(
                "SELECT observed_at, signals_json, fetch_source "
                "FROM entity_signals_history WHERE entity_id=? "
                "ORDER BY observed_at DESC, history_id DESC LIMIT ?",
                (entity_id, limit),
            ).fetchall()
            for observed_at, signals_json, fetch_source in rows:
                try:
                    signals = json.loads(signals_json) if signals_json else {}
                except json.JSONDecodeError:
                    signals = {}
                yield observed_at, signals, fetch_source
        finally:
            conn.close()

    # ---- write ---------------------------------------------------------

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

        Returns the freshly-read entity (after upsert).
        """
        now = _iso_now()
        signals_json = json.dumps(signals, ensure_ascii=False, sort_keys=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                "SELECT entity_id, first_seen_at FROM entities "
                "WHERE entity_type=? AND identity_key=?",
                (entity_type, identity_key),
            ).fetchone()
            if existing is None:
                cur = conn.execute(
                    "INSERT INTO entities (entity_type, identity_key, "
                    "canonical_name, signals_json, derived_authority, "
                    "fetch_source, first_seen_at, last_fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (entity_type, identity_key, canonical_name, signals_json,
                     derived_authority, fetch_source, now, now),
                )
                entity_id = cur.lastrowid
            else:
                entity_id = existing["entity_id"]
                conn.execute(
                    "UPDATE entities SET canonical_name=?, signals_json=?, "
                    "derived_authority=?, fetch_source=?, last_fetched_at=? "
                    "WHERE entity_id=?",
                    (canonical_name, signals_json, derived_authority,
                     fetch_source, now, entity_id),
                )
            conn.execute(
                "INSERT INTO entity_signals_history "
                "(entity_id, observed_at, signals_json, fetch_source) "
                "VALUES (?, ?, ?, ?)",
                (entity_id, now, signals_json, fetch_source),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM entities WHERE entity_id=?",
                (entity_id,),
            ).fetchone()
            return _row_to_entity(row)
        finally:
            conn.close()

    def upsert_many(
        self, records: Iterable[dict[str, Any]],
    ) -> list[Entity]:
        """Bulk version — same fields as ``upsert`` keys per record."""
        out = []
        for rec in records:
            out.append(self.upsert(**rec))
        return out
