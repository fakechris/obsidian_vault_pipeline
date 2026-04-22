"""research-tech reuse signals (Phase 34).

Tunes Phase 32's ``trusted_reuse_event`` derivation. Default lookback for
provenance-clean is 30 days; research-tech sticks with the default.
"""

from __future__ import annotations

from ..base import ReuseSignalsSpec


RESEARCH_TECH_REUSE_SIGNALS = ReuseSignalsSpec(
    extra_surfaces=(),
    provenance_clean_lookback_days=30,
)
