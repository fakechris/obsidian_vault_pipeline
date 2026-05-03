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
from typing import Any

from .base import Signal, SignalProvider

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

    def applies(self, source_url: str, frontmatter: dict[str, Any]) -> bool:
        return bool(source_url and _GITHUB_REPO_RE.match(source_url))

    def score(
        self, source_url: str, frontmatter: dict[str, Any],
    ) -> Signal | None:
        m = _GITHUB_REPO_RE.match(source_url)
        if not m:
            return None
        owner, repo = m.group("owner"), m.group("repo").rstrip(".git")
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
