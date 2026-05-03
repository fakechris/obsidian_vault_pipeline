"""Entity layer — first-class Author/Project records that signals attach to.

Until PR-E1 every author/project signal lived only in the per-clipping
frontmatter.  That worked when each clipping carried full data, but
breaks down when:

  * the same handle appears in 50 clippings (denormalized waste)
  * the clipping was captured before the new adapter (no data at all)
  * we want to accumulate signal across multiple captures of the same
    author (e.g. "we've bookmarked this person 12 times")

This package introduces an ``entities`` table indexed by
``(entity_type, identity_key)`` that holds the merged view.  Authority
scoring will (in PR-E3) read from here instead of frontmatter.

Entity types currently supported:
  * ``twitter_author``  — populated by ovp-backfill-twitter-authors
  * (future) ``github_project`` and ``github_user`` in PR-E2
  * (future) ``wechat_mp``, ``substack_publication`` in later PRs
"""

from .store import (
    Entity,
    EntityStore,
    init_schema,
)

__all__ = [
    "Entity",
    "EntityStore",
    "init_schema",
]
