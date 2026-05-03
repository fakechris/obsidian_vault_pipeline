"""Identity merge — collapse twitter_author + github_user into person.

Why
---

After PR-E1 (twitter_author) and PR-E2 (github_project + github_user)
the entity table has two parallel slices for the same human.  E.g.
@karpathy on Twitter (followers 1.5M, score 0.50) and karpathy on
GitHub (50k followers, score 0.65) are the same person — but the
entity store doesn't know that.

The fix is **another entity type** rather than a pointer column:

  * ``person`` — canonical identity for a human
  * its ``signals_json`` carries a ``links`` array pointing back at
    the platform-specific entities

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
TWITTER_TYPE = "twitter_author"
GITHUB_USER_TYPE = "github_user"

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
    """Upsert a person entity that links the gh + tw entities.

    Returns the freshly-written ``person`` Entity, or ``None`` if either
    side disappeared between discovery and apply (rare race).

    Person identity_key
    -------------------
    We use the **lowercased twitter handle** as the canonical key.
    Reason: Twitter handles are typically more "famous" / oft-quoted
    than the GitHub login (think @karpathy vs karpathy@github), so
    naming the person by their twitter handle keeps human-facing
    surfaces (logs, dashboards) intuitive.
    """
    gh = store.get(GITHUB_USER_TYPE, candidate.github_login)
    tw = store.get(TWITTER_TYPE, candidate.twitter_handle)
    if gh is None or tw is None:
        return None

    person_key = candidate.twitter_handle
    canonical_name = (
        tw.canonical_name or gh.canonical_name or person_key
    )
    derived = max(
        a for a in (gh.derived_authority, tw.derived_authority)
        if a is not None
    )

    signals = {
        "person_canonical_handle": person_key,
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

    return store.upsert(
        entity_type=PERSON_TYPE,
        identity_key=person_key,
        canonical_name=canonical_name,
        signals=signals,
        derived_authority=derived,
        fetch_source="identity_merge",
    )


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
