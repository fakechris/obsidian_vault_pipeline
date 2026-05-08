"""Tests for ``ovp-backfill-objects-source-url``.

The CLI walks ``objects`` rows with empty ``source_url`` and runs
three resolution strategies in order: frontmatter → provenance →
audit_events.  These tests exercise each strategy in isolation and
verify the writer is idempotent + dry-run safe.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ovp_pipeline.commands.backfill_objects_source_url import main
from ovp_pipeline.knowledge_index import rebuild_knowledge_index


def _seed_min_evergreen(vault: Path, slug: str, *, source_url: str = "") -> Path:
    """Write a minimal evergreen file + run the rebuild so its
    ``objects`` row exists.  ``source_url`` defaults to empty so
    the CLI has something to backfill.
    """
    eg = vault / "10-Knowledge" / "Evergreen"
    eg.mkdir(parents=True, exist_ok=True)
    fm_lines = [
        f"note_id: {slug}",
        f'title: "{slug.replace("-", " ").title()}"',
        "type: evergreen",
        "entity_type: fact",
        "date: 2026-04-13",
    ]
    if source_url:
        fm_lines.append(f'source_url: "{source_url}"')
    file_path = eg / f"{slug}.md"
    file_path.write_text(
        "---\n" + "\n".join(fm_lines) + "\n---\n\nBody for " + slug + ".\n",
        encoding="utf-8",
    )
    return file_path


def _seed_db_with_empty_source_url(temp_vault: Path) -> str:
    """Seed evergreen + rebuild + force the ``objects.source_url``
    column to empty.  Returns the inferred pack name."""
    rebuild_knowledge_index(temp_vault)
    db_path = temp_vault / "60-Logs" / "knowledge.db"
    if not db_path.exists():  # alt layout used by some test fixtures
        db_path = temp_vault / "knowledge.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE objects SET source_url = ''")
        conn.commit()
        pack = conn.execute("SELECT pack FROM objects LIMIT 1").fetchone()
    return str(pack[0]) if pack else ""


def _read_source_url(temp_vault: Path, object_id: str) -> str:
    db_path = temp_vault / "60-Logs" / "knowledge.db"
    if not db_path.exists():
        db_path = temp_vault / "knowledge.db"
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT source_url FROM objects WHERE object_id = ?",
            (object_id,),
        ).fetchone()
    return str(row[0]) if row else ""


def test_strategy_1_frontmatter_resolves_url(temp_vault, monkeypatch, capsys):
    """Frontmatter-resident ``source_url:`` populates the column."""
    _seed_min_evergreen(temp_vault, "alpha", source_url="https://example.com/alpha")
    _seed_db_with_empty_source_url(temp_vault)

    monkeypatch.setenv("OVP_VAULT_DIR", str(temp_vault))
    rc = main([])
    assert rc == 0
    assert _read_source_url(temp_vault, "alpha") == "https://example.com/alpha"

    out = capsys.readouterr().out
    assert "resolved from frontmatter:       1" in out
    assert "rows written to objects:         1" in out


def test_strategy_2_provenance_fallback(temp_vault, monkeypatch, capsys):
    """When frontmatter has no URL, a prior ``provenance`` row wins.

    Seeds an evergreen with no ``source_url:`` in frontmatter,
    then writes a hand-crafted ``provenance`` row carrying a URL —
    the CLI must pick that up via strategy 2 before falling
    through to the audit-event walk.
    """
    _seed_min_evergreen(temp_vault, "beta")
    pack = _seed_db_with_empty_source_url(temp_vault)
    db_path = temp_vault / "60-Logs" / "knowledge.db"
    if not db_path.exists():
        db_path = temp_vault / "knowledge.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO provenance
              (pack, object_id, source_url, source_fingerprint,
               derived_via_stage, derived_at, parent_object_id, metadata_json)
            VALUES (?, ?, ?, '', 'extract', '2026-04-28T12:00:00Z', NULL, '{}')
            """,
            (pack, "beta", "https://github.com/example/beta"),
        )
        conn.commit()

    monkeypatch.setenv("OVP_VAULT_DIR", str(temp_vault))
    rc = main([])
    assert rc == 0
    assert _read_source_url(temp_vault, "beta") == "https://github.com/example/beta"

    out = capsys.readouterr().out
    assert "resolved from provenance:        1" in out


def test_strategy_3_audit_events_walk(temp_vault, monkeypatch, capsys):
    """Audit-event walk catches evergreens with neither frontmatter
    nor provenance evidence — the same path
    ``ovp-backfill-provenance`` uses, but writes to the SQL column
    directly.
    """
    _seed_min_evergreen(temp_vault, "gamma")

    # Stage a 03-Processed source file that the audit-event payload
    # will reference; its frontmatter ``source:`` URL is what should
    # bubble up.
    proc = temp_vault / "50-Inbox" / "03-Processed" / "2026-04"
    proc.mkdir(parents=True, exist_ok=True)
    source_file = proc / "2026-04-28_Source_For_Gamma.md"
    source_file.write_text(
        '---\ntitle: "Source for gamma"\nsource: "https://gamma.example.com/post"\n---\n\nBody.\n',
        encoding="utf-8",
    )

    _seed_db_with_empty_source_url(temp_vault)
    db_path = temp_vault / "60-Logs" / "knowledge.db"
    if not db_path.exists():
        db_path = temp_vault / "knowledge.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO audit_events
              (source_log, event_type, slug, session_id, timestamp, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "pipeline.jsonl",
                "evergreen_auto_promoted",
                "gamma",
                "test-session",
                "2026-04-28T12:14:03Z",
                json.dumps({
                    "concept": "gamma",
                    "source": source_file.name,
                }),
            ),
        )
        conn.commit()

    monkeypatch.setenv("OVP_VAULT_DIR", str(temp_vault))
    rc = main([])
    assert rc == 0
    assert _read_source_url(temp_vault, "gamma") == "https://gamma.example.com/post"

    out = capsys.readouterr().out
    assert "resolved from audit_events:      1" in out


def test_dry_run_writes_nothing(temp_vault, monkeypatch, capsys):
    """``--dry-run`` reports the same per-strategy hits but leaves
    the SQL column untouched."""
    _seed_min_evergreen(temp_vault, "alpha", source_url="https://example.com/alpha")
    _seed_db_with_empty_source_url(temp_vault)

    monkeypatch.setenv("OVP_VAULT_DIR", str(temp_vault))
    rc = main(["--dry-run"])
    assert rc == 0
    # Column still empty after a dry run.
    assert _read_source_url(temp_vault, "alpha") == ""

    out = capsys.readouterr().out
    assert "(dry-run)" in out
    assert "resolved from frontmatter:       1" in out
    assert "rows written to objects:         0" in out


def test_unresolved_objects_left_alone(temp_vault, monkeypatch, capsys):
    """Objects with no resolution path stay empty + get counted as
    unresolved.  No spurious writes that overwrite an already-correct
    populated row."""
    _seed_min_evergreen(temp_vault, "delta")  # no frontmatter source_url
    _seed_db_with_empty_source_url(temp_vault)

    monkeypatch.setenv("OVP_VAULT_DIR", str(temp_vault))
    rc = main([])
    assert rc == 0
    assert _read_source_url(temp_vault, "delta") == ""

    out = capsys.readouterr().out
    assert "unresolved (left empty):         1" in out
    assert "rows written to objects:         0" in out


def test_idempotent_second_run(temp_vault, monkeypatch, capsys):
    """A second back-to-back run finds nothing to do — the first
    run consumed every empty row."""
    _seed_min_evergreen(temp_vault, "alpha", source_url="https://example.com/alpha")
    _seed_db_with_empty_source_url(temp_vault)

    monkeypatch.setenv("OVP_VAULT_DIR", str(temp_vault))
    main([])
    capsys.readouterr()  # discard

    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out
    # First run filled alpha; second run finds zero rows to scan.
    assert "empty source_url scanned:        0" in out
    assert "rows written to objects:         0" in out


def test_write_provenance_flag_inserts_ingest_row(temp_vault, monkeypatch):
    """``--write-provenance`` inserts a fresh ``stage='ingest'`` row
    when one doesn't already exist for that ``(pack, object_id,
    source_url)`` triple.

    The rebuild that precedes the test runs writes its own ingest
    row from frontmatter — to prove the flag is wired correctly we
    wipe BOTH ``objects.source_url`` AND every provenance row before
    running the backfill, then assert a freshly-tagged row appears.
    """
    _seed_min_evergreen(temp_vault, "alpha", source_url="https://example.com/alpha")
    pack = _seed_db_with_empty_source_url(temp_vault)

    db_path = temp_vault / "60-Logs" / "knowledge.db"
    if not db_path.exists():
        db_path = temp_vault / "knowledge.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM provenance")
        conn.commit()

    monkeypatch.setenv("OVP_VAULT_DIR", str(temp_vault))
    rc = main(["--write-provenance"])
    assert rc == 0

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT source_url, derived_via_stage, metadata_json
            FROM provenance
            WHERE pack = ? AND object_id = 'alpha'
            ORDER BY derived_at DESC
            """,
            (pack,),
        ).fetchall()
    assert rows, "--write-provenance must insert an ingest row"
    assert rows[0][0] == "https://example.com/alpha"
    assert rows[0][1] == "ingest"
    assert "ovp-backfill-objects-source-url" in (rows[0][2] or "")
