"""BL-061: evergreen revision history.

Tests for ``truth_store_writers.record_evergreen_revision``:

1. Schema is wired into ``TRUTH_STORE_SCHEMA`` so a fresh ``rebuild_knowledge_index``
   creates the ``evergreen_revisions`` table.
2. ``record_evergreen_revision`` assigns monotonic per-(pack, object_id) versions,
   writes content_md verbatim, accepts every documented ``change_type`` constant,
   and is best-effort (returns ``None`` instead of raising on missing schema).
3. End-to-end: ``review_candidate_concept`` writes a ``change_type='promote'``
   revision row alongside the BL-056 ``stage='promote'`` provenance row.
"""

from __future__ import annotations

import sqlite3

import pytest


# ---------------------------------------------------------------------------
# Schema is wired
# ---------------------------------------------------------------------------


def test_schema_includes_evergreen_revisions(tmp_path):
    """Fresh ``rebuild_knowledge_index`` materialises the table."""
    from ovp_pipeline.knowledge_index import rebuild_knowledge_index
    from ovp_pipeline.runtime import VaultLayout

    vault = tmp_path / "vault"
    (vault / "10-Knowledge" / "Evergreen").mkdir(parents=True)
    (vault / "10-Knowledge" / "Evergreen" / "Alpha.md").write_text(
        "---\nnote_id: alpha\ntitle: Alpha\ntype: evergreen\ndate: 2026-04-13\n"
        '---\n\n# Alpha\n\nBody.\n',
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)

    db_path = VaultLayout.from_vault(vault).knowledge_db
    with sqlite3.connect(db_path) as conn:
        cols = [
            row[1]
            for row in conn.execute("PRAGMA table_info(evergreen_revisions)").fetchall()
        ]
    assert cols == [
        "pack",
        "object_id",
        "version",
        "content_md",
        "change_type",
        "changed_by",
        "derived_at",
        "change_note",
    ]


# ---------------------------------------------------------------------------
# record_evergreen_revision unit behaviour
# ---------------------------------------------------------------------------


_SCHEMA_FIXTURE = """
CREATE TABLE evergreen_revisions (
  pack TEXT NOT NULL,
  object_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  content_md TEXT NOT NULL,
  change_type TEXT NOT NULL,
  changed_by TEXT NOT NULL DEFAULT '',
  derived_at TEXT NOT NULL,
  change_note TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, object_id, version)
);
"""


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "k.db"
    c = sqlite3.connect(db)
    c.executescript(_SCHEMA_FIXTURE)
    yield c
    c.close()


def test_first_revision_is_version_1(conn):
    from ovp_pipeline.truth_store_writers import (
        CHANGE_TYPE_PROMOTE,
        record_evergreen_revision,
    )

    version = record_evergreen_revision(
        conn,
        pack="research-tech",
        object_id="alpha",
        content_md="# Alpha\n\nBody v1.\n",
        change_type=CHANGE_TYPE_PROMOTE,
    )
    assert version == 1

    row = conn.execute(
        "SELECT version, content_md, change_type FROM evergreen_revisions"
    ).fetchone()
    assert row == (1, "# Alpha\n\nBody v1.\n", "promote")


def test_versions_increment_monotonically_per_object(conn):
    from ovp_pipeline.truth_store_writers import (
        CHANGE_TYPE_LLM_REWRITE,
        CHANGE_TYPE_PROMOTE,
        record_evergreen_revision,
    )

    v1 = record_evergreen_revision(
        conn, pack="p", object_id="alpha",
        content_md="v1", change_type=CHANGE_TYPE_PROMOTE,
    )
    v2 = record_evergreen_revision(
        conn, pack="p", object_id="alpha",
        content_md="v2", change_type=CHANGE_TYPE_LLM_REWRITE,
    )
    v3 = record_evergreen_revision(
        conn, pack="p", object_id="alpha",
        content_md="v3", change_type=CHANGE_TYPE_PROMOTE,
    )
    assert (v1, v2, v3) == (1, 2, 3)


def test_versions_independent_across_objects(conn):
    """Two evergreens in the same pack each start at v1."""
    from ovp_pipeline.truth_store_writers import (
        CHANGE_TYPE_PROMOTE,
        record_evergreen_revision,
    )

    a1 = record_evergreen_revision(
        conn, pack="p", object_id="alpha",
        content_md="a1", change_type=CHANGE_TYPE_PROMOTE,
    )
    a2 = record_evergreen_revision(
        conn, pack="p", object_id="alpha",
        content_md="a2", change_type=CHANGE_TYPE_PROMOTE,
    )
    b1 = record_evergreen_revision(
        conn, pack="p", object_id="beta",
        content_md="b1", change_type=CHANGE_TYPE_PROMOTE,
    )

    assert a1 == 1 and a2 == 2 and b1 == 1


def test_changed_by_and_change_note_persisted(conn):
    from ovp_pipeline.truth_store_writers import (
        CHANGE_TYPE_PROMOTE,
        record_evergreen_revision,
    )

    record_evergreen_revision(
        conn, pack="p", object_id="alpha",
        content_md="x", change_type=CHANGE_TYPE_PROMOTE,
        changed_by="ui:review_candidate_concept",
        change_note="lifecycle=promote | merged_from=alpha-candidate",
    )
    row = conn.execute(
        "SELECT changed_by, change_note FROM evergreen_revisions"
    ).fetchone()
    assert row == (
        "ui:review_candidate_concept",
        "lifecycle=promote | merged_from=alpha-candidate",
    )


def test_explicit_derived_at_is_preserved(conn):
    """Backdated revisions (e.g. for replay scenarios) keep their
    ``derived_at`` instead of being clobbered by ``utc_now``."""
    from ovp_pipeline.truth_store_writers import (
        CHANGE_TYPE_EXTRACT,
        record_evergreen_revision,
    )

    record_evergreen_revision(
        conn, pack="p", object_id="alpha",
        content_md="x", change_type=CHANGE_TYPE_EXTRACT,
        derived_at="2026-04-28T12:14:03Z",
    )
    row = conn.execute(
        "SELECT derived_at FROM evergreen_revisions"
    ).fetchone()
    assert row == ("2026-04-28T12:14:03Z",)


def test_missing_table_returns_none_does_not_raise(tmp_path):
    """Best-effort contract — same as ``upsert_provenance``."""
    from ovp_pipeline.truth_store_writers import (
        CHANGE_TYPE_PROMOTE,
        record_evergreen_revision,
    )

    bare = sqlite3.connect(tmp_path / "empty.db")
    try:
        result = record_evergreen_revision(
            bare, pack="p", object_id="alpha",
            content_md="x", change_type=CHANGE_TYPE_PROMOTE,
        )
        assert result is None  # no row written, no exception
    finally:
        bare.close()


def test_invalid_inputs_short_circuit(conn):
    from ovp_pipeline.truth_store_writers import record_evergreen_revision

    # Empty pack / object_id / change_type all return None without
    # writing.  Caller invariant violation, not a DB error.
    assert record_evergreen_revision(
        conn, pack="", object_id="alpha", content_md="x", change_type="promote",
    ) is None
    assert record_evergreen_revision(
        conn, pack="p", object_id="", content_md="x", change_type="promote",
    ) is None
    assert record_evergreen_revision(
        conn, pack="p", object_id="alpha", content_md="x", change_type="",
    ) is None

    count = conn.execute(
        "SELECT COUNT(*) FROM evergreen_revisions"
    ).fetchone()[0]
    assert count == 0


# ---------------------------------------------------------------------------
# End-to-end: review_candidate_concept writes a promote revision
# ---------------------------------------------------------------------------


def _seed_v2_candidate(temp_vault):
    """Minimal candidate fixture matching ``test_truth_api`` patterns —
    enough for ``review_candidate_concept(action='promote')`` to land
    a row in ``objects`` with the canonical_path pointing at a real
    markdown file."""
    from ovp_pipeline.concept_registry import ConceptRegistry
    from ovp_pipeline.promote_candidates import write_candidate_file

    registry = ConceptRegistry(temp_vault)
    candidate = registry.upsert_candidate(
        slug="alpha-candidate",
        title="Alpha Candidate",
        definition="Candidate concept awaiting review.",
        area="testing",
        aliases=["alpha"],
    )
    registry.save()
    write_candidate_file(temp_vault, candidate, dry_run=False)
    return candidate


def test_promote_writes_evergreen_revision(temp_vault):
    """End-to-end: a successful promote produces both a
    ``stage='promote'`` provenance row (BL-056) AND a
    ``change_type='promote'`` revision row (BL-061), atomically."""
    from ovp_pipeline.runtime import VaultLayout
    from ovp_pipeline.truth_api import review_candidate_concept

    _seed_v2_candidate(temp_vault)
    payload = review_candidate_concept(
        temp_vault,
        slug="alpha-candidate",
        action="promote",
        note="Promote from test",
    )
    assert payload["mutation"]["action"] == "promote"
    assert payload["knowledge_index_rebuilt"] is True

    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT version, change_type, changed_by, change_note "
            "FROM evergreen_revisions "
            "WHERE object_id = 'alpha-candidate' "
            "ORDER BY version"
        ).fetchall()
    assert len(rows) == 1, f"expected exactly one promote revision, got {rows}"
    version, change_type, changed_by, change_note = rows[0]
    assert version == 1
    assert change_type == "promote"
    assert changed_by == "ui:review_candidate_concept"
    # Note carries lifecycle + reviewer note in the documented format.
    assert "lifecycle=promote" in change_note
    assert "note=Promote from test" in change_note
