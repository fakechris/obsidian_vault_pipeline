"""T2 signal: GitHub stars + forks + recency.

For ``github.com/<owner>/<repo>`` URLs, fetch the repo metadata via
the unauthenticated GitHub REST API (60 req/h per IP, plenty for
ingest workloads) and convert to a 0-1 authority score.

Score formula
-------------

    base    = 0.40                                  # repo exists at all
    star    = 0.45 * tanh(log10(stars + 1) / 4)    # saturates ~10k stars
    recency = 0.15 * (1.0 if updated within 365d
                      else 0.5 if within 3 years
                      else 0.0)
    return min(1.0, base + star + recency)

This is similar to npm's ``quality`` metric.  Saturating at ~10k
stars avoids overweighting "trendy" repos at the expense of less-known
but still-canonical ones.

Cache
-----

Results cached to ``60-Logs/source_signals_cache.jsonl`` for 7 days.
The orchestrator handles cache lookup; this provider is responsible
only for the API call + score arithmetic.
"""

from __future__ import annotations

import json
import logging
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import Signal

logger = logging.getLogger(__name__)

_GITHUB_REPO_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:/.*)?(?:\.git)?$"
)
_API = "https://api.github.com/repos/{owner}/{repo}"
_TIMEOUT_S = 10.0


@dataclass(frozen=True, slots=True)
class GitHubSignalProvider:
    name: str = "github_stars"
    user_agent: str = "ovp-pipeline-source-authority/0.1"
    # When set, scoring first consults the ``entities`` table (populated
    # by ``ovp-backfill-github``).  An entity hit returns immediately —
    # no live API call.  An entity miss falls through to the live fetch
    # path below, preserving correctness for repos we haven't backfilled.
    # Default ``None`` keeps PR-D2 behavior unchanged.
    entity_store_path: Path | None = None

    def applies(self, source_url: str, frontmatter: dict[str, Any]) -> bool:
        return bool(source_url and _GITHUB_REPO_RE.match(source_url))

    def score(
        self, source_url: str, frontmatter: dict[str, Any],
    ) -> Signal | None:
        m = _GITHUB_REPO_RE.match(source_url)
        if not m:
            return None
        # ``rstrip(".git")`` was a character-set strip (would corrupt
        # repos like "widget" → "wid").  Use suffix removal instead.
        owner = m.group("owner")
        repo = m.group("repo")
        if repo.endswith(".git"):
            repo = repo[:-4]

        # Entity-table fast path — returns the github_backfill score
        # (more dimensions, calibrated 0-1) without an API call.
        entity_signal = self._entity_fast_path(owner, repo)
        if entity_signal is not None:
            return entity_signal

        url = _API.format(owner=owner, repo=repo)
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return Signal(
                    provider=self.name, value=0.10,
                    raw={"owner": owner, "repo": repo, "error": "404 — repo deleted/renamed"},
                )
            logger.warning("GitHub API HTTP %s for %s/%s", exc.code, owner, repo)
            return None
        except (urllib.error.URLError, TimeoutError) as exc:
            logger.warning("GitHub API error for %s/%s: %s", owner, repo, exc)
            return None
        except json.JSONDecodeError:
            return None

        stars = int(data.get("stargazers_count", 0) or 0)
        forks = int(data.get("forks_count", 0) or 0)
        archived = bool(data.get("archived"))

        star_component = 0.45 * math.tanh(math.log10(stars + 1) / 4.0)

        recency_component = 0.0
        pushed_at = data.get("pushed_at") or data.get("updated_at")
        if pushed_at:
            try:
                pushed_dt = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - pushed_dt).days
                if age_days <= 365:
                    recency_component = 0.15
                elif age_days <= 365 * 3:
                    recency_component = 0.075
            except ValueError:
                pass

        if archived:
            recency_component *= 0.5

        value = min(1.0, 0.40 + star_component + recency_component)

        return Signal(
            provider=self.name,
            value=round(value, 3),
            raw={
                "owner": owner,
                "repo": repo,
                "stars": stars,
                "forks": forks,
                "archived": archived,
                "pushed_at": pushed_at,
                "components": {
                    "base": 0.40,
                    "star": round(star_component, 3),
                    "recency": round(recency_component, 3),
                },
            },
        )

    def _entity_fast_path(self, owner: str, repo: str) -> Signal | None:
        """Return a Signal from the entity table or None.

        Hits ``entities/github_project`` first (most precise), then
        ``entities/github_user`` as an owner-fallback (capped at 0.55
        inside the resolver — see entities/resolver.py).  Either path
        yields the github_backfill formula score, which has more
        dimensions than the live ``score`` body below.

        Returns ``None`` (so the caller falls through to live fetch) if:
          * ``entity_store_path`` is not configured;
          * the store can't be opened;
          * neither the project nor the owner has been backfilled yet.
        """
        if self.entity_store_path is None:
            return None
        # Lazy import keeps source_signals/ free of an entities/ dep
        # when this fast path isn't wired.
        from ..entities.resolver import resolve_github_project_authority
        from ..entities.store import EntityStore

        try:
            store = EntityStore(db_path=self.entity_store_path)
        except Exception as exc:  # noqa: BLE001 - resilience over diagnostics
            logger.warning("entity store unavailable for %s/%s: %s",
                           owner, repo, exc)
            return None
        result = resolve_github_project_authority(store, owner, repo)
        if result.authority is None:
            return None
        return Signal(
            provider=self.name,
            value=round(result.authority, 4),
            raw={
                "owner": owner,
                "repo": repo,
                "matched_via": "entity_table",
                "entity_source": result.source,
                "entity_authority": result.authority,
            },
        )
