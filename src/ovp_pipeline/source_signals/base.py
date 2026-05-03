"""Core types for the source-authority subsystem.

``SignalProvider`` is the contract every signal source implements.
``Signal`` and ``AuthorityScore`` are the immutable record types passed
between provider, orchestrator, and storage (knowledge.db /
``60-Logs/source_authority.jsonl``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Protocol


def _empty_mapping() -> Mapping[str, Any]:
    """Return a read-only empty mapping for default ``Signal.raw``."""
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Signal:
    """One provider's verdict on one source.

    Attributes
    ----------
    provider : str
        Short identifier ("github_stars", "domain_rules", ...).
    value : float
        0-1 authority contribution.  ``0`` means "low confidence",
        ``1`` means "maximally authoritative on the dimension this
        provider cares about".  Providers should never return values
        outside [0, 1] — the orchestrator clips defensively.
    weight : float
        Relative weight when combined with other providers' signals.
        Defaults are set per-provider; the user can override via
        ``60-Logs/source_authority.config.json``.
    raw : Mapping
        Structured backing data for audit (e.g. ``{"stars": 1500,
        "forks": 47, "last_commit_iso": "2026-04-12"}``).  Typed as
        ``Mapping`` (read-only protocol) rather than ``dict`` to
        signal that the orchestrator + storage code MUST treat it as
        immutable.  Providers may pass an ordinary dict; downstream
        code never mutates.  Goes into ``signals_json`` in the DB;
        never participates in scoring math.
    """

    provider: str
    value: float
    weight: float = 1.0
    raw: Mapping[str, Any] = field(default_factory=_empty_mapping)


@dataclass(frozen=True, slots=True)
class AuthorityScore:
    """Combined verdict across all providers for one source.

    ``signals`` is typed ``tuple`` (immutable) rather than ``list`` so
    audit/provenance state can't drift after the score is computed.
    Construct with ``signals=tuple(your_list)``.
    """

    source_id: str             # canonical identifier (URL or vault-relative path)
    authority: float           # weighted combination, 0-1
    signals: tuple[Signal, ...]  # provenance — every contributing signal
    scored_at: str             # ISO 8601 UTC
    scorer_version: str = "v1"  # bumps when combination logic changes


class SignalProvider(Protocol):
    """A pluggable source-of-evidence.  Implement two methods.

    Providers are registered with the orchestrator at construction
    time; the orchestrator decides which providers apply per source by
    calling ``applies()``, then collects ``Signal`` from every one
    that does.
    """

    name: str

    def applies(self, source_url: str, frontmatter: dict[str, Any]) -> bool:
        """Return ``True`` if this provider has evidence on this source.

        Cheap; no network calls.  Typical implementation: regex match
        on the URL (``github.com``, ``arxiv.org``, ``substack.com``) or
        a frontmatter field (``author``, ``original_handle``).
        """
        ...

    def score(
        self, source_url: str, frontmatter: dict[str, Any],
    ) -> Signal | None:
        """Return a ``Signal`` for this source, or ``None`` if the
        provider couldn't reach a verdict (e.g. API timeout).

        May be slow / make network calls.  Providers that hit the
        network are responsible for their own ``socket`` / ``urlopen``
        timeouts (10s is the convention used by ``github.py`` and
        ``arxiv.py``).  The orchestrator does not currently cache — a
        7-day SQLite-backed cache is queued as a separate PR; for now,
        ``ovp-score-sources --since YYYY-MM-DD`` provides incremental
        scoring as a workaround.
        """
        ...
