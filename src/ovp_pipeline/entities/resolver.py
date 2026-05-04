"""Entity-table resolver — single read path for source-signal providers.

Use case: ``AuthorRulesProvider.score()`` falls through when an X handle
isn't in ``authors.jsonl``.  Before PR-E3 that meant a hardcoded 0.45
default (via the orchestrator).  After PR-E3 the fallback is "look up
the entity table" — using the partial author_weight that PR-E1 collected.

The resolver also resolves person-merged identities: a handle that has
been merged with another platform's entity returns the **higher**
authority.  E.g. @karpathy on Twitter scores 0.50, but PR-E2 found him
on GitHub at 0.65, so the merged person scores 0.65.

Public surface kept tiny on purpose — only what callers need:

  * ``resolve_twitter_authority(store, handle)``
  * ``resolve_github_project_authority(store, owner, repo)``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .store import Entity, EntityStore


# Source-of-truth labels we pass back so callers can record where the
# authority came from in the audit log.
ResolveSource = Literal[
    "person", "organization", "twitter_author",
    "github_user", "github_project", "none",
]


# Canonical-identity types in lookup-priority order.  Both share
# the same ``identity_key`` namespace (lowercased twitter handle),
# so a given handle can only resolve to one of them.
_CANONICAL_TYPES: tuple[ResolveSource, ...] = ("person", "organization")


@dataclass(frozen=True, slots=True)
class ResolveResult:
    """What the resolver knows about one platform handle.

    ``authority`` is None when the entity isn't in the table at all
    (or only exists as a not_found stub).  Callers should treat this
    like ``Signal == None`` and let the fallback logic apply.
    """

    authority: float | None
    source: ResolveSource
    entity: Entity | None


def _normalize(s: str | None) -> str:
    return (s or "").strip().lstrip("@").lower()


def resolve_twitter_authority(
    store: EntityStore, handle: str,
) -> ResolveResult:
    """Look up a Twitter handle's authority via person → twitter_author."""
    norm = _normalize(handle)
    if not norm:
        return ResolveResult(None, "none", None)

    # 1. Canonical-identity entity (person or organization) keyed by
    #    twitter handle.  PR-F1 split person → person + organization;
    #    a given handle is only in one bucket so the loop returns on
    #    first hit.
    for canonical_type in _CANONICAL_TYPES:
        canonical = store.get(canonical_type, norm)
        if canonical is not None and canonical.derived_authority is not None:
            return ResolveResult(
                canonical.derived_authority, canonical_type, canonical,
            )

    # 2. Plain twitter_author entity from PR-E1.
    tw = store.get("twitter_author", norm)
    if tw is not None and tw.derived_authority is not None:
        return ResolveResult(tw.derived_authority, "twitter_author", tw)

    return ResolveResult(None, "none", None)


def resolve_github_project_authority(
    store: EntityStore, owner: str, repo: str,
) -> ResolveResult:
    """Look up a GitHub project's authority by ``owner/repo``.

    Falls back to the owner's authority (capped) if the project is
    missing — covers fresh repos we haven't backfilled yet but whose
    owner is well-known.
    """
    owner_n = _normalize(owner)
    repo_n = _normalize(repo)
    if not owner_n or not repo_n:
        return ResolveResult(None, "none", None)

    proj = store.get("github_project", f"{owner_n}/{repo_n}")
    if proj is not None and proj.derived_authority is not None:
        return ResolveResult(proj.derived_authority, "github_project", proj)

    user = store.get("github_user", owner_n)
    if user is not None and user.derived_authority is not None:
        # Owner-only fallback gets dampened — we don't actually know
        # whether THIS particular repo is high quality, only that the
        # owner usually ships good things.  Cap at 0.55 so an
        # explicit project entity always wins on re-fetch.
        return ResolveResult(
            min(user.derived_authority, 0.55),
            "github_user", user,
        )

    return ResolveResult(None, "none", None)


def resolve_github_user_authority(
    store: EntityStore, login: str,
) -> ResolveResult:
    """Look up a GitHub user's authority — used by author_rules when
    the source URL is e.g. ``github.com/karpathy/dotfiles`` and we
    want to credit Karpathy's reputation, not the dotfiles repo's
    star count."""
    norm = _normalize(login)
    if not norm:
        return ResolveResult(None, "none", None)

    # Canonical-identity merge takes precedence here too — if karpathy
    # was merged to a person entity, or langchain-ai was merged to an
    # organization entity, we want that view.
    #
    # Two ways to reach the canonical entity:
    #
    #   (a) The back-link written by ``apply_merge`` — works for ALL
    #       merge methods (self_reported / exact_handle / fuzzy).
    #   (b) Falling back to ``github_user.signals.twitter_username`` —
    #       works only when the user self-reported their Twitter
    #       handle.  Kept as a belt-and-suspenders for pre-fix data
    #       that was migrated before the back-link writes shipped.
    gh = store.get("github_user", norm)
    if gh is not None and gh.derived_authority is not None:
        backlink = _resolve_via_backlink(store, gh)
        if backlink is not None:
            return backlink

        tw_username = _normalize(gh.signals.get("twitter_username"))
        if tw_username:
            for canonical_type in _CANONICAL_TYPES:
                canonical = store.get(canonical_type, tw_username)
                if (canonical is not None
                        and canonical.derived_authority is not None):
                    return ResolveResult(
                        canonical.derived_authority, canonical_type, canonical,
                    )
        return ResolveResult(gh.derived_authority, "github_user", gh)

    return ResolveResult(None, "none", None)


def _resolve_via_backlink(
    store: EntityStore, side: Entity,
) -> ResolveResult | None:
    """If ``side.signals`` carries a ``canonical_handle`` back-link
    (written by ``identity_merge.apply_merge``), load that canonical
    entity and return its authority.

    Returns ``None`` when the back-link is missing or the linked
    entity can't be loaded (e.g. deleted between writes).
    """
    canonical_type = side.signals.get("canonical_entity_type")
    canonical_key = _normalize(side.signals.get("canonical_handle"))
    if canonical_type not in _CANONICAL_TYPES or not canonical_key:
        return None
    canonical = store.get(canonical_type, canonical_key)
    if canonical is None or canonical.derived_authority is None:
        return None
    return ResolveResult(
        canonical.derived_authority, canonical_type, canonical,
    )
