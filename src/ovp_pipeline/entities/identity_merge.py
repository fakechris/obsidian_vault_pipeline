"""Identity merge — collapse twitter_author + github_user into person/organization.

Why
---

After PR-E1 (twitter_author) and PR-E2 (github_project + github_user)
the entity table has two parallel slices for the same actor.  E.g.
@karpathy on Twitter (followers 1.5M, score 0.50) and karpathy on
GitHub (50k followers, score 0.65) are the same person — but the
entity store doesn't know that.

The fix is **two new entity types** rather than a pointer column:

  * ``person`` — canonical identity for a human
  * ``organization`` — canonical identity for an org (huggingface,
    polymarket, posthog) — split from ``person`` in PR-F1 because
    the semantics differ:
      - persons inherit individual-author authority (followers,
        bio, written works)
      - orgs inherit institutional-author authority (membership,
        endorsements, brand)
    Conflating them mis-files karpathy@x↔karpathy@gh next to
    polymarket@x↔polymarket@gh in the same bucket.

Both types carry the same ``links`` array pointing back at the
platform-specific entities, so downstream code only needs ONE union
read path (see ``resolver.py``).

Three merge sources, in order of confidence:

  1. **GitHub-self-reported Twitter handle**.  ``github_user.signals.
     twitter_username`` is a string the user typed into their own
     GitHub profile — high signal, low effort.  In the OVP vault
     this rule alone produces ~193 auto-merge candidates.
  2. **Exact handle equality** when one platform is empty.  E.g. a
     user with @karpathy@twitter and karpathy@github but neither
     self-reported the other.  Same string, ≥4 chars, both exist
     as entities — merge.
  3. **Manual / fuzzy review**.  Levenshtein distance ≤ 2 (e.g.
     mattpocock ↔ mattpocockuk).  Surfaced for human approval, not
     auto-applied.

This module produces the merge plan; the CLI in
``commands/merge_identities.py`` applies it.

Person authority
----------------

When two slices are merged, the person's derived_authority is the
**max** of its links — never the average.  Reasoning: if karpathy's
github score is 0.65 and twitter score is 0.50, what we know about
him as a Person is at least 0.65 (the strong signal).  Averaging
would punish him for being more famous on one platform than the other.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterator, Literal

from .store import Entity, EntityStore

logger = logging.getLogger(__name__)


# Entity types we emit and consume.
PERSON_TYPE = "person"
ORGANIZATION_TYPE = "organization"
TWITTER_TYPE = "twitter_author"
GITHUB_USER_TYPE = "github_user"

# All canonical-identity entity types that this module produces.
# Resolver lookups iterate this set so adding a future type (e.g.
# ``team`` for github org-within-org) only edits one constant.
CANONICAL_TYPES: tuple[str, ...] = (PERSON_TYPE, ORGANIZATION_TYPE)


def _is_organization(github_user: Entity) -> bool:
    """Decide whether to file a merged actor under organization or person.

    Source of truth: GitHub's own ``type`` field on the user object,
    which is either ``User`` or ``Organization``.  We don't try to
    second-guess via heuristics on the name (``-ai``, ``Inc.``, …) —
    GitHub already classifies, just trust it.
    """
    return (github_user.signals.get("type") or "").lower() == "organization"

# Confidence categories for the report / review queue.
MergeMethod = Literal["self_reported", "exact_handle", "fuzzy"]
_FUZZY_MAX_DISTANCE = 2
_MIN_HANDLE_LEN_FOR_EXACT = 4


@dataclass(frozen=True, slots=True)
class MergeCandidate:
    """One proposed link between a github_user and a twitter_author."""

    github_login: str
    twitter_handle: str
    method: MergeMethod
    confidence: float           # 0-1; auto-applied if ≥ AUTO_THRESHOLD
    rationale: str

    @property
    def is_auto(self) -> bool:
        return self.confidence >= 0.9


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _normalize_handle(s: str | None) -> str:
    """Lowercase + strip @ + trim whitespace.  Empty inputs → ''."""
    if not s:
        return ""
    return s.strip().lstrip("@").lower()


def _levenshtein(a: str, b: str) -> int:
    """Iterative Levenshtein distance.  Small enough for handle pairs."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(
                cur[j - 1] + 1,        # insertion
                prev[j] + 1,           # deletion
                prev[j - 1] + cost,    # substitution
            )
        prev = cur
    return prev[len(b)]


def find_merge_candidates(store: EntityStore) -> list[MergeCandidate]:
    """Scan the entity table and return all proposed links.

    The result mixes auto-mergeable + review candidates; callers
    filter via ``is_auto`` to apply only the high-confidence subset.
    """
    twitter_entities = store.list_by_type(TWITTER_TYPE)
    github_users = store.list_by_type(GITHUB_USER_TYPE)

    twitter_by_handle: dict[str, Entity] = {
        _normalize_handle(e.identity_key): e for e in twitter_entities
    }
    twitter_handles_set: set[str] = set(twitter_by_handle)

    candidates: list[MergeCandidate] = []
    seen_pairs: set[tuple[str, str]] = set()  # (github_login, twitter_handle)

    # --- 1. GitHub self-reported twitter_username -------------------------
    for gh in github_users:
        if gh.derived_authority is None:
            continue   # not_found stub — skip
        tw_self = _normalize_handle(gh.signals.get("twitter_username"))
        if not tw_self:
            continue
        if tw_self not in twitter_by_handle:
            continue
        pair = (gh.identity_key, tw_self)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        candidates.append(MergeCandidate(
            github_login=gh.identity_key,
            twitter_handle=tw_self,
            method="self_reported",
            confidence=0.95,
            rationale=f"github_user.twitter_username='{tw_self}' matches "
                      f"existing twitter_author entity",
        ))

    # --- 2. Exact handle equality -----------------------------------------
    # Only fire when (a) the same string ≥4 chars exists on both platforms
    # AND (b) we didn't already self-report-merge it.
    for gh in github_users:
        if gh.derived_authority is None:
            continue
        gh_login = _normalize_handle(gh.identity_key)
        if len(gh_login) < _MIN_HANDLE_LEN_FOR_EXACT:
            continue
        if gh_login not in twitter_handles_set:
            continue
        pair = (gh.identity_key, gh_login)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        candidates.append(MergeCandidate(
            github_login=gh.identity_key,
            twitter_handle=gh_login,
            method="exact_handle",
            confidence=0.85,    # below the auto threshold by default
            rationale=f"identical handle '{gh_login}' on both platforms",
        ))

    # --- 3. Fuzzy (review queue, never auto) ------------------------------
    # Pairs of handles within Levenshtein 2 — surfaces near-matches like
    # mattpocock ↔ mattpocockuk that a human can approve in one click.
    fuzzy_seen: set[tuple[str, str]] = set()
    for gh in github_users:
        if gh.derived_authority is None:
            continue
        gh_login = _normalize_handle(gh.identity_key)
        if len(gh_login) < _MIN_HANDLE_LEN_FOR_EXACT:
            continue
        for tw_handle in twitter_handles_set:
            if (gh.identity_key, tw_handle) in seen_pairs:
                continue
            if (gh_login, tw_handle) in fuzzy_seen:
                continue
            if abs(len(gh_login) - len(tw_handle)) > _FUZZY_MAX_DISTANCE:
                continue
            d = _levenshtein(gh_login, tw_handle)
            if 0 < d <= _FUZZY_MAX_DISTANCE:
                fuzzy_seen.add((gh_login, tw_handle))
                candidates.append(MergeCandidate(
                    github_login=gh.identity_key,
                    twitter_handle=tw_handle,
                    method="fuzzy",
                    confidence=max(0.0, 0.6 - 0.1 * d),
                    rationale=f"levenshtein({gh_login!r}, {tw_handle!r}) = {d}",
                ))

    return candidates


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_merge(
    store: EntityStore, candidate: MergeCandidate,
) -> Entity | None:
    """Upsert a person OR organization entity linking the gh + tw entities.

    The choice between ``person`` and ``organization`` is driven by
    ``github_user.signals.type`` — GitHub already classifies its
    accounts, and we trust that classification.

    Returns the freshly-written canonical Entity, or ``None`` if either
    side disappeared between discovery and apply (rare race).

    identity_key
    ------------
    We use the **lowercased twitter handle** as the canonical key
    for both types.  Reason: Twitter handles are typically more
    "famous" / oft-quoted than the GitHub login (think @karpathy vs
    karpathy@github, @langchain vs langchain-ai@github), so naming
    the canonical entity by its twitter handle keeps human-facing
    surfaces (logs, dashboards) intuitive.
    """
    gh = store.get(GITHUB_USER_TYPE, candidate.github_login)
    tw = store.get(TWITTER_TYPE, candidate.twitter_handle)
    if gh is None or tw is None:
        return None

    canonical_type = ORGANIZATION_TYPE if _is_organization(gh) else PERSON_TYPE
    canonical_key = candidate.twitter_handle
    canonical_name = (
        tw.canonical_name or gh.canonical_name or canonical_key
    )
    derived = max(
        a for a in (gh.derived_authority, tw.derived_authority)
        if a is not None
    )

    signals = {
        "canonical_handle": canonical_key,
        # Pre-PR-F1 readers expected ``person_canonical_handle``.  Keep
        # both keys until we've audited the downstream consumers.
        "person_canonical_handle": canonical_key,
        "actor_kind": canonical_type,        # "person" | "organization"
        "links": [
            {"entity_type": GITHUB_USER_TYPE,
             "identity_key": gh.identity_key,
             "derived_authority": gh.derived_authority,
             "fetch_source": gh.fetch_source},
            {"entity_type": TWITTER_TYPE,
             "identity_key": tw.identity_key,
             "derived_authority": tw.derived_authority,
             "fetch_source": tw.fetch_source},
        ],
        "merge_method": candidate.method,
        "merge_confidence": candidate.confidence,
        "merge_rationale": candidate.rationale,
        # surface the strongest fields from each side for quick reads
        "twitter_followers": tw.signals.get("followers"),
        "github_followers": gh.signals.get("followers"),
        "github_public_repos": gh.signals.get("public_repos"),
        "company": gh.signals.get("company") or tw.signals.get("location"),
        "bio": gh.signals.get("bio") or tw.signals.get("description"),
    }

    # Migration safety: if a stale ``person`` row exists for an entity
    # we now classify as ``organization`` (or vice versa), delete it
    # so the entity table doesn't carry both.  See migration helper
    # ``reclassify_persons_to_orgs`` for bulk-mode behavior.
    other_type = (
        PERSON_TYPE if canonical_type == ORGANIZATION_TYPE else ORGANIZATION_TYPE
    )
    stale = store.get(other_type, canonical_key)
    if stale is not None:
        store.delete(other_type, canonical_key)

    return store.upsert(
        entity_type=canonical_type,
        identity_key=canonical_key,
        canonical_name=canonical_name,
        signals=signals,
        derived_authority=derived,
        fetch_source="identity_merge",
    )


def _iter_reclassify_targets(store: EntityStore) -> Iterator[Entity]:
    """Yield ``person`` rows whose linked github_user is now classified
    as an organization.  Single source of truth for both the bulk
    migration and any dry-run / preview caller — guarantees the two
    can never disagree.
    """
    for person in store.list_by_type(PERSON_TYPE):
        gh_link = next(
            (ln for ln in (person.signals.get("links") or [])
             if ln.get("entity_type") == GITHUB_USER_TYPE),
            None,
        )
        if gh_link is None:
            continue
        gh = store.get(GITHUB_USER_TYPE, gh_link.get("identity_key", ""))
        if gh is None or not _is_organization(gh):
            continue
        yield person


def reclassify_persons_to_orgs(
    store: EntityStore, *, dry_run: bool = False,
) -> tuple[int, int, list[str]]:
    """One-shot migration: walk existing ``person`` rows and re-file
    those whose linked github_user has ``type == "Organization"``.

    Parameters
    ----------
    dry_run :
        When True, scans + reports without writing.

    Returns
    -------
    ``(reclassified, kept, candidate_handles)``
        ``reclassified`` is the count of rows moved (or that would be
        moved in dry-run); ``kept`` is everything else; ``candidate_handles``
        lists the identity_keys at issue (handy for CLI display).

    Idempotent — running twice is a no-op on the second pass because
    the org-typed rows are no longer ``person``.

    This is the bulk equivalent of ``apply_merge``'s inline cleanup;
    use it once after PR-F1 lands to fix the pre-split person entities
    created by PR-E3.
    """
    candidates: list[Entity] = list(_iter_reclassify_targets(store))
    candidate_handles = [p.identity_key for p in candidates]

    if dry_run:
        kept = len(store.list_by_type(PERSON_TYPE)) - len(candidates)
        return len(candidates), kept, candidate_handles

    for person in candidates:
        # Reclassify: re-upsert under organization with the same
        # signals (plus the new actor_kind + canonical_handle keys
        # so migrated rows match newly-merged rows exactly); delete
        # the old person row.
        new_signals = dict(person.signals)
        new_signals["actor_kind"] = ORGANIZATION_TYPE
        new_signals.setdefault("canonical_handle", person.identity_key)
        store.upsert(
            entity_type=ORGANIZATION_TYPE,
            identity_key=person.identity_key,
            canonical_name=person.canonical_name,
            signals=new_signals,
            derived_authority=person.derived_authority,
            fetch_source="identity_merge",
        )
        store.delete(PERSON_TYPE, person.identity_key)

    kept = len(store.list_by_type(PERSON_TYPE))
    return len(candidates), kept, candidate_handles


def iter_auto_apply(
    store: EntityStore, candidates: list[MergeCandidate],
) -> Iterator[Entity]:
    """Apply only the high-confidence candidates; yield the upserted person rows."""
    for c in candidates:
        if not c.is_auto:
            continue
        person = apply_merge(store, c)
        if person is not None:
            yield person
