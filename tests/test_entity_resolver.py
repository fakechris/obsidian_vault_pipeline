"""Tests for entities/resolver.py."""

from __future__ import annotations

from ovp_pipeline.entities.resolver import (
    resolve_github_project_authority,
    resolve_github_user_authority,
    resolve_twitter_authority,
)
from ovp_pipeline.entities.store import EntityStore


def _seed_twitter(store, h, auth):
    store.upsert(
        entity_type="twitter_author", identity_key=h, canonical_name=h,
        signals={}, derived_authority=auth, fetch_source="t",
    )


def _seed_gh_user(store, login, auth, **signals):
    store.upsert(
        entity_type="github_user", identity_key=login, canonical_name=login,
        signals=signals, derived_authority=auth, fetch_source="g",
    )


def _seed_gh_project(store, ident, auth):
    store.upsert(
        entity_type="github_project", identity_key=ident, canonical_name=ident,
        signals={}, derived_authority=auth, fetch_source="g",
    )


def _seed_person(store, h, auth):
    store.upsert(
        entity_type="person", identity_key=h, canonical_name=h,
        signals={"links": []}, derived_authority=auth, fetch_source="m",
    )


def _seed_organization(store, h, auth):
    store.upsert(
        entity_type="organization", identity_key=h, canonical_name=h,
        signals={"links": []}, derived_authority=auth, fetch_source="m",
    )


class TestResolveTwitter:
    def test_falls_back_to_twitter_author(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        _seed_twitter(store, "karpathy", 0.50)
        r = resolve_twitter_authority(store, "karpathy")
        assert r.authority == 0.50
        assert r.source == "twitter_author"

    def test_person_wins_over_twitter(self, tmp_path):
        # When a person entity exists for the handle, it carries the
        # max-of-platforms authority — beat the bare twitter_author.
        store = EntityStore(db_path=tmp_path / "k.db")
        _seed_twitter(store, "karpathy", 0.50)
        _seed_person(store, "karpathy", 0.65)
        r = resolve_twitter_authority(store, "karpathy")
        assert r.authority == 0.65
        assert r.source == "person"

    def test_normalizes_atsign_and_case(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        _seed_twitter(store, "karpathy", 0.50)
        r = resolve_twitter_authority(store, "@KARPATHY")
        assert r.authority == 0.50

    def test_unknown_returns_none(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        r = resolve_twitter_authority(store, "ghost")
        assert r.authority is None
        assert r.source == "none"

    def test_empty_handle_returns_none(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        r = resolve_twitter_authority(store, "")
        assert r.authority is None
        r2 = resolve_twitter_authority(store, "@")
        assert r2.authority is None


class TestResolveGithubProject:
    def test_direct_project_hit(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        _seed_gh_project(store, "karpathy/nanogpt", 0.92)
        r = resolve_github_project_authority(store, "karpathy", "nanoGPT")
        assert r.authority == 0.92
        assert r.source == "github_project"

    def test_owner_fallback_capped(self, tmp_path):
        # No project entity, but owner is well-known.  Falls back but
        # caps at 0.55 — we don't actually know whether THIS particular
        # repo is high-quality.
        store = EntityStore(db_path=tmp_path / "k.db")
        _seed_gh_user(store, "karpathy", 0.65)
        r = resolve_github_project_authority(store, "karpathy", "experiments")
        assert r.authority == 0.55
        assert r.source == "github_user"

    def test_unknown_owner_unknown_repo(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        r = resolve_github_project_authority(store, "ghost", "missing")
        assert r.authority is None


class TestResolveGithubUser:
    def test_direct_hit(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        _seed_gh_user(store, "karpathy", 0.65)
        r = resolve_github_user_authority(store, "karpathy")
        assert r.authority == 0.65
        assert r.source == "github_user"

    def test_person_via_self_reported_twitter(self, tmp_path):
        # github_user has twitter_username="karpathy", and a person
        # entity exists keyed by that twitter handle — return the
        # person's higher score.
        store = EntityStore(db_path=tmp_path / "k.db")
        _seed_gh_user(store, "karpathy", 0.65, twitter_username="karpathy")
        _seed_person(store, "karpathy", 0.75)
        r = resolve_github_user_authority(store, "karpathy")
        assert r.authority == 0.75
        assert r.source == "person"


# ---------------------------------------------------------------------------
# PR-F1: organization resolution
# ---------------------------------------------------------------------------


class TestOrganizationResolution:
    def test_twitter_resolves_to_organization(self, tmp_path):
        # langchain is filed as ``organization`` (PR-F1) — twitter
        # resolution must surface that, not silently miss.
        store = EntityStore(db_path=tmp_path / "k.db")
        _seed_organization(store, "langchain", 0.57)
        r = resolve_twitter_authority(store, "langchain")
        assert r.authority == 0.57
        assert r.source == "organization"

    def test_github_user_via_org_canonical(self, tmp_path):
        # github_user.langchain-ai has twitter_username="langchain";
        # the organization entity for "langchain" should win over
        # the bare github_user authority.
        store = EntityStore(db_path=tmp_path / "k.db")
        _seed_gh_user(store, "langchain-ai", 0.55, twitter_username="langchain")
        _seed_organization(store, "langchain", 0.65)
        r = resolve_github_user_authority(store, "langchain-ai")
        assert r.authority == 0.65
        assert r.source == "organization"

    def test_person_and_org_dont_collide_on_same_handle(self, tmp_path):
        # In practice the same identity_key won't exist as both — the
        # apply_merge migration cleans up — but the resolver should
        # behave deterministically if a stale row sneaks through.
        # _CANONICAL_TYPES iterates person before organization, so
        # person wins (matches the migration's expected end state
        # where the stale row is the org one).
        store = EntityStore(db_path=tmp_path / "k.db")
        _seed_person(store, "ambiguous", 0.5)
        _seed_organization(store, "ambiguous", 0.7)
        r = resolve_twitter_authority(store, "ambiguous")
        assert r.source == "person"
        assert r.authority == 0.5
