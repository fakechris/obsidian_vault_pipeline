"""Source authority orchestrator.

Combines signals from registered ``SignalProvider``s into a single
``AuthorityScore`` per source.  Persists to ``knowledge.db``
(``source_authority`` table) and an append-only audit log
(``60-Logs/source_authority.jsonl``).

Soft-signal semantics
---------------------

The score is **never** used to gate extraction.  Downstream consumers
(``ovp-query``, UI badges, conflict detection) read it to filter or
rank, but every ingested source gets its evergreens / entities
extracted regardless of authority.  This is the explicit choice from
the May 2026 design discussion: "soft, not hard".

Combination rule
----------------

Weighted arithmetic mean of provider signals, with one twist: the
``domain_rules`` signal acts as the floor — a provider that contradicts
the domain (e.g. arXiv paper on a low-quality preprint farm) drops the
score, but the domain alone never raises it above what the path-based
signal said.

::

    primary = sum(s.value * s.weight for s in signals) / sum(s.weight)
    floor   = (domain_rules.value * 0.7) if domain_rules in signals else 0.0
    final   = max(floor, primary)

When no provider produced a signal at all, the orchestrator returns
the neutral default 0.45 (matching unknown-domain default).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict
from math import isfinite
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .source_signals import (
    ArxivSignalProvider,
    AuthorRulesProvider,
    AuthorityScore,
    DomainRulesProvider,
    GitHubSignalProvider,
    Signal,
    SignalProvider,
    SubstackSignalProvider,
    TwitterSignalProvider,
)

logger = logging.getLogger(__name__)

_SCORER_VERSION = "v1"
_DEFAULT_TIMEOUT_S = 12.0


def default_providers(vault_dir: Path) -> list[SignalProvider]:
    """Construct the default provider stack.

    Order matters only for ``AuthorityScore.signals`` audit clarity;
    the combination rule treats them as a multiset.

    Three input surfaces wired in here:
      * ``60-Logs/authors.jsonl`` + ``60-Logs/author_overrides.yaml``
        — curated whitelist (PR-D1 / PR-D3)
      * ``60-Logs/domain_overrides.yaml``
        — extended/overridden domain table (PR-D3)
      * ``60-Logs/knowledge.db``
        — entity table fast path (PR-E1 / E2 / E3 / E4) for handles +
        repos that aren't curated yet.  Providers no-op gracefully
        when the file or its tables don't exist, so a fresh vault
        still works.
    """
    authors_path = vault_dir / "60-Logs" / "authors.jsonl"
    domain_overrides_path = vault_dir / "60-Logs" / "domain_overrides.yaml"
    author_overrides_path = vault_dir / "60-Logs" / "author_overrides.yaml"
    entity_store_path = vault_dir / "60-Logs" / "knowledge.db"
    return [
        DomainRulesProvider(overrides_path=domain_overrides_path),
        AuthorRulesProvider(
            authors_path=authors_path,
            overrides_path=author_overrides_path,
            entity_store_path=entity_store_path,
        ),
        GitHubSignalProvider(entity_store_path=entity_store_path),
        ArxivSignalProvider(),
        TwitterSignalProvider(),    # stub; returns None unless OVP_TWITTER_BACKEND set
        SubstackSignalProvider(),   # stub; returns None unless OVP_SUBSTACK_BACKEND set
    ]


def score_source(
    source_url: str,
    frontmatter: dict[str, Any],
    *,
    providers: list[SignalProvider],
) -> AuthorityScore:
    """Run every applicable provider on a single source; combine."""
    signals: list[Signal] = []
    for p in providers:
        try:
            if not p.applies(source_url, frontmatter):
                continue
        except Exception as exc:
            logger.warning("provider %s.applies error: %s", p.name, exc)
            continue
        try:
            sig = p.score(source_url, frontmatter)
        except NotImplementedError:
            continue  # stubbed backends — silent
        except Exception as exc:
            logger.warning("provider %s.score error: %s", p.name, exc)
            continue
        if sig is not None:
            # Defensive normalization — a buggy provider returning
            # NaN / inf / non-numeric value or weight should degrade
            # gracefully (skip the signal + warn), never abort the run.
            try:
                value = float(sig.value)
                weight = float(sig.weight)
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "provider %s returned non-numeric signal: %s",
                    sig.provider, exc,
                )
                continue
            if not isfinite(value) or not isfinite(weight) or weight <= 0:
                logger.warning(
                    "provider %s returned non-finite or non-positive "
                    "value/weight (%s, %s); skipping",
                    sig.provider, value, weight,
                )
                continue
            clipped = Signal(
                provider=sig.provider,
                value=max(0.0, min(1.0, value)),
                weight=weight,
                raw=sig.raw,
            )
            signals.append(clipped)

    authority = _combine(signals)
    return AuthorityScore(
        source_id=_canonical_source_id(source_url, frontmatter),
        authority=authority,
        signals=tuple(signals),
        scored_at=datetime.now(timezone.utc).isoformat(),
        scorer_version=_SCORER_VERSION,
    )


def _canonical_source_id(source_url: str, frontmatter: dict[str, Any]) -> str:
    """Choose a stable identifier per source.

    Preference order: source URL → frontmatter ``source`` → vault path.
    Empty string is allowed and preserved (some sources are local
    clippings without a URL); the audit log handles dedup by hash.
    """
    if source_url:
        return source_url
    fm_source = frontmatter.get("source") or frontmatter.get("source_url")
    if isinstance(fm_source, str) and fm_source:
        return fm_source
    return ""


def _combine(signals: tuple[Signal, ...] | list[Signal]) -> float:
    """Combine provider signals into a single 0-1 score.

    Weighted average, with the ``domain_rules`` signal acting as a
    soft floor (lets the network-fetched signals lift it up but not
    drop it through the floor by more than 30%).
    """
    if not signals:
        return 0.45  # default neutral when nothing applies

    total_weight = sum(s.weight for s in signals)
    if total_weight <= 0:
        return 0.45
    weighted_sum = sum(s.value * s.weight for s in signals)
    primary = weighted_sum / total_weight

    domain_signal = next(
        (s for s in signals if s.provider == "domain_rules"), None,
    )
    floor = domain_signal.value * 0.7 if domain_signal else 0.0

    return round(max(floor, primary), 3)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS source_authority (
    source_id TEXT PRIMARY KEY,
    authority REAL NOT NULL,
    signals_json TEXT NOT NULL,
    scored_at TEXT NOT NULL,
    scorer_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_authority_value ON source_authority(authority);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent: safe to call on every ``rebuild_knowledge_index`` run."""
    conn.executescript(_SCHEMA_SQL)


def replay_authority_log(
    conn: sqlite3.Connection, jsonl_path: Path,
) -> int:
    """Repopulate the ``source_authority`` table from the append-only
    audit log.  BL-054: the table is wiped on every
    ``rebuild_knowledge_index``; the JSONL is the durable source of
    truth, so we replay it on every rebuild instead of relying on
    ``preserve_existing_truth_rows``.  Returns the number of rows
    written.

    Each JSONL line is one ``AuthorityScore``-shaped record:

    .. code-block:: json

        {
          "source_id": "https://example.com/article",
          "authority": 0.55,
          "signals": [...],
          "scored_at": "2026-04-29T...",
          "scorer_version": "v1"
        }

    The latest record per ``source_id`` wins on conflict (the audit log
    is append-only, so iterating top-down and using ``ON CONFLICT DO
    UPDATE`` collapses to the most recent score).
    """
    if not jsonl_path.exists():
        return 0
    written = 0
    try:
        with jsonl_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "skipping malformed source_authority audit line",
                    )
                    continue
                source_id = record.get("source_id")
                if not source_id:
                    continue
                authority = record.get("authority")
                if authority is None:
                    continue
                conn.execute(
                    "INSERT INTO source_authority(source_id, authority, "
                    "signals_json, scored_at, scorer_version) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(source_id) DO UPDATE SET "
                    "authority=excluded.authority, "
                    "signals_json=excluded.signals_json, "
                    "scored_at=excluded.scored_at, "
                    "scorer_version=excluded.scorer_version",
                    (
                        str(source_id),
                        float(authority),
                        json.dumps(
                            record.get("signals") or [],
                            ensure_ascii=False,
                        ),
                        str(record.get("scored_at") or ""),
                        str(record.get("scorer_version") or ""),
                    ),
                )
                written += 1
    except OSError as exc:
        logger.warning(
            "could not read source_authority log %s: %s",
            jsonl_path, exc,
        )
        return 0
    return written


def upsert_score(
    conn: sqlite3.Connection,
    score: AuthorityScore,
) -> None:
    """Write the score; previous values for the same source_id are overwritten."""
    conn.execute(
        "INSERT INTO source_authority(source_id, authority, signals_json, "
        "scored_at, scorer_version) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(source_id) DO UPDATE SET "
        "authority=excluded.authority, signals_json=excluded.signals_json, "
        "scored_at=excluded.scored_at, scorer_version=excluded.scorer_version",
        (
            score.source_id,
            score.authority,
            json.dumps([asdict(s) for s in score.signals], ensure_ascii=False),
            score.scored_at,
            score.scorer_version,
        ),
    )


def serialize_audit_record(score: AuthorityScore) -> str:
    """Render one ``AuthorityScore`` as a JSONL line (with trailing newline).

    The batch CLI (``ovp-score-sources``) holds the audit log file
    handle open for the whole run and writes via this helper, avoiding
    the open-then-close-per-record overhead of the previous
    ``append_audit`` helper that opened the file for each score.
    """
    record = {
        "source_id": score.source_id,
        "authority": score.authority,
        "signals": [asdict(s) for s in score.signals],
        "scored_at": score.scored_at,
        "scorer_version": score.scorer_version,
    }
    return json.dumps(record, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# Batch scoring helper
# ---------------------------------------------------------------------------


def score_sources(
    sources: Iterable[tuple[str, dict[str, Any]]],
    *,
    providers: list[SignalProvider],
) -> list[AuthorityScore]:
    """Score many sources, accumulating results without persistence.

    The orchestrator ``ovp-score-sources`` CLI handles persistence.
    Returns scores in input order.
    """
    return [
        score_source(url, fm, providers=providers)
        for url, fm in sources
    ]
