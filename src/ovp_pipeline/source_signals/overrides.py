"""User-editable YAML overrides for domain + author authority.

Hardcoded ``_CANONICAL`` / ``_MIXED`` tables in ``domain_rules.py`` cover
the well-known top-30 hosts.  Real users see a long tail of domains
(WeChat 公众号, 小红书, B站, Cloudflare blog, etc.) that no curator can
reasonably enumerate in source code.  This module loads two YAML files
that override / extend the hardcoded tables without touching code:

  60-Logs/domain_overrides.yaml
  60-Logs/author_overrides.yaml

Both are optional — if either file is missing, the hardcoded tables
are returned unchanged.

YAML schema
-----------

domain_overrides.yaml::

    domains:
      mp.weixin.qq.com:
        authority: 0.55
        bucket: mixed
        rationale: "WeChat aggregator — author-level signal needed"
        source: llm_assisted | manual | imported
        added_at: "2026-05-03"
      cloudflare.com:
        authority: 0.85
        bucket: canonical
        rationale: "Top-tier infrastructure technical blog"
        source: manual
    excluded_hosts:
      # Hosts that should never be scored — e.g. local clippings,
      # test fixtures.  Returns Signal value=0.45 default but flags
      # the source as ``excluded: true`` in audit log.
      - localhost
      - 127.0.0.1

author_overrides.yaml::

    authors:
      - handle: "newperson"
        aliases: ["A. New Person"]
        authority: 0.78
        rationale: "..."
        added_at: "2026-05-03"

Loading + merge semantics
-------------------------

* Override values **win over** hardcoded values when both exist.
* YAML schema mismatches log a warning and skip the entry, never crash.
* The ``OverridesLoader`` caches the parsed file by mtime — repeated
  calls within a single CLI invocation don't re-parse.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .url_utils import normalize_host

logger = logging.getLogger(__name__)


def _normalize_override_host(host: str) -> str:
    """Apply the same host canonicalization that DomainRulesProvider uses
    when looking up source URLs, so a YAML key like ``www.cloudflare.com``
    or ``HTTPS://Cloudflare.COM`` matches a runtime ``cloudflare.com`` lookup.

    ``normalize_host`` expects a URL — wrap bare hosts in ``https://``.
    """
    if not host:
        return ""
    s = host.strip()
    if "://" not in s:
        s = "https://" + s
    return normalize_host(s)


@dataclass
class DomainOverrides:
    """Loaded view of ``domain_overrides.yaml``.

    Fields are read-only after construction; reload by creating a new
    instance via ``DomainOverrides.load(path)``.
    """

    domains: dict[str, dict[str, Any]] = field(default_factory=dict)
    excluded_hosts: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Path) -> "DomainOverrides":
        if not path.exists():
            return cls()
        try:
            import yaml
        except ImportError:
            logger.warning("PyYAML not available; domain overrides disabled")
            return cls()

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
            logger.warning("Failed to parse %s: %s", path, exc)
            return cls()
        if not isinstance(data, dict):
            return cls()

        domains: dict[str, dict[str, Any]] = {}
        domains_raw = data.get("domains")
        if domains_raw is None:
            domains_raw = {}
        elif not isinstance(domains_raw, dict):
            logger.warning(
                "%s: 'domains' must be a mapping, got %s — skipping section",
                path, type(domains_raw).__name__,
            )
            domains_raw = {}
        for host, entry in domains_raw.items():
            if not isinstance(entry, dict):
                continue
            authority = entry.get("authority")
            try:
                authority_f = float(authority)
            except (TypeError, ValueError):
                logger.warning(
                    "Skipping override for %s: missing/invalid authority", host,
                )
                continue
            if not 0.0 <= authority_f <= 1.0:
                logger.warning(
                    "Skipping override for %s: authority %s outside [0, 1]",
                    host, authority_f,
                )
                continue
            # Normalize the YAML key the same way runtime lookups normalize
            # source URLs, so 'www.foo.com' / 'HTTPS://Foo.com' / 'foo.com'
            # all collapse to one entry.
            normalized = _normalize_override_host(str(host))
            if not normalized:
                logger.warning("Skipping override with empty host: %r", host)
                continue
            domains[normalized] = {
                "authority": authority_f,
                "bucket": str(entry.get("bucket", "manual")),
                "rationale": str(entry.get("rationale", "")),
                "source": str(entry.get("source", "manual")),
                "added_at": str(entry.get("added_at", "")),
            }

        excluded: set[str] = set()
        excluded_raw = data.get("excluded_hosts") or []
        if not isinstance(excluded_raw, list):
            logger.warning(
                "%s: 'excluded_hosts' must be a list, got %s",
                path, type(excluded_raw).__name__,
            )
            excluded_raw = []
        for host in excluded_raw:
            if isinstance(host, str) and host:
                normalized = _normalize_override_host(host)
                if normalized:
                    excluded.add(normalized)

        return cls(domains=domains, excluded_hosts=excluded)


@dataclass
class AuthorOverrides:
    """Loaded view of ``author_overrides.yaml``.

    Same schema as ``60-Logs/authors.jsonl`` (which remains the
    primary curation surface) — yaml is provided as an alternative for
    users who prefer editing a single grouped file.  Both load paths
    are merged at provider construction time.
    """

    authors: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "AuthorOverrides":
        if not path.exists():
            return cls()
        try:
            import yaml
        except ImportError:
            logger.warning("PyYAML not available; author overrides disabled")
            return cls()

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
            logger.warning("Failed to parse %s: %s", path, exc)
            return cls()
        if not isinstance(data, dict):
            return cls()

        out: list[dict[str, Any]] = []
        for entry in (data.get("authors") or []):
            if not isinstance(entry, dict):
                continue
            handle = entry.get("handle")
            if not isinstance(handle, str) or not handle:
                continue
            authority = entry.get("authority")
            try:
                authority_f = float(authority)
            except (TypeError, ValueError):
                logger.warning(
                    "Skipping author override %s: missing/invalid authority %r",
                    handle, authority,
                )
                continue
            clamped = max(0.0, min(1.0, authority_f))
            if clamped != authority_f:
                logger.warning(
                    "Clamping author %s authority %s to %s (must be in [0, 1])",
                    handle, authority_f, clamped,
                )
            out.append({
                "handle": handle.lower().lstrip("@"),
                "aliases": [
                    a.lower().lstrip("@")
                    for a in (entry.get("aliases") or [])
                    if isinstance(a, str)
                ],
                "authority": clamped,
                "rationale": str(entry.get("rationale", "")),
                "added_at": str(entry.get("added_at", "")),
            })
        return cls(authors=out)
