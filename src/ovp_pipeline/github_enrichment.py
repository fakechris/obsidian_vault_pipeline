"""3-tier enrichment for GitHub repo sources (BL-066).

The previous github intake fetched only ``README.md`` + stars and then
ran a 13-section LLM "深度解读" prompt to manufacture an article.  That
template forced abstraction inflation (``项目定位``, ``技术架构``, …)
and produced wiki-shaped output that absorb then had to re-flatten back
into atomic units — two LLM passes, both lossy.

This module replaces the README fetch with a 3-tier chain that returns
a richer, *non-LLM-rewritten* body.  Downstream absorb reads the body
directly with no intermediate template:

    Tier 1  DeepWiki    pre-rendered structured wiki (Devin-indexed).
                        ~70% coverage on our actual github URLs as of
                        2026-05-05.  Highest quality when present —
                        retains specific numbers, method names, model
                        formats, etc.
    Tier 2  GitIngest   clones the repo (depth=1) and concatenates the
                        textual files.  Always works for any public
                        repo; we filter to docs/markdown only so the
                        body stays readable.
    Tier 3  README      original behavior — fetch README.md from
                        raw.githubusercontent.com.  Last-resort.

Each tier returns a tuple ``(body_markdown, metadata)``; the caller
decides which tier to use based on what's available.

Why not Zread:
    Tested 2026-05-05 — Cloudflare's bot protection serves a 403 with
    JS challenge to server-side requests.  Headless-browser fetch is
    out of scope; treat Zread as not-available.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class EnrichedSource:
    """The output of ``enrich_github_source``.

    ``body`` is markdown ready to drop into a ``50-Inbox/03-Processed``
    file.  ``tier`` identifies which fallback was used so frontmatter
    can record provenance.
    """
    owner: str
    repo: str
    tier: str  # "deepwiki" | "gitingest" | "readme"
    body: str
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tier 1 — DeepWiki
# ---------------------------------------------------------------------------

DEEPWIKI_BASE = "https://deepwiki.com"

# DeepWiki gates everything behind a SPA shell.  Real wiki pages contain
# the literal string ``Last indexed`` in the HTML; an unindexed repo
# returns the same shell with a "Loading…" placeholder and no
# ``Last indexed`` marker.  This is the cheapest reliable detector.
_DEEPWIKI_INDEXED_MARKER = "Last indexed"

# Cap section count so a pathological wiki doesn't run away.  Real
# DeepWikis we sampled have 8-25 sections; 60 is well above that.
_DEEPWIKI_MAX_SECTIONS = 60


def _http_get(url: str, timeout: float = 12.0) -> Optional[str]:
    """GET a URL and return the response body, or None on any failure.

    We deliberately swallow exceptions and return None — the caller is
    a fallback chain, and any HTTP error means "try the next tier".
    """
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "ovp-github-enrichment/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            return raw.decode("utf-8", errors="ignore")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        logger.debug("HTTP GET failed for %s: %s", url, exc)
        return None
    except Exception as exc:  # noqa: BLE001 — fallback chain, never raise
        logger.debug("HTTP GET unexpected failure for %s: %s", url, exc)
        return None


def _is_deepwiki_indexed(html: str) -> bool:
    return _DEEPWIKI_INDEXED_MARKER in html


def _extract_deepwiki_section_paths(html: str, owner: str, repo: str) -> list[str]:
    """Find the ordered list of section subpaths from the index page.

    DeepWiki's nav includes hrefs like ``/<owner>/<repo>/1-overview``,
    ``/<owner>/<repo>/1.1-installation``, etc.  We collect them in
    document order, dedupe, and cap.
    """
    pattern = re.compile(
        rf'href="(/{re.escape(owner)}/{re.escape(repo)}/([\w.-]+))"',
    )
    seen: set[str] = set()
    paths: list[str] = []
    for match in pattern.finditer(html):
        path, slug = match.group(1), match.group(2)
        # skip the bare repo path
        if not slug or slug in seen:
            continue
        seen.add(slug)
        paths.append(path)
        if len(paths) >= _DEEPWIKI_MAX_SECTIONS:
            break
    return paths


def _strip_deepwiki_chrome(html: str) -> str:
    """Strip script/style and the SPA navigation chrome, leaving only
    the article body.

    DeepWiki's rendered HTML embeds the article between the section
    title (e.g. "Overview Relevant source files README.md") and the
    "Sources:" trailer near the end.  We extract everything in between
    and run a hand-rolled HTML→markdown that's good enough for the
    downstream absorb prompt to read.
    """
    # Drop scripts/styles entirely
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)

    # Hand-rolled tag-to-markdown — keeps it dependency-free.  We
    # don't need perfect rendering; we need readable text with section
    # structure intact so absorb can pick up specifics.
    text = html
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<h1[^>]*>(.*?)</h1>", r"\n# \1\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<h2[^>]*>(.*?)</h2>", r"\n## \1\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<h3[^>]*>(.*?)</h3>", r"\n### \1\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<h4[^>]*>(.*?)</h4>", r"\n#### \1\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<h5[^>]*>(.*?)</h5>", r"\n##### \1\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<h6[^>]*>(.*?)</h6>", r"\n###### \1\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<li[^>]*>(.*?)</li>", r"- \1\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<pre[^>]*>(.*?)</pre>", r"\n```\n\1\n```\n", text, flags=re.DOTALL | re.IGNORECASE)
    # collapse remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    # decode common entities
    text = (
        text
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&#x27;", "'")
        .replace("&nbsp;", " ")
    )
    # collapse runs of whitespace, preserve paragraph breaks
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _isolate_deepwiki_main_content(text: str) -> str:
    """Drop the SPA chrome (header/nav with 'Index your code with Devin'
    etc.) so what remains is the article body.

    Heuristic: real content begins at the first content marker we
    can find — the 'Relevant source files' line, or the first level-2
    heading.  Everything before that is nav.
    """
    # The most common content start
    for marker in (
        "Relevant source files",
        "This document provides",
        "What is",
    ):
        idx = text.find(marker)
        if idx > 200:  # skip if marker is in the nav block
            return text[idx:].strip()
    # Fallback: drop everything up to the first ## heading
    h2_match = re.search(r"^##\s", text, flags=re.MULTILINE)
    if h2_match:
        return text[h2_match.start():].strip()
    return text


def fetch_deepwiki(owner: str, repo: str) -> Optional[tuple[str, dict]]:
    """Try to fetch a DeepWiki for ``{owner}/{repo}``.

    Returns ``(body_markdown, metadata)`` if a real wiki exists.
    Returns ``None`` when the repo isn't indexed.
    """
    index_url = f"{DEEPWIKI_BASE}/{owner}/{repo}"
    index_html = _http_get(index_url)
    if not index_html:
        return None
    if not _is_deepwiki_indexed(index_html):
        return None

    # Extract last-indexed date for metadata
    last_indexed_match = re.search(
        r"Last indexed[:\s]*([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})",
        index_html,
    )
    last_indexed = last_indexed_match.group(1) if last_indexed_match else None

    # Walk every section subpage.  We start with the index page itself
    # (which renders the "Overview" section as visible text) and then
    # follow each numbered subpath.
    sections: list[tuple[str, str]] = []  # (slug, markdown_body)
    main_text = _strip_deepwiki_chrome(index_html)
    main_body = _isolate_deepwiki_main_content(main_text)
    if main_body:
        sections.append(("index", main_body))

    section_paths = _extract_deepwiki_section_paths(index_html, owner, repo)
    for sp in section_paths:
        url = f"{DEEPWIKI_BASE}{sp}"
        html = _http_get(url)
        if not html:
            continue
        text = _strip_deepwiki_chrome(html)
        body = _isolate_deepwiki_main_content(text)
        if body:
            slug = sp.rsplit("/", 1)[-1]
            sections.append((slug, body))
        # be polite — DeepWiki appears to throttle
        time.sleep(0.4)

    if not sections:
        return None

    # Concatenate sections.  We don't dedupe across sections — DeepWiki
    # itself may repeat headers across pages, but that's the source's
    # decision and absorb can ignore boilerplate.
    body_parts = [f"_DeepWiki section: {slug}_\n\n{body}" for slug, body in sections]
    body = "\n\n---\n\n".join(body_parts)

    metadata = {
        "tier": "deepwiki",
        "deepwiki_url": index_url,
        "deepwiki_section_count": len(sections),
        "deepwiki_last_indexed": last_indexed,
    }
    return body, metadata


# ---------------------------------------------------------------------------
# Tier 2 — GitIngest
# ---------------------------------------------------------------------------


# Filter the GitIngest content to docs / markdown / config.  The full
# clone often hits 5-50MB of source code that absorb has no use for —
# we want narrative documentation, not implementation details.
_GITINGEST_DOC_PATTERNS = re.compile(
    r"(README|CHANGELOG|HISTORY|TRAINING|ARCHITECTURE|DESIGN|"
    r"CONTRIBUTING|ROADMAP|SECURITY|GOVERNANCE|API|GLOSSARY|"
    r"AGENTS|CLAUDE|GUIDE|TUTORIAL|FAQ|NOTES|MIGRATION|UPGRADE|"
    r"docs?/|documentation/|wiki/|examples?/[^/]+\.(md|rst))",
    re.IGNORECASE,
)

# Hard upper bound on body size we'll inline into a vault markdown —
# beyond this the file becomes unreadable in Obsidian and absorb is
# unlikely to read past it anyway.
_GITINGEST_MAX_BODY_CHARS = 60_000


def _gitingest_filter_content(content: str) -> str:
    """Keep only file blocks whose path matches a documentation pattern.

    GitIngest output is structured as repeated::

        ============================================================
        FILE: <path>
        ============================================================
        <content>
        ...

    We split on the divider, keep documentation files, drop source
    code.  This is heuristic but conservative.
    """
    parts = re.split(r"=+\s*\nFILE:\s*([^\n]+)\n=+\s*\n", content)
    # parts is [pre, path1, body1, path2, body2, ...]
    if len(parts) < 3:
        return content[:_GITINGEST_MAX_BODY_CHARS]
    kept: list[str] = []
    for i in range(1, len(parts), 2):
        path = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        if not _GITINGEST_DOC_PATTERNS.search(path):
            continue
        kept.append(f"## File: {path}\n\n{body.strip()}")
    if not kept:
        # No docs found — fall back to the first file (usually README)
        first_path = parts[1].strip()
        first_body = parts[2] if len(parts) > 2 else ""
        kept.append(f"## File: {first_path}\n\n{first_body.strip()}")
    out = "\n\n---\n\n".join(kept)
    if len(out) > _GITINGEST_MAX_BODY_CHARS:
        out = out[:_GITINGEST_MAX_BODY_CHARS] + "\n\n_[truncated by enrichment cap]_"
    return out


def fetch_gitingest(owner: str, repo: str) -> Optional[tuple[str, dict]]:
    """Clone the repo via the gitingest python package and assemble a
    markdown body from its documentation files.

    Returns ``None`` if gitingest is not installed or the clone fails.
    """
    try:
        from gitingest import ingest  # type: ignore
    except ImportError:
        logger.warning(
            "gitingest not installed — skipping Tier 2 (pip install gitingest)",
        )
        return None

    url = f"https://github.com/{owner}/{repo}"
    try:
        summary, tree, content = ingest(url)
    except Exception as exc:  # noqa: BLE001 — clone failures fall through
        logger.warning("gitingest failed for %s: %s", url, exc)
        return None

    filtered = _gitingest_filter_content(content)

    # Build the body: summary + tree + filtered content
    body_parts = [
        "_GitIngest summary:_",
        summary.strip(),
        "",
        "## Directory tree",
        "",
        "```",
        tree.strip(),
        "```",
        "",
        "## Documentation files",
        "",
        filtered,
    ]
    body = "\n".join(body_parts)

    # Pull the commit hash from the summary so the metadata is
    # reproducible even if the repo moves.
    commit_match = re.search(r"Commit:\s*([0-9a-f]{40})", summary)
    file_count_match = re.search(r"Files analyzed:\s*(\d+)", summary)
    metadata = {
        "tier": "gitingest",
        "gitingest_url": url,
        "gitingest_commit": commit_match.group(1) if commit_match else None,
        "gitingest_file_count": int(file_count_match.group(1)) if file_count_match else None,
    }
    return body, metadata


# ---------------------------------------------------------------------------
# Tier 3 — README + stars (existing behavior)
# ---------------------------------------------------------------------------


_README_FILENAMES = (
    "README.md", "readme.md", "Readme.md",
    "README.markdown", "README.rst", "README.txt", "README",
)
_README_BRANCH_FALLBACKS = ("main", "master", "develop", "dev", "trunk")


def fetch_repo_metadata(owner: str, repo: str) -> tuple[Optional[str], int]:
    """Cheap probe for ``default_branch`` and ``stargazers_count``.

    Single request to ``api.github.com/repos/{owner}/{repo}`` — no
    raw README probing.  Used by ``enrich_github_source`` to pay
    for the metadata once up front so Tier 1/2 can short-circuit
    without ever touching the README path.

    Pre-fix, the only way to get ``stars`` was via ``fetch_readme``
    which probed up to 7 README filenames × 5 branches.  That
    meant DeepWiki-served repos still paid the full Tier-3
    network cost just to fill in the stars frontmatter field.

    Returns ``(default_branch, stars)``.  ``default_branch`` is
    ``None`` when the API call fails (rate-limited, repo gone,
    etc.) — callers should fall back to the hardcoded branch list.
    """
    api_text = _http_get(
        f"https://api.github.com/repos/{owner}/{repo}",
        timeout=10.0,
    )
    if not api_text:
        return None, 0
    try:
        data = json.loads(api_text)
        return (
            data.get("default_branch") or None,
            int(data.get("stargazers_count", 0) or 0),
        )
    except (json.JSONDecodeError, ValueError, TypeError):
        return None, 0


def fetch_readme(
    owner: str, repo: str,
    *,
    default_branch: Optional[str] = None,
) -> tuple[str, int]:
    """Final fallback — fetch README body.

    Returns ``(body, stars)`` for backward compatibility, but
    callers that already know the stars (via ``fetch_repo_metadata``)
    can ignore the second tuple element.  ``default_branch`` lets
    a caller skip the duplicate API roundtrip — when omitted, this
    function fetches the metadata itself.

    Strategy when ``default_branch`` is unknown:
      1. Hit ``api.github.com/repos/{owner}/{repo}`` to get the
         default branch and stars in one request.
      2. Try each filename in ``_README_FILENAMES`` against the
         default branch first, then the hardcoded fallback branches.

    Always returns a tuple; ``body`` may be empty if the repo has
    no README anywhere we can find.
    """
    stars = 0
    if default_branch is None:
        default_branch, stars = fetch_repo_metadata(owner, repo)

    # Build branch list: API-reported default first, then the hardcoded
    # fallbacks (deduped, preserving order).
    branches: list[str] = []
    seen: set[str] = set()
    for branch in (default_branch, *_README_BRANCH_FALLBACKS):
        if branch and branch not in seen:
            seen.add(branch)
            branches.append(branch)

    for branch in branches:
        for filename in _README_FILENAMES:
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{filename}"
            text = _http_get(url, timeout=10.0)
            if text:
                return text, stars

    return "", stars


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def enrich_github_source(owner: str, repo: str) -> EnrichedSource:
    """Run the 3-tier chain and return the best body we can assemble.

    Always returns an ``EnrichedSource``.  When all tiers fail (e.g.
    repo deleted), ``body`` is empty and ``tier='readme'`` —
    downstream callers should check ``len(body)`` before trying to
    extract knowledge from it.
    """
    # Stars + default_branch are stable signals independent of body
    # provenance, but fetching them via ``fetch_readme`` would also
    # probe up to 7 README files × 5 branches.  Pay the metadata
    # cost once up front so Tier 1/2 hits don't drag the README
    # network round-trips along for the ride.
    default_branch, stars = fetch_repo_metadata(owner, repo)

    # Tier 1
    deepwiki_result = fetch_deepwiki(owner, repo)
    if deepwiki_result is not None:
        body, meta = deepwiki_result
        meta["github_stars"] = stars
        return EnrichedSource(owner=owner, repo=repo, tier="deepwiki", body=body, metadata=meta)

    # Tier 2
    gitingest_result = fetch_gitingest(owner, repo)
    if gitingest_result is not None:
        body, meta = gitingest_result
        meta["github_stars"] = stars
        return EnrichedSource(owner=owner, repo=repo, tier="gitingest", body=body, metadata=meta)

    # Tier 3 — README only.  Reuse the default_branch we already
    # know to avoid a duplicate API hit.
    body, _ = fetch_readme(owner, repo, default_branch=default_branch)
    return EnrichedSource(
        owner=owner, repo=repo, tier="readme",
        body=body,
        metadata={"tier": "readme", "github_stars": stars},
    )


def parse_github_url(url: str) -> Optional[tuple[str, str]]:
    """Parse ``https://github.com/owner/repo`` into ``(owner, repo)``."""
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    # ``str.lstrip(chars)`` strips a CHARACTER CLASS, not a prefix string —
    # ``"wwwgithub.com".lstrip("www.")`` returns ``"github.com"`` and would
    # incorrectly accept that as a github URL.  Use ``removeprefix`` so we
    # only strip the literal ``www.`` prefix.
    if netloc.removeprefix("www.") != "github.com":
        return None
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    repo = parts[1]
    # strip .git suffix and any trailing tree/blob refs
    if repo.endswith(".git"):
        repo = repo[:-4]
    return parts[0], repo
