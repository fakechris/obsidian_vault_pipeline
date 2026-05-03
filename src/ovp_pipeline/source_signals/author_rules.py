"""T1 signal: author whitelist / authority list.

Loads ``60-Logs/authors.jsonl`` (one JSON object per line) and matches
the source's author handle against it.  The list is hand-curated (the
trust input) — currently seeded with ~60 high-signal names.

Schema of one author entry::

    {
      "handle": "karpathy",          // primary identifier (no @)
      "aliases": ["andrej", "@karpathy", "karpathy.ai"],
      "authority": 0.95,             // 0-1
      "domain_hints": ["x.com", "karpathy.ai"],
      "source": "manual",            // or "imported_from_x", "expert_panel", ...
      "rationale": "OpenAI founding member, AI educator, deeply influential",
      "added_at": "2026-05-03"
    }

Handle resolution order (first match wins):
  1. ``frontmatter.author`` exact match against handle/aliases
  2. ``frontmatter.author`` substring match
  3. URL author handle (e.g. ``x.com/<handle>/status/...``)

If no author file exists yet, this provider returns ``None`` rather
than scoring zero — silence is more honest than guessing.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import Signal
from .overrides import AuthorOverrides
from .url_utils import extract_x_handle

logger = logging.getLogger(__name__)


@dataclass
class AuthorRulesProvider:
    """Match author identity against a curated authority list.

    Two load surfaces for the curated whitelist (merged at first lookup):
      * ``authors_path`` — JSONL, primary curation surface
      * ``overrides_path`` — YAML (``author_overrides.yaml``), useful
        for users who prefer grouped editing or exporting/importing
        the list to/from another tool.  YAML overrides win over JSONL
        on handle collision (most-recently-added wins).

    Plus an optional entity-table fallback:
      * ``entity_store_path`` — knowledge.db (PR-E1/E2 backfills).
        When the curated whitelist misses a handle, look it up in the
        entity table.  Result is multiplied by ``entity_score_multiplier``
        (default 0.85) so curated entries strictly outrank derived ones
        at the same raw score.

    When all three are None (the original PR-D1 default), behavior is
    "whitelist or nothing".
    """

    authors_path: Path
    overrides_path: Path | None = None
    name: str = "author_rules"
    entity_store_path: Path | None = None
    # Multiplier applied to entity-derived authority to keep curated
    # whitelist entries strictly above derived ones at the same raw
    # score.  Tunable via the dataclass field if a future calibration
    # round wants to lift entity signals.
    entity_score_multiplier: float = 0.85
    _index: dict[str, dict[str, Any]] | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._index is not None:
            return self._index
        index: dict[str, dict[str, Any]] = {}
        if self.authors_path.exists():
            with self.authors_path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as exc:
                        logger.warning("authors.jsonl parse error on %r: %s", line[:80], exc)
                        continue
                    handle = (record.get("handle") or "").lower().lstrip("@")
                    if handle:
                        index[handle] = record
                    for alias in record.get("aliases", []):
                        norm = (alias or "").lower().lstrip("@").strip()
                        if norm:
                            index[norm] = record
        # YAML overrides — applied after JSONL so they win on collision
        if self.overrides_path is not None:
            yaml_data = AuthorOverrides.load(self.overrides_path)
            for record in yaml_data.authors:
                handle = record["handle"]
                index[handle] = record
                for alias in record["aliases"]:
                    if alias:
                        index[alias] = record
        self._index = index
        return index

    def _extract_handle_from_url(self, source_url: str) -> str | None:
        # Delegated to the shared utility so url_utils is the single
        # source of truth for X/Twitter handle parsing.
        return extract_x_handle(source_url)

    def applies(self, source_url: str, frontmatter: dict[str, Any]) -> bool:
        if frontmatter.get("author") or frontmatter.get("authors"):
            return True
        if self._extract_handle_from_url(source_url):
            return True
        return False

    def score(
        self, source_url: str, frontmatter: dict[str, Any],
    ) -> Signal | None:
        index = self._load()

        candidates: list[str] = []
        author = frontmatter.get("author") or ""
        if isinstance(author, str) and author:
            candidates.append(author.lower().lstrip("@").strip())
        elif isinstance(author, list):
            for a in author:
                if isinstance(a, str):
                    candidates.append(a.lower().lstrip("@").strip())
        for a in (frontmatter.get("authors") or []):
            if isinstance(a, str):
                candidates.append(a.lower().lstrip("@").strip())
        url_handle = self._extract_handle_from_url(source_url)
        if url_handle:
            candidates.append(url_handle)

        # Exact match first
        for cand in candidates:
            if cand in index:
                rec = index[cand]
                return Signal(
                    provider=self.name,
                    value=float(rec.get("authority", 0.5)),
                    raw={
                        "matched": cand,
                        "handle": rec.get("handle"),
                        "rationale": rec.get("rationale", ""),
                    },
                )

        # Substring match (looser, e.g. "Andrej Karpathy" → "karpathy")
        for cand in candidates:
            for key, rec in index.items():
                if key in cand and len(key) >= 4:  # avoid silly 2-char matches
                    return Signal(
                        provider=self.name,
                        value=float(rec.get("authority", 0.5)) * 0.9,  # softer
                        raw={
                            "matched": cand,
                            "matched_via": "substring",
                            "handle": rec.get("handle"),
                        },
                    )

        # PR-E3 fallback: not in whitelist → consult the entity table
        # (twitter_author + person merges).  This is where the 521
        # twitterapi.io-backfilled entities start affecting scoring.
        entity_signal = self._entity_fallback(candidates)
        if entity_signal is not None:
            return entity_signal

        return None

    def _entity_fallback(self, candidates: list[str]) -> Signal | None:
        """Resolve a candidate handle through the entity table.

        Returns ``None`` if no ``entity_store_path`` is configured,
        if the store is empty, or if no candidate maps to an entity
        with a derived_authority.  Multiplied by
        ``entity_score_multiplier`` so curated whitelist entries
        strictly outrank entity-derived ones at the same raw score.
        """
        if self.entity_store_path is None:
            return None
        # Lazy import keeps source_signals/ import-time graph clean
        # (no entities/ pulled in unless this fallback fires).
        from ..entities.store import EntityStore
        from ..entities.resolver import resolve_twitter_authority

        try:
            store = EntityStore(db_path=self.entity_store_path)
        except Exception as exc:  # noqa: BLE001 - resilience over diagnostics
            logger.warning("entity store unavailable: %s", exc)
            return None

        for cand in candidates:
            result = resolve_twitter_authority(store, cand)
            if result.authority is None:
                continue
            return Signal(
                provider=self.name,
                value=round(result.authority * self.entity_score_multiplier, 4),
                raw={
                    "matched": cand,
                    "matched_via": "entity_table",
                    "entity_source": result.source,
                    "raw_authority": result.authority,
                    "multiplier": self.entity_score_multiplier,
                },
            )
        return None
