"""Tests for entities/identity_merge.py."""

from __future__ import annotations

from ovp_pipeline.entities.identity_merge import (
    GITHUB_USER_TYPE,
    MergeCandidate,
    ORGANIZATION_TYPE,
    PERSON_TYPE,
    TWITTER_TYPE,
    apply_merge,
    find_merge_candidates,
    reclassify_persons_to_orgs,
    _levenshtein,
)
from ovp_pipeline.entities.store import EntityStore


def _make_tw(store, handle, **kw):
    return store.upsert(
        entity_type="twitter_author", identity_key=handle,
        canonical_name=kw.pop("name", handle),
        signals=kw.pop("signals", {}),
        derived_authority=kw.pop("authority", 0.5),
        fetch_source="twitterapi.io",
    )


def _make_gh(store, login, **kw):
    return store.upsert(
        entity_type="github_user", identity_key=login,
        canonical_name=kw.pop("name", login),
        signals=kw.pop("signals", {}),
        derived_authority=kw.pop("authority", 0.5),
        fetch_source="github_rest",
    )


class TestLevenshtein:
    def test_identical(self):
        assert _levenshtein("abc", "abc") == 0

    def test_one_substitution(self):
        assert _levenshtein("abc", "abd") == 1

    def test_one_insertion(self):
        assert _levenshtein("abc", "abxc") == 1

    def test_empty_either_side(self):
        assert _levenshtein("", "abc") == 3
        assert _levenshtein("abc", "") == 3

    def test_real_world_pair(self):
        # The mattpocock case from the PR-E2 cross-link report.
        assert _levenshtein("mattpocock", "mattpocockuk") == 2


class TestSelfReportedMerge:
    def test_picks_up_self_reported_handle(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        _make_tw(store, "karpathy", authority=0.50)
        _make_gh(store, "karpathy",
                 signals={"twitter_username": "karpathy"},
                 authority=0.65)
        cands = find_merge_candidates(store)
        self_rep = [c for c in cands if c.method == "self_reported"]
        assert len(self_rep) == 1
        assert self_rep[0].github_login == "karpathy"
        assert self_rep[0].twitter_handle == "karpathy"
        assert self_rep[0].is_auto

    def test_self_report_normalizes_atsign_and_case(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        _make_tw(store, "karpathy", authority=0.50)
        _make_gh(store, "kp",
                 signals={"twitter_username": "@KARPATHY"},
                 authority=0.65)
        cands = find_merge_candidates(store)
        self_rep = [c for c in cands if c.method == "self_reported"]
        assert len(self_rep) == 1
        assert self_rep[0].twitter_handle == "karpathy"

    def test_self_report_skipped_when_twitter_entity_missing(self, tmp_path):
        # GitHub user reports a Twitter handle we never backfilled —
        # nothing to link, no candidate emitted.
        store = EntityStore(db_path=tmp_path / "k.db")
        _make_gh(store, "ghost",
                 signals={"twitter_username": "neverseen"},
                 authority=0.4)
        cands = find_merge_candidates(store)
        assert cands == []


class TestExactHandle:
    def test_emits_review_candidate(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        _make_tw(store, "simonw", authority=0.50)
        _make_gh(store, "simonw", authority=0.65)   # no twitter_username field
        cands = find_merge_candidates(store)
        exact = [c for c in cands if c.method == "exact_handle"]
        assert len(exact) == 1
        # Exact-handle is below the auto threshold by default
        assert not exact[0].is_auto

    def test_exact_skips_short_handles(self, tmp_path):
        # "abc" exists on both sides but is too short to call a same-person.
        store = EntityStore(db_path=tmp_path / "k.db")
        _make_tw(store, "abc", authority=0.5)
        _make_gh(store, "abc", authority=0.5)
        cands = find_merge_candidates(store)
        assert all(c.method != "exact_handle" for c in cands)

    def test_exact_skipped_when_self_report_already_matches(self, tmp_path):
        # github_user reports the same twitter handle — only the
        # self_reported entry should fire, not also exact_handle.
        store = EntityStore(db_path=tmp_path / "k.db")
        _make_tw(store, "simonw", authority=0.5)
        _make_gh(store, "simonw",
                 signals={"twitter_username": "simonw"},
                 authority=0.5)
        cands = find_merge_candidates(store)
        # No duplicate emission for the same (github, twitter) pair.
        pairs = {(c.github_login, c.twitter_handle) for c in cands}
        assert len(pairs) == len(cands)


class TestFuzzy:
    def test_emits_low_confidence_review(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        _make_tw(store, "mattpocockuk", authority=0.4)
        _make_gh(store, "mattpocock", authority=0.5)
        cands = find_merge_candidates(store)
        fuzzy = [c for c in cands if c.method == "fuzzy"]
        assert len(fuzzy) == 1
        # Fuzzy candidates must NOT be auto-applied
        assert not fuzzy[0].is_auto

    def test_fuzzy_distance_cap(self, tmp_path):
        # Levenshtein distance of 3 is past the cap — must not fire.
        store = EntityStore(db_path=tmp_path / "k.db")
        _make_tw(store, "alice123", authority=0.5)
        _make_gh(store, "bob", authority=0.5)   # length difference alone is 5
        cands = find_merge_candidates(store)
        assert not any(c.method == "fuzzy" for c in cands)


class TestNotFoundStubsExcluded:
    def test_stub_entities_not_merged(self, tmp_path):
        # not_found stubs (derived_authority=None) must never appear
        # in merge candidates — there's no signal to merge.
        store = EntityStore(db_path=tmp_path / "k.db")
        _make_tw(store, "ghost", authority=None)
        _make_gh(store, "ghost", authority=None,
                 signals={"twitter_username": "ghost"})
        cands = find_merge_candidates(store)
        assert cands == []


class TestApplyMerge:
    def test_creates_person_entity(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        _make_tw(store, "karpathy", authority=0.50,
                 signals={"followers": 1_500_000})
        _make_gh(store, "karpathy", authority=0.65,
                 signals={"twitter_username": "karpathy",
                          "followers": 60_000,
                          "bio": "AI researcher"})
        cands = find_merge_candidates(store)
        applied = [apply_merge(store, c) for c in cands if c.is_auto]
        assert len(applied) == 1

        person = store.get(PERSON_TYPE, "karpathy")
        assert person is not None
        # Authority is the MAX of the two sides, not the average
        assert person.derived_authority == 0.65
        # Both platforms are linked
        link_types = {ln["entity_type"] for ln in person.signals["links"]}
        assert link_types == {"twitter_author", "github_user"}
        # Surfaced fields from both sides
        assert person.signals["twitter_followers"] == 1_500_000
        assert person.signals["github_followers"] == 60_000
        assert person.signals["bio"] == "AI researcher"

    def test_apply_is_idempotent(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        _make_tw(store, "x", authority=0.4)
        _make_gh(store, "x", authority=0.5,
                 signals={"twitter_username": "x"})
        cands = find_merge_candidates(store)
        for c in cands:
            apply_merge(store, c)
            apply_merge(store, c)   # second call must not error or duplicate
        assert len(store.list_by_type(PERSON_TYPE)) == 1

    def test_apply_returns_none_when_side_disappears(self, tmp_path):
        # Race: candidate built from a snapshot, then one side gets
        # deleted before apply runs.  Should return None, not crash.
        store = EntityStore(db_path=tmp_path / "k.db")
        c = MergeCandidate(
            github_login="ghost", twitter_handle="ghost",
            method="self_reported", confidence=0.95, rationale="",
        )
        # Neither entity exists.
        assert apply_merge(store, c) is None


# ---------------------------------------------------------------------------
# PR-F1: person/organization split
# ---------------------------------------------------------------------------


class TestOrganizationSplit:
    def test_org_typed_github_user_creates_organization(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        _make_tw(store, "langchain", authority=0.55,
                 signals={"followers": 248_000})
        _make_gh(
            store, "langchain-ai", authority=0.65,
            signals={"twitter_username": "langchain",
                     "type": "Organization", "followers": 18_700},
        )
        cands = find_merge_candidates(store)
        applied = [apply_merge(store, c) for c in cands if c.is_auto]
        assert len(applied) == 1
        # Should be an organization, NOT a person.
        org = store.get(ORGANIZATION_TYPE, "langchain")
        person = store.get(PERSON_TYPE, "langchain")
        assert org is not None
        assert person is None
        assert org.signals["actor_kind"] == ORGANIZATION_TYPE

    def test_user_typed_github_user_still_creates_person(self, tmp_path):
        # PR-F1 must not break the person path — User-typed accounts
        # still file under person.
        store = EntityStore(db_path=tmp_path / "k.db")
        _make_tw(store, "karpathy", authority=0.50)
        _make_gh(store, "karpathy", authority=0.65,
                 signals={"twitter_username": "karpathy", "type": "User"})
        cands = find_merge_candidates(store)
        applied = [apply_merge(store, c) for c in cands if c.is_auto]
        assert len(applied) == 1
        person = store.get(PERSON_TYPE, "karpathy")
        org = store.get(ORGANIZATION_TYPE, "karpathy")
        assert person is not None
        assert org is None
        assert person.signals["actor_kind"] == PERSON_TYPE

    def test_missing_type_field_defaults_to_person(self, tmp_path):
        # Backward-compat: pre-PR-F1 github_user entities don't have
        # a ``type`` field in signals.  Default is person, not org.
        store = EntityStore(db_path=tmp_path / "k.db")
        _make_tw(store, "x", authority=0.4)
        _make_gh(store, "x", authority=0.5,
                 signals={"twitter_username": "x"})  # no type
        cands = find_merge_candidates(store)
        for c in cands:
            apply_merge(store, c)
        assert store.get(PERSON_TYPE, "x") is not None
        assert store.get(ORGANIZATION_TYPE, "x") is None

    def test_apply_reclassifies_existing_person_as_org(self, tmp_path):
        # Simulates a PR-E3-era person row that should be reclassified
        # when this PR's apply_merge runs.  The old row must be
        # deleted, not duplicated.
        store = EntityStore(db_path=tmp_path / "k.db")
        _make_tw(store, "polymarket", authority=0.57,
                 signals={"followers": 1_467_000})
        _make_gh(store, "polymarket", authority=0.55,
                 signals={"twitter_username": "polymarket",
                          "type": "Organization"})
        # Pre-seed a stale person entity (as if from PR-E3).
        store.upsert(
            entity_type=PERSON_TYPE, identity_key="polymarket",
            canonical_name="Polymarket", signals={"stale": True},
            derived_authority=0.57, fetch_source="identity_merge",
        )
        cands = find_merge_candidates(store)
        for c in cands:
            apply_merge(store, c)
        # Now organization, no person.
        assert store.get(ORGANIZATION_TYPE, "polymarket") is not None
        assert store.get(PERSON_TYPE, "polymarket") is None


class TestReclassifyMigration:
    def test_bulk_migrates_orgs_only(self, tmp_path):
        # Three pre-existing person rows: two are actually orgs, one is
        # a real person.  reclassify_persons_to_orgs should move only
        # the two org-typed ones.
        store = EntityStore(db_path=tmp_path / "k.db")

        # Org #1 — langchain-ai on github
        _make_gh(store, "langchain-ai", authority=0.65,
                 signals={"twitter_username": "langchain",
                          "type": "Organization"})
        store.upsert(
            entity_type=PERSON_TYPE, identity_key="langchain",
            canonical_name="LangChain", signals={
                "links": [{"entity_type": GITHUB_USER_TYPE,
                           "identity_key": "langchain-ai"}],
            }, derived_authority=0.57, fetch_source="identity_merge",
        )
        # Org #2 — posthog
        _make_gh(store, "posthog", authority=0.65,
                 signals={"twitter_username": "posthog",
                          "type": "Organization"})
        store.upsert(
            entity_type=PERSON_TYPE, identity_key="posthog",
            canonical_name="PostHog", signals={
                "links": [{"entity_type": GITHUB_USER_TYPE,
                           "identity_key": "posthog"}],
            }, derived_authority=0.56, fetch_source="identity_merge",
        )
        # Real person — karpathy
        _make_gh(store, "karpathy", authority=0.65,
                 signals={"twitter_username": "karpathy", "type": "User"})
        store.upsert(
            entity_type=PERSON_TYPE, identity_key="karpathy",
            canonical_name="Andrej Karpathy", signals={
                "links": [{"entity_type": GITHUB_USER_TYPE,
                           "identity_key": "karpathy"}],
            }, derived_authority=0.65, fetch_source="identity_merge",
        )

        reclassified, kept = reclassify_persons_to_orgs(store)
        assert reclassified == 2
        assert kept == 1
        # Orgs are now organization rows; person rows for those gone.
        assert store.get(ORGANIZATION_TYPE, "langchain") is not None
        assert store.get(PERSON_TYPE, "langchain") is None
        assert store.get(ORGANIZATION_TYPE, "posthog") is not None
        assert store.get(PERSON_TYPE, "posthog") is None
        # Karpathy stays a person.
        assert store.get(PERSON_TYPE, "karpathy") is not None
        assert store.get(ORGANIZATION_TYPE, "karpathy") is None

    def test_idempotent(self, tmp_path):
        # Second run should be a no-op.
        store = EntityStore(db_path=tmp_path / "k.db")
        _make_gh(store, "langchain-ai", authority=0.65,
                 signals={"type": "Organization"})
        store.upsert(
            entity_type=PERSON_TYPE, identity_key="langchain",
            canonical_name="LangChain", signals={
                "links": [{"entity_type": GITHUB_USER_TYPE,
                           "identity_key": "langchain-ai"}],
            }, derived_authority=0.57, fetch_source="identity_merge",
        )
        first = reclassify_persons_to_orgs(store)
        second = reclassify_persons_to_orgs(store)
        assert first == (1, 0)
        assert second == (0, 0)

    def test_skips_when_github_link_missing(self, tmp_path):
        # If a person row's links don't include a github_user (perhaps
        # a future merge source like substack-only), we can't tell —
        # leave it alone.
        store = EntityStore(db_path=tmp_path / "k.db")
        store.upsert(
            entity_type=PERSON_TYPE, identity_key="orphan",
            canonical_name="Orphan", signals={"links": []},
            derived_authority=0.5, fetch_source="identity_merge",
        )
        reclassified, kept = reclassify_persons_to_orgs(store)
        assert reclassified == 0
        assert kept == 1
        assert store.get(PERSON_TYPE, "orphan") is not None


class TestStoreDelete:
    def test_delete_removes_row_and_history(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        _make_tw(store, "ghost", authority=0.4)
        e = store.get(TWITTER_TYPE, "ghost")
        assert e is not None
        ok = store.delete(TWITTER_TYPE, "ghost")
        assert ok is True
        assert store.get(TWITTER_TYPE, "ghost") is None
        # History rows for that entity_id are gone too.
        rows = list(store.history(e.entity_id))
        assert rows == []

    def test_delete_nonexistent_returns_false(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        assert store.delete(TWITTER_TYPE, "never-existed") is False
