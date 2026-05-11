"""REVS: tests for ``revisions_view`` + ``ovp-rollback-evergreen``."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

# Test fixtures land in the truth-pack-resolved-from-temp_vault
# pack — typically "research-tech" because that pack defines
# a fixtures area; resolve dynamically rather than hard-coding.
from ovp_pipeline.knowledge_index import _truth_pack_name
_DEFAULT_PACK = _truth_pack_name(None)


def _seed_evergreen(temp_vault: Path, *, slug: str, body: str = "v1 body") -> Path:
    """Write a minimal evergreen file so rebuild produces an objects row."""
    eg = temp_vault / "10-Knowledge" / "Evergreen"
    eg.mkdir(parents=True, exist_ok=True)
    path = eg / f"{slug.replace('-', ' ').title().replace(' ', '-')}.md"
    path.write_text(
        f"---\nnote_id: {slug}\ntitle: {slug}\ntype: evergreen\n"
        f"date: 2026-04-13\n---\n\n# {slug}\n\n{body}\n",
        encoding="utf-8",
    )
    from ovp_pipeline.knowledge_index import rebuild_knowledge_index
    rebuild_knowledge_index(temp_vault)
    return path


def _seed_revision(
    temp_vault: Path,
    *,
    pack: str,
    object_id: str,
    content_md: str,
    change_type: str,
    changed_by: str = "test",
    derived_at: str | None = None,
) -> int:
    """Insert one row via the BL-061 helper (single-writer)."""
    from ovp_pipeline.runtime import VaultLayout
    from ovp_pipeline.truth_store_writers import record_evergreen_revision

    db = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db) as conn:
        version = record_evergreen_revision(
            conn,
            pack=pack,
            object_id=object_id,
            content_md=content_md,
            change_type=change_type,
            changed_by=changed_by,
            derived_at=derived_at,
        )
        conn.commit()
    assert version is not None
    return version


# ---------------------------------------------------------------------------
# list_evergreen_revisions
# ---------------------------------------------------------------------------


def test_list_returns_revisions_newest_first(temp_vault):
    from ovp_pipeline.revisions_view import list_evergreen_revisions

    _seed_evergreen(temp_vault, slug="alpha")
    _seed_revision(
        temp_vault, pack=_DEFAULT_PACK, object_id="alpha",
        content_md="v1 content", change_type="promote",
        derived_at="2026-04-13T10:00:00Z",
    )
    _seed_revision(
        temp_vault, pack=_DEFAULT_PACK, object_id="alpha",
        content_md="v2 content", change_type="llm_rewrite",
        derived_at="2026-04-14T10:00:00Z",
    )
    revisions = list_evergreen_revisions(
        temp_vault, pack=_DEFAULT_PACK, object_id="alpha",
    )
    assert [r.version for r in revisions] == [2, 1]
    assert revisions[0].change_type == "llm_rewrite"
    assert revisions[1].content_md == "v1 content"


def test_list_returns_empty_when_table_missing(tmp_path):
    """Pre-BL-061 vault — schema doesn't exist.  No exception."""
    from ovp_pipeline.revisions_view import list_evergreen_revisions

    assert list_evergreen_revisions(
        tmp_path, pack=_DEFAULT_PACK, object_id="alpha",
    ) == []


def test_list_respects_limit(temp_vault):
    from ovp_pipeline.revisions_view import list_evergreen_revisions

    _seed_evergreen(temp_vault, slug="alpha")
    for i in range(5):
        _seed_revision(
            temp_vault, pack=_DEFAULT_PACK, object_id="alpha",
            content_md=f"v{i+1}", change_type="promote",
            derived_at=f"2026-04-{13+i:02d}T10:00:00Z",
        )
    revisions = list_evergreen_revisions(
        temp_vault, pack=_DEFAULT_PACK, object_id="alpha", limit=2,
    )
    assert [r.version for r in revisions] == [5, 4]


# ---------------------------------------------------------------------------
# get_evergreen_revision
# ---------------------------------------------------------------------------


def test_get_specific_version_returns_content_verbatim(temp_vault):
    from ovp_pipeline.revisions_view import get_evergreen_revision

    _seed_evergreen(temp_vault, slug="alpha")
    _seed_revision(
        temp_vault, pack=_DEFAULT_PACK, object_id="alpha",
        content_md="exactly this content",
        change_type="promote",
    )
    target = get_evergreen_revision(
        temp_vault, pack=_DEFAULT_PACK, object_id="alpha", version=1,
    )
    assert target is not None
    assert target.content_md == "exactly this content"


def test_get_missing_version_returns_none(temp_vault):
    from ovp_pipeline.revisions_view import get_evergreen_revision

    _seed_evergreen(temp_vault, slug="alpha")
    assert get_evergreen_revision(
        temp_vault, pack=_DEFAULT_PACK, object_id="alpha", version=99,
    ) is None


# ---------------------------------------------------------------------------
# rollback_evergreen
# ---------------------------------------------------------------------------


def test_rollback_restores_content_and_writes_new_revision(temp_vault):
    """Happy path: rollback v1 → restores content_md to file, appends
    a ``change_type='rollback'`` row that references the source
    version.  History is preserved (no rows deleted)."""
    from ovp_pipeline.revisions_view import rollback_evergreen
    from ovp_pipeline.runtime import VaultLayout

    path = _seed_evergreen(temp_vault, slug="alpha", body="current body")
    _seed_revision(
        temp_vault, pack=_DEFAULT_PACK, object_id="alpha",
        content_md="# Original\n\nOriginal v1 body.\n",
        change_type="promote",
        derived_at="2026-04-13T10:00:00Z",
    )
    # Operator hand-edits the file to something we want to undo.
    path.write_text("# Edited\n\nBad rewrite.\n", encoding="utf-8")

    result = rollback_evergreen(
        temp_vault,
        pack=_DEFAULT_PACK,
        object_id="alpha",
        target_version=1,
    )
    assert result["status"] == "rolled_back"
    assert result["source_version"] == 1
    assert result["new_version"] == 2

    # File on disk now matches v1 content.
    assert path.read_text(encoding="utf-8") == "# Original\n\nOriginal v1 body.\n"

    # Revisions table has 2 rows now: original promote + new rollback.
    db = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT version, change_type, changed_by, change_note "
            "FROM evergreen_revisions WHERE object_id='alpha' ORDER BY version"
        ).fetchall()
    assert [r[0] for r in rows] == [1, 2]
    assert rows[1][1] == "rollback"
    assert rows[1][2] == "cli:rollback"
    assert "rolled_back_to_version=1" in rows[1][3]


def test_rollback_raises_for_missing_version(temp_vault):
    from ovp_pipeline.revisions_view import rollback_evergreen

    _seed_evergreen(temp_vault, slug="alpha")
    with pytest.raises(ValueError, match=r"No revision found"):
        rollback_evergreen(
            temp_vault,
            pack=_DEFAULT_PACK,
            object_id="alpha",
            target_version=99,
        )


def test_rollback_explicit_canonical_path_overrides_lookup(temp_vault):
    """Operator passes ``--canonical-path`` explicitly when the
    objects table is stale or missing.  Test seeds a revision, then
    calls rollback with an explicit path that doesn't match the
    objects row — write must go to the explicit path."""
    from ovp_pipeline.revisions_view import rollback_evergreen

    _seed_evergreen(temp_vault, slug="alpha")
    _seed_revision(
        temp_vault, pack=_DEFAULT_PACK, object_id="alpha",
        content_md="snapshot content\n",
        change_type="promote",
    )
    explicit = temp_vault / "10-Knowledge" / "Evergreen" / "Custom-Name.md"
    result = rollback_evergreen(
        temp_vault,
        pack=_DEFAULT_PACK,
        object_id="alpha",
        target_version=1,
        canonical_path=str(explicit),
    )
    assert Path(result["canonical_path"]) == explicit
    assert explicit.read_text(encoding="utf-8") == "snapshot content\n"


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_list_mode_emits_json(temp_vault, capsys):
    from ovp_pipeline.commands.rollback_evergreen import main

    _seed_evergreen(temp_vault, slug="alpha")
    _seed_revision(
        temp_vault, pack=_DEFAULT_PACK, object_id="alpha",
        content_md="v1", change_type="promote",
    )
    rc = main(["--vault-dir", str(temp_vault), "alpha", "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc == 0
    assert payload["mode"] == "list"
    assert payload["slug"] == "alpha"
    assert payload["revisions"][0]["version"] == 1


def test_cli_dry_run_does_not_mutate_file(temp_vault, capsys):
    from ovp_pipeline.commands.rollback_evergreen import main

    path = _seed_evergreen(temp_vault, slug="alpha", body="current body")
    _seed_revision(
        temp_vault, pack=_DEFAULT_PACK, object_id="alpha",
        content_md="# Old\n", change_type="promote",
    )
    before = path.read_text(encoding="utf-8")
    rc = main([
        "--vault-dir", str(temp_vault), "alpha", "1", "--dry-run", "--json",
    ])
    after = path.read_text(encoding="utf-8")
    assert rc == 0
    assert before == after  # not mutated
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "would_rollback"
