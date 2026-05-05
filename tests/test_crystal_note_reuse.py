"""Tests for the BL-058 follow-up: crystal-note reuse-event emit.

The Reader's ``/note?path=40-Resources/Crystals/<id>.md`` route now
emits a ``reuse_events`` row keyed by ``object_kind=community_crystal``
or ``contradiction_crystal``, so ``crystal_scoring._reuse_recency_signal``
finally has a producer.  Pre-fix the only producer was test-fixture
seeds and the signal stayed cold-zero in production.

Two layers:

1. ``_crystal_kind_and_id_from_note_path`` — pure path parser.
2. ``_maybe_emit_crystal_note_reuse`` — best-effort emit; non-crystal
   paths are no-ops and emitter failures don't bubble up.
"""

from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# Path → (kind, id) parser
# ---------------------------------------------------------------------------


class TestCrystalKindAndIdFromNotePath:
    @pytest.mark.parametrize("path, expected", [
        # Community crystal lives at 40-Resources/Crystals/<safe-id>.md.
        (
            "40-Resources/Crystals/abc12345.md",
            ("community_crystal", "cluster::abc12345"),
        ),
        # Contradiction crystal has the ``contradiction-`` prefix on
        # the filename — round-trips back to the ``contradiction::``
        # crystal_id.
        (
            "40-Resources/Crystals/contradiction-xy789.md",
            ("contradiction_crystal", "contradiction::xy789"),
        ),
        # Leading ``./`` is tolerated.
        (
            "./40-Resources/Crystals/foo-bar.md",
            ("community_crystal", "cluster::foo-bar"),
        ),
    ])
    def test_recognises_crystal_paths(self, path, expected):
        from ovp_pipeline.commands.ui_server import (
            _crystal_kind_and_id_from_note_path,
        )
        assert _crystal_kind_and_id_from_note_path(path) == expected

    @pytest.mark.parametrize("path", [
        # Anywhere outside 40-Resources/Crystals/ → no emit.
        "10-Knowledge/Evergreen/some-note.md",
        "20-Areas/AI/note.md",
        "60-Logs/pipeline.jsonl",
        # The directory itself, no filename → no emit.
        "40-Resources/Crystals/",
        # Wrong extension.
        "40-Resources/Crystals/abc.txt",
        # Just the contradiction-prefix with empty stem.
        "40-Resources/Crystals/contradiction-.md",
        # Empty.
        "",
    ])
    def test_rejects_non_crystal_paths(self, path):
        from ovp_pipeline.commands.ui_server import (
            _crystal_kind_and_id_from_note_path,
        )
        assert _crystal_kind_and_id_from_note_path(path) is None


# ---------------------------------------------------------------------------
# Best-effort emit — non-crystal paths are no-ops, errors don't bubble
# ---------------------------------------------------------------------------


class TestMaybeEmitCrystalNoteReuse:
    def test_emits_reuse_event_for_crystal_path(self, temp_vault):
        from ovp_pipeline.commands.ui_server import (
            _maybe_emit_crystal_note_reuse,
        )

        _maybe_emit_crystal_note_reuse(
            temp_vault,
            "40-Resources/Crystals/abcdef.md",
            pack_name="research-tech",
        )
        # JSONL append went through.
        log = temp_vault / "60-Logs" / "reuse-events.jsonl"
        assert log.exists(), "reuse-events.jsonl should be written"
        events = [json.loads(line) for line in log.read_text("utf-8").splitlines()]
        assert len(events) == 1
        e = events[0]
        assert e["object_kind"] == "community_crystal"
        assert e["object_id"] == "cluster::abcdef"
        assert e["surface"] == "reader_note"
        assert e["consumer_ref"] == "40-Resources/Crystals/abcdef.md"

    def test_no_op_for_non_crystal_path(self, temp_vault):
        from ovp_pipeline.commands.ui_server import (
            _maybe_emit_crystal_note_reuse,
        )

        _maybe_emit_crystal_note_reuse(
            temp_vault,
            "10-Knowledge/Evergreen/regular.md",
            pack_name="research-tech",
        )
        log = temp_vault / "60-Logs" / "reuse-events.jsonl"
        # Either the file doesn't exist or it's empty — but no crystal
        # row was written.
        if log.exists():
            content = log.read_text("utf-8").strip()
            assert content == "" or "object_kind" not in content
