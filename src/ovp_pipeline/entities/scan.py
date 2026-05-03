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


# X/Twitter handle inside a status URL or a bare profile URL.
# We accept both ``x.com/<handle>`` and ``x.com/<handle>/status/<id>``
# because the question is "does this handle appear" not "did they
# write a tweet we ingested".
_X_HANDLE_RE = re.compile(
    r"(?:x|twitter)\.com/(@?[A-Za-z0-9_]+)(?:/status/\d+)?",
    re.IGNORECASE,
)

# GitHub owner/repo from a URL.  Stops at the first ``/?#`` after the
# repo segment so we don't pick up sub-paths like ``/issues/12`` as
# part of the repo name.
_GH_REPO_RE = re.compile(
    # The repo segment ends at any URL-terminating character: path
    # separators, query/fragment markers, closing brackets/parens
    # (markdown links!), whitespace, quotes, or end-of-string.
    r"github\.com/([\w.-]+)/([\w.-]+?)(?=[/?#)\]\s\"'`>]|$)",
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


def scan_twitter_handles(vault_dir: Path) -> list[HandleMention]:
    """Return handles sorted descending by mention_count."""
    total: Counter[str] = Counter()
    files: dict[str, set[Path]] = {}

    for path in iter_markdown_files(vault_dir):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Cheap pre-filter — saves regex work on the >90% of files
        # that don't reference Twitter at all.
        if "x.com" not in text and "twitter.com" not in text:
            continue
        seen_in_file: set[str] = set()
        for m in _X_HANDLE_RE.finditer(text):
            handle = m.group(1).lstrip("@").lower()
            if handle in _X_NON_HANDLE:
                continue
            if not handle:
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
    """Return repo+owner mentions sorted descending by mention_count.

    A ``repo=None`` row means "we only saw owner-level URLs"; a row
    with a repo also exists for the same owner if both kinds appear.
    """
    repo_total: Counter[tuple[str, str]] = Counter()
    repo_files: dict[tuple[str, str], set[Path]] = {}
    owner_total: Counter[str] = Counter()
    owner_files: dict[str, set[Path]] = {}

    for path in iter_markdown_files(vault_dir):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "github.com" not in text:
            continue
        seen_repos_in_file: set[tuple[str, str]] = set()
        seen_owners_in_file: set[str] = set()
        for m in _GH_REPO_RE.finditer(text):
            owner = m.group(1).lower()
            if owner in _GH_NON_REPO_OWNERS:
                continue
            repo = m.group(2).lower()
            if repo.endswith(".git"):
                repo = repo[:-4]
            repo_total[(owner, repo)] += 1
            owner_total[owner] += 1
            seen_repos_in_file.add((owner, repo))
            seen_owners_in_file.add(owner)
        for key in seen_repos_in_file:
            repo_files.setdefault(key, set()).add(path)
        for owner in seen_owners_in_file:
            owner_files.setdefault(owner, set()).add(path)

    out: list[GitHubMention] = []
    for (owner, repo), c in repo_total.most_common():
        out.append(GitHubMention(
            owner=owner,
            repo=repo,
            mention_count=c,
            file_count=len(repo_files.get((owner, repo), ())),
        ))
    return out
