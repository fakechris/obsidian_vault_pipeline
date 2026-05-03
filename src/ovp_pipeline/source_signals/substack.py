"""T3 signal: Substack publication subscriber count + author authority — STUB.

Backends investigated (May 2026)
--------------------------------

================  =========  ==========  =================================
Backend           cost       reliability notes
================  =========  ==========  =================================
public_api        free       high        ``substack.com/api/v1/feed/profile/
                                         {handle}`` returns recent posts +
                                         basic profile.  Unauthenticated.
                                         No subscriber count directly, but
                                         can derive heuristic from
                                         ``post_count`` and ``last_post_at``.
post_html_scrape  free       medium      Subscriber count is rendered in
                                         post HTML as ``<X> subscribers``
                                         (not in API).  Fragile to template
                                         changes; cache aggressively.
rss_feed          free       low         Public RSS gives content + author
                                         but no engagement signal at all.
================  =========  ==========  =================================

Recommended starter:
  * ``public_api`` for "is this a real publication?" check (cheap, reliable)
  * ``post_html_scrape`` if subscriber count actually drives decisions
    (i.e. discriminating between 100-sub niche newsletters and 100k-sub
    flagship ones)

Score components when fully wired
---------------------------------

::

    base                = 0.55
    age_component       = 0.10 if pub_age > 1y else 0.0
    cadence_component   = 0.10 if posts_last_30d >= 4 else 0.05 if >=1 else 0.0
    subscribers         = (parsed from HTML, see post_html_scrape backend)
    subscriber_component = 0.20 * tanh(log10(subscribers + 1) / 5)
    author_authority    = author_rules.score(handle) * 0.20
    return clip(sum(components), 0, 1)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from .base import Signal, SignalProvider

_SUBSTACK_RE = re.compile(
    r"^https?://(?P<handle>[\w-]+)\.substack\.com/(?:p/[\w-]+/?)?$"
    r"|^https?://substack\.com/p/(?P<post_slug>[\w-]+)/?$"
)


@dataclass(frozen=True, slots=True)
class SubstackSignalProvider:
    """Stubbed provider — wires interface, defers implementation."""

    name: str = "substack_authority"

    def applies(self, source_url: str, frontmatter: dict[str, Any]) -> bool:
        if not source_url:
            return False
        return "substack.com" in source_url

    def score(
        self, source_url: str, frontmatter: dict[str, Any],
    ) -> Signal | None:
        backend = os.environ.get("OVP_SUBSTACK_BACKEND", "").strip().lower()
        if not backend:
            return None  # opt-in only

        if backend == "public_api":
            return _fetch_public_api(self.name, source_url)
        if backend == "post_html_scrape":
            return _fetch_post_html_scrape(self.name, source_url)
        if backend == "rss_feed":
            return _fetch_rss_feed(self.name, source_url)
        return None


def _fetch_public_api(name: str, url: str) -> Signal | None:
    """Substack public profile API — ``/api/v1/feed/profile/{handle}``.

    Returns recent posts + display name + bio.  No subscriber count.
    Use to verify a publication is alive + active; combine with
    ``DomainRulesProvider`` baseline for full score.
    """
    raise NotImplementedError("OVP_SUBSTACK_BACKEND=public_api not implemented yet")


def _fetch_post_html_scrape(name: str, url: str) -> Signal | None:
    """Scrape ``<X> subscribers`` from post HTML (the only public source).

    Use a polite User-Agent + ETag/Last-Modified headers to avoid hammering.
    Cache results aggressively (TTL ≥ 7 days) since subscribers don't
    change rapidly enough to justify daily re-checks.
    """
    raise NotImplementedError("OVP_SUBSTACK_BACKEND=post_html_scrape not implemented yet")


def _fetch_rss_feed(name: str, url: str) -> Signal | None:
    """Pure RSS — gives post cadence + author identity.  Lowest signal
    quality but cleanest legally.
    """
    raise NotImplementedError("OVP_SUBSTACK_BACKEND=rss_feed not implemented yet")
