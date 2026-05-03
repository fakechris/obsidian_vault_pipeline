"""Shared URL / host normalization for the source-authority subsystem.

The same logic was duplicated across ``domain_rules.py``,
``author_rules.py``, ``commands/source_coverage.py``, and
``commands/score_domain.py`` — including a recurring ``lstrip("www.")``
bug that this module fixes once.

The two operations:

  * ``normalize_host(url)`` — return the registrable host with port /
    userinfo / leading "www." stripped.  Returns ``""`` on parse error.
  * ``extract_x_handle(url)`` — return the lowercased Twitter/X handle
    from a status URL, or ``None`` if the URL isn't an X status link.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse


_X_HOSTS = frozenset({"x.com", "twitter.com"})
_X_HANDLE_FROM_STATUS = re.compile(
    r"^/?(?:@)?(?P<handle>[A-Za-z0-9_]+)/status/"
)


def normalize_host(url: str) -> str:
    """Return the canonical host for a URL: lowercased, no port/userinfo,
    no leading ``www.``.

    Examples
    --------
    >>> normalize_host("https://www.Anthropic.com/news/x")
    'anthropic.com'
    >>> normalize_host("https://example.com:443/path")
    'example.com'
    >>> normalize_host("not-a-url")
    ''

    Returns the empty string on parse error or empty input — callers
    are expected to short-circuit on ``""``.
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    # ``parsed.hostname`` strips port and userinfo (would otherwise
    # mangle classification for ``host:443`` style URLs).
    host = (parsed.hostname or "").lower()
    # Use prefix removal — ``lstrip("www.")`` was a character-set
    # strip that mangled hosts like ``web.com`` → ``eb.com``.
    if host.startswith("www."):
        host = host[4:]
    return host


def extract_x_handle(url: str) -> str | None:
    """Return the lowercased X / Twitter handle from a status URL.

    Returns ``None`` when the URL isn't an X status link or is
    malformed (rather than throwing).
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in _X_HOSTS:
        return None
    m = _X_HANDLE_FROM_STATUS.match(parsed.path or "")
    return m.group("handle").lower() if m else None
