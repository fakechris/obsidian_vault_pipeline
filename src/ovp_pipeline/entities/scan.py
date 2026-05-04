"""Scan a vault for entity references.

The recon script in ``/tmp/recon_entities.py`` proved the design;
this is the productionized version.

Why "anywhere in markdown" rather than "frontmatter source only":
  * a Twitter handle may be quoted as ``@karpathy`` in body prose
    even when the source URL is GitHub or anthropic.com
  * a GitHub repo may be linked in a wikilink like
    ``[karpathy/nanoGPT](https://github.com/karpathy/nanoGPT)`` from
    a regular evergreen note
  * the entity-as-citizen view is platform-agnostic; we should pick
    up *all* mentions, not just the ones lucky enough to be the
    primary source of a clipping
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


# Shared URL terminator class — applied as a lookahead so the
# captured group stops at any character that ends a URL inside prose
# (path separators, query/fragment markers, closing brackets/parens
# from markdown links, whitespace, quotes).
_URL_END = r"(?=[/?#)\]\s\"'`>]|$)"

# X/Twitter handle inside a status URL or a bare profile URL.
# Accepts both ``x.com/<handle>`` and ``x.com/<handle>/status/<id>``.
# The terminator prevents over-matching on sub-paths like
# ``x.com/karpathy/settings`` (which would otherwise capture
# ``karpathy`` AND continue reading).
_X_HANDLE_RE = re.compile(
    rf"(?:x|twitter)\.com/(@?[A-Za-z0-9_]+)(?:/status/\d+)?{_URL_END}",
    re.IGNORECASE,
)

# GitHub owner/repo from a URL.
_GH_REPO_RE = re.compile(
    rf"github\.com/([\w.-]+)/([\w.-]+?){_URL_END}",
    re.IGNORECASE,
)

# Bare GitHub profile URL — ``github.com/<owner>`` with NO ``/<repo>``
# segment after.  Catches profile-only mentions that the repo regex
# misses (e.g. ``Visit https://github.com/karpathy``), which would
# otherwise be invisible to backfill_github's user pass.
# Note: the terminator class includes ``.,`` — sentence delimiters
# would otherwise prevent a match at end-of-clause prose like
# ``Visit github.com/karpathy.`` or ``..., github.com/karpathy,``.
# Crucially the class still excludes ``/`` so we don't overlap with
# the owner/repo regex.
_GH_OWNER_RE = re.compile(
    r"github\.com/([\w.-]+?)(?=[?#)\],.\s\"'`>]|$)",
    re.IGNORECASE,
)

# Path segments that look like ``<owner>/<repo>`` to the regex but are
# really top-level GitHub features.  Drop them.
_GH_NON_REPO_OWNERS = frozenset({
    "orgs", "topics", "marketplace", "settings", "pricing", "search",
    "about", "features", "explore", "trending", "issues", "pulls",
    "notifications", "sponsors", "collections", "events",
})

# Reserved X paths that look like handles but aren't.
_X_NON_HANDLE = frozenset({
    "home", "explore", "i", "search", "messages", "notifications",
    "compose", "settings", "intent", "share", "login", "signup",
    "tos", "privacy", "about",
})


@dataclass(frozen=True, slots=True)
class HandleMention:
    """One X/Twitter handle and how often it appears across the vault."""

    handle: str            # lowercased, no @
    mention_count: int     # total occurrences across all files
    file_count: int        # number of distinct files that mention it


@dataclass(frozen=True, slots=True)
class GitHubMention:
    """A GitHub repo + its owner."""

    owner: str
    repo: str | None       # None if we only saw the owner, not a repo URL
    mention_count: int
    file_count: int


def iter_markdown_files(vault_dir: Path) -> Iterator[Path]:
    """Yield every .md path under the vault, skipping caches/backups."""
    skip_parts = frozenset({"__pycache__", "_backup", ".git", "node_modules"})
    for p in vault_dir.rglob("*.md"):
        if any(part in skip_parts for part in p.parts):
            continue
        yield p


def _iter_relevant_files(
    vault_dir: Path, keywords: tuple[str, ...],
) -> Iterator[tuple[Path, str]]:
    """Walk the vault yielding ``(path, text)`` only for files that
    contain at least one of ``keywords``.

    The keyword pre-filter saves the regex engine from being run
    against the ~90% of vault files that don't mention the platform
    in question — measured: ~30x faster on the full OVP vault.
    Errors reading a file are swallowed so a single bad path can't
    abort the whole scan.
    """
    for path in iter_markdown_files(vault_dir):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not any(kw in text for kw in keywords):
            continue
        yield path, text


def scan_twitter_handles(vault_dir: Path) -> list[HandleMention]:
    """Return handles sorted descending by mention_count."""
    total: Counter[str] = Counter()
    files: dict[str, set[Path]] = {}

    for path, text in _iter_relevant_files(vault_dir, ("x.com", "twitter.com")):
        seen_in_file: set[str] = set()
        for m in _X_HANDLE_RE.finditer(text):
            handle = m.group(1).lstrip("@").lower()
            if not handle or handle in _X_NON_HANDLE:
                continue
            total[handle] += 1
            seen_in_file.add(handle)
        for handle in seen_in_file:
            files.setdefault(handle, set()).add(path)

    return [
        HandleMention(
            handle=h,
            mention_count=c,
            file_count=len(files.get(h, ())),
        )
        for h, c in total.most_common()
    ]


def scan_github_mentions(vault_dir: Path) -> list[GitHubMention]:
    """Return repo + owner mentions sorted descending by mention_count.

    Includes:
      * (owner, repo) entries for every ``github.com/<owner>/<repo>`` URL
      * (owner, None) entries for every bare ``github.com/<owner>``
        profile URL (review-fix: previously missed, so a vault note
        like "Visit https://github.com/karpathy" never triggered a
        backfill of karpathy's github_user entity)
    """
    repo_total: Counter[tuple[str, str]] = Counter()
    repo_files: dict[tuple[str, str], set[Path]] = {}
    owner_only_total: Counter[str] = Counter()
    owner_only_files: dict[str, set[Path]] = {}

    for path, text in _iter_relevant_files(vault_dir, ("github.com",)):
        seen_repos_in_file: set[tuple[str, str]] = set()
        # First pass: owner/repo URLs.
        for m in _GH_REPO_RE.finditer(text):
            owner = m.group(1).lower()
            if owner in _GH_NON_REPO_OWNERS:
                continue
            repo = m.group(2).lower()
            if repo.endswith(".git"):
                repo = repo[:-4]
            repo_total[(owner, repo)] += 1
            seen_repos_in_file.add((owner, repo))
        for key in seen_repos_in_file:
            repo_files.setdefault(key, set()).add(path)

        # Second pass: profile-only URLs (no /<repo> after owner).  We
        # detect them via a separate regex with a tighter terminator
        # (no ``/`` allowed) so they don't overlap the repo regex.
        seen_owners_in_file: set[str] = set()
        for m in _GH_OWNER_RE.finditer(text):
            owner = m.group(1).lower()
            if owner in _GH_NON_REPO_OWNERS:
                continue
            owner_only_total[owner] += 1
            seen_owners_in_file.add(owner)
        for owner in seen_owners_in_file:
            owner_only_files.setdefault(owner, set()).add(path)

    out: list[GitHubMention] = [
        GitHubMention(
            owner=owner,
            repo=repo,
            mention_count=c,
            file_count=len(repo_files.get((owner, repo), ())),
        )
        for (owner, repo), c in repo_total.most_common()
    ]
    # Append owner-only rows.  An owner that only ever appeared via
    # bare profile URLs gets a ``repo=None`` row; if the same owner
    # also appeared with a repo, both rows coexist (consumers can
    # union the counts).
    for owner, c in owner_only_total.most_common():
        out.append(GitHubMention(
            owner=owner,
            repo=None,
            mention_count=c,
            file_count=len(owner_only_files.get(owner, ())),
        ))
    return out
