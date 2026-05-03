"""T1 signal: deterministic domain + URL-pattern authority.

Hard-coded weights based on observed reliability of frequently-cited
domains in the OVP vault.  No external network calls.

Three buckets:

  CANONICAL (0.85-0.95) — official channels, primary sources
  MIXED    (0.50-0.75)  — community-quality but variable
  UNKNOWN  (0.45)       — default for unrecognized domains

Path overrides: a few domains have wildly different quality between
sub-paths (Medium official publications vs random user blogs;
github.com pages with ``/orgs/`` vs ``/users/``).  Those are encoded
as path-pattern overrides.

Editing this map is the primary lever for domain-level recalibration —
keep additions data-driven (track which domains we ingest most) and
backed by a one-line rationale comment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .base import Signal, SignalProvider


# Default authority for any URL whose domain isn't in the table.
_DEFAULT_AUTHORITY = 0.45

# Canonical primary sources — first-party announcements.
_CANONICAL = {
    "anthropic.com": 0.95,
    "openai.com": 0.95,
    "deepmind.google": 0.95,
    "ai.google.dev": 0.92,
    "blog.google": 0.90,
    "x.ai": 0.90,
    "mistral.ai": 0.92,
    "together.ai": 0.88,
    "huggingface.co": 0.90,        # blog + papers; user spaces are mixed
    "research.google": 0.95,
    "developer.nvidia.com": 0.92,
    "blogs.microsoft.com": 0.88,
    "openrouter.ai": 0.88,
    "cursor.sh": 0.85,
    "github.blog": 0.92,
    "stripe.com": 0.92,            # /blog and /docs only
    "vercel.com": 0.88,
    "supabase.com": 0.85,
}

# Recognized commentary / well-curated venues.
_MIXED = {
    "github.com": 0.70,            # adjusted up by stars in T2 (github.py)
    "arxiv.org": 0.78,             # paper authority varies; T2 enriches
    "x.com": 0.55,                 # author authority is the real signal
    "twitter.com": 0.55,
    "medium.com": 0.50,            # official publications get bumped via path
    "substack.com": 0.55,          # T3 enriches with subscriber data
    "lesswrong.com": 0.75,
    "reddit.com": 0.45,
    "ycombinator.com": 0.65,
    "news.ycombinator.com": 0.50,  # comments quality varies
    "dev.to": 0.55,
    "stackoverflow.com": 0.70,
    "techcrunch.com": 0.60,
    "theverge.com": 0.65,
    "venturebeat.com": 0.55,
    "wired.com": 0.70,
    "newyorker.com": 0.78,
    "ft.com": 0.80,
    "wsj.com": 0.80,
    "bloomberg.com": 0.75,
    "youtube.com": 0.55,           # channel-dependent, manual curation needed
    "zhihu.com": 0.55,
}

# Path-level overrides for domains where authority varies sharply by
# sub-path.  Each entry now scopes the path pattern to specific hosts
# so a generic ``/blog/`` path on a random domain doesn't get the
# canonical-blog bump (catches the case where any unrecognized site
# uses ``/blog/`` for marketing content).
#
# The "" host pattern means "any host" — kept for genuinely
# domain-agnostic paths like the GitHub org-listing dampener (which is
# already host-locked to github.com elsewhere).
_PATH_OVERRIDES: list[tuple[set[str], re.Pattern, float, str]] = [
    # Github org listing pages — never canonical knowledge artifacts
    ({"github.com"}, re.compile(r"^/orgs/"), 0.05, "github org listing — ignore"),
]


# Hosts where a "/blog/" or "/research/" path should be treated as
# canonical even if we haven't whitelisted the bare domain.  Kept
# narrow so unknown domains don't get a free lift.
_TRUSTED_BLOG_HOSTS: set[str] = {
    # Add hosts here as we observe genuinely high-quality blog content.
    # Empty by default — bare domain rules are the primary lift.
}


@dataclass(frozen=True, slots=True)
class DomainRulesProvider:
    """Look up a hard-coded authority score by domain + path."""

    name: str = "domain_rules"

    def applies(self, source_url: str, frontmatter: dict[str, Any]) -> bool:
        return bool(source_url and source_url.startswith(("http://", "https://")))

    def score(
        self, source_url: str, frontmatter: dict[str, Any],
    ) -> Signal | None:
        try:
            parsed = urlparse(source_url)
        except ValueError:
            return None
        # ``parsed.hostname`` strips port/userinfo (would otherwise
        # misclassify ``example.com:443`` as an unknown domain).
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = parsed.path or "/"

        # 1. Host-scoped path overrides (e.g. github.com /orgs/)
        for hosts, pattern, override, reason in _PATH_OVERRIDES:
            if hosts and host not in hosts:
                continue
            if pattern.search(path):
                return Signal(
                    provider=self.name,
                    value=override,
                    raw={"host": host, "path": path, "reason": reason},
                )

        # 2. Trusted-blog-host /blog/ or /research/ canonical bump
        if host in _TRUSTED_BLOG_HOSTS and (
            path.startswith("/blog/") or path.startswith("/research/")
        ):
            return Signal(
                provider=self.name, value=0.85,
                raw={"host": host, "path": path, "reason": "trusted blog path"},
            )

        # 3. Domain table — exact match first
        for table, label in ((_CANONICAL, "canonical"), (_MIXED, "mixed")):
            if host in table:
                return Signal(
                    provider=self.name,
                    value=table[host],
                    raw={"host": host, "bucket": label},
                )

        # 4. Subdomain → parent match (e.g. blog.huggingface.co → huggingface.co).
        # Use ``_extract_registrable_domain`` which handles common
        # multi-part TLDs (.co.uk, .com.au, .ac.jp) instead of the naive
        # ``host.split(".")[-2:]`` that misclassified them.
        parent = _extract_registrable_domain(host)
        if parent and parent != host:
            for table, label in ((_CANONICAL, "canonical"), (_MIXED, "mixed")):
                if parent in table:
                    return Signal(
                        provider=self.name,
                        value=table[parent] - 0.05,  # slight penalty for subdomain
                        raw={"host": host, "matched": parent, "bucket": label},
                    )

        # 5. Default
        return Signal(
            provider=self.name,
            value=_DEFAULT_AUTHORITY,
            raw={"host": host, "bucket": "unknown"},
        )


# Common multi-part public suffixes that the naive last-2-labels split
# would mis-handle.  Not exhaustive (the full PSL has thousands), but
# covers the venues we actually encounter in practice.
_MULTIPART_SUFFIXES = frozenset({
    "co.uk", "com.au", "co.jp", "ac.jp", "co.kr", "com.cn", "com.hk",
    "com.sg", "com.br", "com.mx", "com.tr", "co.in", "co.nz", "co.za",
})


def _extract_registrable_domain(host: str) -> str:
    """Return the registrable domain from a hostname.

    Handles common multi-part public suffixes (``.co.uk``, ``.com.au``)
    that would otherwise be misclassified by ``host.split(".")[-2:]``.
    Falls back to the last two labels for everything else.

    Examples
    --------
    >>> _extract_registrable_domain("blog.example.com")
    'example.com'
    >>> _extract_registrable_domain("blog.example.co.uk")
    'example.co.uk'
    >>> _extract_registrable_domain("example.com")
    'example.com'
    """
    parts = host.split(".")
    if len(parts) < 2:
        return host
    last_two = ".".join(parts[-2:])
    if last_two in _MULTIPART_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return last_two
