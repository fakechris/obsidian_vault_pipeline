"""Guard: ``note_type_normalize`` must not rewrite the M19/M20
note-type signals (``user-profile`` / ``live-concept`` / ``digest``)
to ``article``.

Regression caught on 2026-05-11: running ``ovp --incremental`` on
the live operator vault rewrote ``00-Polaris/USER.md`` from
``type: user-profile`` to ``type: article``, breaking the BL-075
contract (the loader keys off the literal type to know it's reading
a profile).  All three types now sit in ``CANONICAL_NOTE_TYPES``
and pass through normalisation unchanged.

Tests are parametrised over the three M19/M20 types so adding a
fourth in the future is a one-line append (rev-bot 207.2).
"""

from __future__ import annotations

import pytest

from ovp_pipeline.note_type_normalize import (
    CANONICAL_NOTE_TYPES,
    load_mapping,
)

_M19_M20_TYPES = ("user-profile", "live-concept", "digest")


@pytest.mark.parametrize("type_name", _M19_M20_TYPES)
def test_type_is_canonical(type_name: str):
    assert type_name in CANONICAL_NOTE_TYPES


@pytest.mark.parametrize("type_name", _M19_M20_TYPES)
def test_normalize_returns_self(type_name: str):
    mapping = load_mapping()
    assert mapping.normalize(type_name) == type_name
