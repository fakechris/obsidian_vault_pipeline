"""T3 signal: Twitter/X engagement + author authority — STUB.

The interface is finalized so concrete fetchers can plug in without
touching the orchestrator.  Production fetchers are NOT shipped in this
PR; you must opt in by setting ``OVP_TWITTER_BACKEND`` to one of the
documented values below.

Backends investigated (May 2026)
--------------------------------

================  =========  ==========  ========  ===================
Backend           cost / mo  reliability legal     notes
================  =========  ==========  ========  ===================
api_v2_basic      $200       high        clean     10K reads/mo cap; OK
                                                   for ~1k articles/yr
apify_scraper     $0.30/1K   high        gray      pay-per-use; pragmatic
twscrape          free       medium      gray      uses session cookies;
                                                   fragile to UI changes
og_meta_only      free       low         clean     scrapes <meta og:>
                                                   only; gives like count,
                                                   no RT count, no follower
nitter_mirrors    free       very low    gray      most public mirrors
                                                   are dead in 2026
================  =========  ==========  ========  ===================

Recommended starter:
  * for occasional / batch backfill → ``apify_scraper`` (pay-per-use)
  * for steady stream → ``api_v2_basic`` (predictable cost)
  * for read-only smoke test (only "like_count") → ``og_meta_only``

Score components when fully wired
---------------------------------

::

    follower_component = 0.30 * tanh(log10(followers + 1) / 5)
    engagement_component = 0.20 * (rt_count / max(follower_count, 1) * 1000)
                                    .clip(0, 1)
                                # ratio normalized; 1 RT per 1k followers ≈ 1.0
    verified_component = 0.10 if user.verified else 0.0
    affiliation_component = 0.10 if affiliation in {"Anthropic", "OpenAI",
                                                     "Google DeepMind", ...} else 0
    author_authority_component = author_rules.score(@handle).value * 0.30
    return clip(sum(components), 0, 1)

The author_authority_component double-dips deliberately: when an author
is in ``authors.jsonl``, we use their declared authority directly (more
reliable than follower count for known-credible voices like @karpathy).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .base import Signal, SignalProvider

logger = logging.getLogger(__name__)

_X_STATUS_RE = re.compile(
    r"^https?://(?:x|twitter)\.com/(?P<handle>[\w_]+)/status/(?P<tweet_id>\d+)/?"
)


@dataclass(frozen=True, slots=True)
class TwitterSignalProvider:
    """Stubbed provider — wires interface, defers implementation.

    The orchestrator includes this provider in its registry but it
    returns ``None`` (no contribution) until ``OVP_TWITTER_BACKEND`` is
    configured.  Domain-level authority is meanwhile carried by
    ``DomainRulesProvider`` (x.com baseline 0.55) and per-author
    authority by ``AuthorRulesProvider``, both already shipping.
    """

    name: str = "twitter_engagement"

    def applies(self, source_url: str, frontmatter: dict[str, Any]) -> bool:
        if not source_url:
            return False
        return bool(_X_STATUS_RE.match(source_url))

    def score(
        self, source_url: str, frontmatter: dict[str, Any],
    ) -> Signal | None:
        backend = os.environ.get("OVP_TWITTER_BACKEND", "").strip().lower()
        if not backend:
            return None  # opt-in only

        m = _X_STATUS_RE.match(source_url)
        if not m:
            return None

        handle, tweet_id = m.group("handle"), m.group("tweet_id")

        if backend == "api_v2_basic":
            data = _fetch_api_v2_basic(handle, tweet_id)
        elif backend == "apify_scraper":
            data = _fetch_apify(handle, tweet_id)
        elif backend == "twscrape":
            data = _fetch_twscrape(handle, tweet_id)
        elif backend == "og_meta_only":
            data = _fetch_og_meta(source_url)
        else:
            logger.warning("Unknown OVP_TWITTER_BACKEND=%r", backend)
            return None

        if data is None:
            return None
        return _combine_signal(self.name, handle, data)


# --- backend stubs (raise NotImplementedError until wired) -----------


def _fetch_api_v2_basic(handle: str, tweet_id: str) -> dict[str, Any] | None:
    """X API v2 basic tier ($200/mo, 10K reads/mo).

    Endpoints required:
      * ``GET /2/tweets/{id}?tweet.fields=public_metrics,author_id``
      * ``GET /2/users/{author_id}?user.fields=public_metrics,verified``

    Implementation deferred until a billing decision lands.
    """
    raise NotImplementedError("OVP_TWITTER_BACKEND=api_v2_basic not implemented yet")


def _fetch_apify(handle: str, tweet_id: str) -> dict[str, Any] | None:
    """Apify Twitter Scraper ($0.30/1k tweets pay-per-use).

    Sends a single-tweet job to ``apify/twitter-scraper`` actor and
    waits for the dataset.  Plug in ``APIFY_TOKEN`` env var.
    """
    raise NotImplementedError("OVP_TWITTER_BACKEND=apify_scraper not implemented yet")


def _fetch_twscrape(handle: str, tweet_id: str) -> dict[str, Any] | None:
    """twscrape — open-source, uses real X session cookies.

    Free but in a gray area legally; UI changes break the parser.  Use
    a dedicated throwaway X account; rotate cookies via
    ``TWSCRAPE_ACCOUNTS`` env.  Bundled rate limiter avoids bans.
    """
    raise NotImplementedError("OVP_TWITTER_BACKEND=twscrape not implemented yet")


def _fetch_og_meta(url: str) -> dict[str, Any] | None:
    """Public-only fallback: read OpenGraph meta tags from the tweet URL.

    Gives ``like_count`` (sometimes), tweet text, author handle.  No
    retweet_count, no follower_count.  Better than nothing when no
    paid backend is configured.

    Implementation: ``urllib.request.urlopen(url)`` + BeautifulSoup
    parse ``<meta property="og:*">`` tags.
    """
    raise NotImplementedError("OVP_TWITTER_BACKEND=og_meta_only not implemented yet")


def _combine_signal(name: str, handle: str, data: dict[str, Any]) -> Signal:
    """Apply the score formula documented in the module docstring.

    Common entry point for every backend so the math stays in one place.
    Each backend just normalizes the data shape.
    """
    raise NotImplementedError("Score combination is wired but no backend yet returns data")
