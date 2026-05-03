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
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .base import Signal, SignalProvider

logger = logging.getLogger(__name__)

_HANDLE_FROM_X_URL = re.compile(
    r"^/?(?:@)?(?P<handle>[A-Za-z0-9_]+)(?:/.*)?$"
)


@dataclass
class AuthorRulesProvider:
    """Match author identity against a curated authority list."""

    authors_path: Path
    name: str = "author_rules"
    _index: dict[str, dict[str, Any]] | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._index is not None:
            return self._index
        index: dict[str, dict[str, Any]] = {}
        if not self.authors_path.exists():
            self._index = index
            return index
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
        self._index = index
        return index

    def _extract_handle_from_url(self, source_url: str) -> str | None:
        try:
            parsed = urlparse(source_url)
        except ValueError:
            return None
        host = (parsed.netloc or "").lower().lstrip("www.")
        if host not in {"x.com", "twitter.com"}:
            return None
        m = _HANDLE_FROM_X_URL.match(parsed.path)
        return m.group("handle").lower() if m else None

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
        if not index:
            return None

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

        return None
