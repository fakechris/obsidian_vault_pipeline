"""Guard: ``note_type_normalize`` must not rewrite ``user-profile`` or
``live-concept`` frontmatter.

Regression caught on 2026-05-11: running ``ovp --incremental`` on the
live operator vault rewrote ``00-Polaris/USER.md`` from
``type: user-profile`` to ``type: article``, breaking the M20 / BL-075
contract (the loader keys off the literal type to know it's reading
a profile).  Both types now sit in ``CANONICAL_NOTE_TYPES`` and pass
through normalisation unchanged.
"""

from __future__ import annotations

from ovp_pipeline.note_type_normalize import (
    CANONICAL_NOTE_TYPES,
    load_mapping,
)


def test_user_profile_is_canonical():
    assert "user-profile" in CANONICAL_NOTE_TYPES


def test_live_concept_is_canonical():
    assert "live-concept" in CANONICAL_NOTE_TYPES


def test_normalize_user_profile_returns_self():
    mapping = load_mapping()
    assert mapping.normalize("user-profile") == "user-profile"


def test_normalize_live_concept_returns_self():
    mapping = load_mapping()
    assert mapping.normalize("live-concept") == "live-concept"
