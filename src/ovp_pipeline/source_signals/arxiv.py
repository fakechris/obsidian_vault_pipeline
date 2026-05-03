"""T2 signal: arXiv paper metadata.

For ``arxiv.org/abs/<id>`` URLs, fetch metadata via the public arXiv
API and score on:

  * is the paper from a recognized lab (DeepMind / OpenAI / Anthropic /
    Meta AI / FAIR / MSR)?  → +0.15
  * recency: published within 2y → +0.10, within 5y → +0.05

Note: arXiv API doesn't expose citation count or author-count signals
reliably (affiliation isn't always present in the public XML).  For
citations we'd need Semantic Scholar (separate provider, can be added
later).

Score formula
-------------

    base       = 0.65                              # arxiv default authority
    lab_bonus  = 0.15 if any(known_lab) in authors else 0
    recency    = 0.10 if published < 2y else 0.05 if < 5y else 0
    return min(1.0, base + lab_bonus + recency)
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# arXiv API XML responses come from a trusted endpoint, but
# ``xml.etree.ElementTree`` is documented as unsafe for untrusted
# input (XXE / billion-laughs).  ``defusedxml.ElementTree`` is a
# drop-in replacement with the same parsing API.
try:
    from defusedxml import ElementTree as ET  # type: ignore
except ImportError:  # pragma: no cover - defusedxml is in requirements
    import xml.etree.ElementTree as ET  # type: ignore[no-redef]

from .base import Signal

logger = logging.getLogger(__name__)

_ARXIV_ID_RE = re.compile(
    r"^https?://arxiv\.org/(?:abs|pdf)/(?P<id>\d{4}\.\d{4,5}|\w+/\d{7,})(?:v\d+)?(?:\.pdf)?/?$"
)
_API = "https://export.arxiv.org/api/query?id_list={id}"
_NS = {"a": "http://www.w3.org/2005/Atom"}
_TIMEOUT_S = 10.0

_KNOWN_LABS = (
    "google deepmind", "deepmind", "google research", "google brain",
    "openai", "anthropic", "meta ai", "fair", "facebook ai",
    "microsoft research", "msr", "apple machine learning",
    "nvidia research", "allen institute", "ai2",
    "stanford", "mit", "berkeley", "carnegie mellon", "cmu",
)


@dataclass(frozen=True, slots=True)
class ArxivSignalProvider:
    name: str = "arxiv"
    user_agent: str = "ovp-pipeline-source-authority/0.1"

    def applies(self, source_url: str, frontmatter: dict[str, Any]) -> bool:
        return bool(source_url and _ARXIV_ID_RE.match(source_url))

    def score(
        self, source_url: str, frontmatter: dict[str, Any],
    ) -> Signal | None:
        m = _ARXIV_ID_RE.match(source_url)
        if not m:
            return None
        arxiv_id = m.group("id")
        url = _API.format(id=arxiv_id)
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r:
                xml_text = r.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            logger.warning("arXiv API error for %s: %s", arxiv_id, exc)
            return None

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None

        entry = root.find("a:entry", _NS)
        if entry is None:
            return Signal(
                provider=self.name, value=0.65,
                raw={"id": arxiv_id, "metadata": "unavailable"},
            )

        # Authors + affiliations (free-form text, lowercased for matching).
        author_blob = " ".join(
            (a.findtext("a:name", "", _NS) or "")
            for a in entry.findall("a:author", _NS)
        ).lower()
        # Affiliation often appears as <arxiv:affiliation>; arXiv doesn't
        # always expose it via the public API, so we settle for author
        # name / abstract matching.
        abstract = (entry.findtext("a:summary", "", _NS) or "").lower()

        lab_bonus = 0.15 if any(lab in author_blob or lab in abstract for lab in _KNOWN_LABS) else 0.0

        published = entry.findtext("a:published", "", _NS)
        recency_component = 0.0
        if published:
            try:
                pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - pub_dt).days
                if age_days <= 365 * 2:
                    recency_component = 0.10
                elif age_days <= 365 * 5:
                    recency_component = 0.05
            except ValueError:
                pass

        value = min(1.0, 0.65 + lab_bonus + recency_component)

        return Signal(
            provider=self.name,
            value=round(value, 3),
            raw={
                "id": arxiv_id,
                "title": entry.findtext("a:title", "", _NS).strip()[:120],
                "published": published,
                "lab_bonus": lab_bonus,
                "recency": recency_component,
            },
        )
