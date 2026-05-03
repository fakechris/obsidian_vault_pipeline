"""Pinning tests for the May 2026 entity-layer review fixes.

One file, one test class per review issue, with the issue summary
embedded in the docstring so future readers can trace why each
invariant exists.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from ovp_pipeline.entities.identity_merge import (
    GITHUB_USER_TYPE,
    ORGANIZATION_TYPE,
    PERSON_TYPE,
    TWITTER_TYPE,
    apply_merge,
    find_merge_candidates,
)
from ovp_pipeline.entities.resolver import (
    resolve_github_user_authority,
)
from ovp_pipeline.entities.scan import scan_github_mentions
from ovp_pipeline.entities.store import EntityStore


# ---------------------------------------------------------------------------
# Pass A — read-side write side effects
# ---------------------------------------------------------------------------


class TestStoreReadIsSideEffectFree:
    """Issue: ``EntityStore.__post_init__`` unconditionally called
    ``init_schema``, so any source-signal provider that consulted the
    entity table on a fresh vault would silently materialize a 60-Logs
    directory + empty knowledge.db.  Read paths must be pure reads.
    """

    def test_construction_does_not_create_db(self, tmp_path):
        db = tmp_path / "no.db"
        EntityStore(db_path=db)
        assert not db.exists()

    def test_get_on_missing_db_returns_none(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "no.db")
        assert store.get("twitter_author", "anyone") is None

    def test_list_by_type_on_missing_db_returns_empty(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "no.db")
        assert store.list_by_type("twitter_author") == []

    def test_history_on_missing_db_yields_nothing(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "no.db")
        assert list(store.history(entity_id=1)) == []

    def test_first_write_creates_db(self, tmp_path):
        db = tmp_path / "fresh.db"
        store = EntityStore(db_path=db)
        assert not db.exists()
        store.upsert(
            entity_type="twitter_author", identity_key="x",
            canonical_name="X", signals={}, derived_authority=0.5,
            fetch_source="t",
        )
        assert db.exists()


class TestProviderFastPathDoesNotCreateDB:
    """The same invariant from the provider perspective: scoring a URL
    when the entity table doesn't exist must NOT create it."""

    def test_author_rules_no_op_on_missing_db(self, tmp_path):
        from ovp_pipeline.source_signals.author_rules import AuthorRulesProvider

        # No authors.jsonl, no entity DB — nothing to learn from.
        empty_jsonl = tmp_path / "authors.jsonl"
        empty_jsonl.write_text("", encoding="utf-8")
        missing_db = tmp_path / "knowledge.db"

        provider = AuthorRulesProvider(
            authors_path=empty_jsonl,
            entity_store_path=missing_db,
        )
        sig = provider.score("https://x.com/ghost/status/1", {})
        assert sig is None
        # Critical: no DB was created.
        assert not missing_db.exists()

    def test_github_signal_no_op_on_missing_db(self, tmp_path):
        from ovp_pipeline.source_signals.github import GitHubSignalProvider

        missing_db = tmp_path / "knowledge.db"
        provider = GitHubSignalProvider(entity_store_path=missing_db)
        # Fast path returns None → falls through to live API; mock that
        # so we don't hit GitHub from CI.
        with patch(
            "ovp_pipeline.source_signals.github.urllib.request.urlopen"
        ) as m:
            m.side_effect = Exception("blocked in test")
            try:
                provider.score("https://github.com/owner/repo", {})
            except Exception:
                pass
        # The fast path should not have created the DB even when the
        # live fetch errored.
        assert not missing_db.exists()


# ---------------------------------------------------------------------------
# Pass B — identity merge backlink
# ---------------------------------------------------------------------------


def _seed(store, gh_login, tw_handle, *, gh_type="User",
          gh_signals=None, twitter_username=None,
          gh_authority=0.65, tw_authority=0.5):
    sig = {"type": gh_type}
    if gh_signals:
        sig.update(gh_signals)
    if twitter_username is not None:
        sig["twitter_username"] = twitter_username
    store.upsert(
        entity_type=GITHUB_USER_TYPE, identity_key=gh_login,
        canonical_name=gh_login, signals=sig,
        derived_authority=gh_authority, fetch_source="github_rest",
    )
    store.upsert(
        entity_type=TWITTER_TYPE, identity_key=tw_handle,
        canonical_name=tw_handle, signals={},
        derived_authority=tw_authority, fetch_source="twitterapi.io",
    )


class TestApplyMergeWritesBacklink:
    """Issue: ``apply_merge`` only created the canonical entity but
    didn't write a back-link into github_user.signals.  The resolver
    then could only reach the canonical via
    ``twitter_username`` — which only self_reported merges write.
    exact_handle / fuzzy merges silently bypassed the merged
    authority and returned the bare github score.
    """

    def test_self_reported_writes_canonical_handle_to_github_user(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        _seed(store, "karpathy", "karpathy", twitter_username="karpathy")
        cands = find_merge_candidates(store)
        for c in cands:
            apply_merge(store, c)
        gh = store.get(GITHUB_USER_TYPE, "karpathy")
        assert gh is not None
        assert gh.signals["canonical_handle"] == "karpathy"
        assert gh.signals["canonical_entity_type"] == PERSON_TYPE
        # Same back-link written on the twitter side too.
        tw = store.get(TWITTER_TYPE, "karpathy")
        assert tw is not None
        assert tw.signals["canonical_handle"] == "karpathy"

    def test_exact_handle_merge_writes_backlink(self, tmp_path):
        # The bug: exact_handle merges didn't have twitter_username,
        # so the resolver couldn't reach the canonical entity.  Fix:
        # apply_merge writes the back-link regardless of merge method.
        # Pre-seed a person entity directly so apply_merge has a side
        # to read; it'll re-upsert it via the merge.
        store = EntityStore(db_path=tmp_path / "k.db")
        _seed(store, "simonw", "simonw")     # no twitter_username field
        # Trigger merge.  exact_handle is below auto threshold, so we
        # construct the candidate manually.
        from ovp_pipeline.entities.identity_merge import MergeCandidate
        c = MergeCandidate(
            github_login="simonw", twitter_handle="simonw",
            method="exact_handle", confidence=0.85,
            rationale="test",
        )
        apply_merge(store, c)
        gh = store.get(GITHUB_USER_TYPE, "simonw")
        assert gh is not None
        assert gh.signals.get("canonical_handle") == "simonw"

    def test_organization_backlink_uses_org_type(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        _seed(store, "langchain-ai", "langchain",
              gh_type="Organization",
              twitter_username="langchain")
        for c in find_merge_candidates(store):
            apply_merge(store, c)
        gh = store.get(GITHUB_USER_TYPE, "langchain-ai")
        assert gh is not None
        assert gh.signals["canonical_entity_type"] == ORGANIZATION_TYPE


class TestResolverFollowsBacklink:
    """Issue: ``resolve_github_user_authority`` only used
    ``twitter_username`` to reach the canonical entity, missing
    exact_handle / fuzzy merges.  Fix: also follow the back-link
    written by apply_merge.
    """

    def test_resolver_uses_backlink_for_exact_handle_merge(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        _seed(store, "simonw", "simonw")
        from ovp_pipeline.entities.identity_merge import MergeCandidate
        c = MergeCandidate(
            github_login="simonw", twitter_handle="simonw",
            method="exact_handle", confidence=0.85, rationale="",
        )
        apply_merge(store, c)
        # Resolver should now return the canonical authority (the max
        # of the two sides), not just the bare github score.
        r = resolve_github_user_authority(store, "simonw")
        assert r.source == "person"
        # gh=0.65, tw=0.5 → max=0.65
        assert r.authority == 0.65

    def test_resolver_falls_through_when_no_backlink(self, tmp_path):
        # Pre-fix data (or unmerged actor) — back-link absent.  Must
        # still return the bare github authority, not crash.
        store = EntityStore(db_path=tmp_path / "k.db")
        _seed(store, "loneuser", "loneuser")
        r = resolve_github_user_authority(store, "loneuser")
        assert r.authority == 0.65       # bare github_user score
        assert r.source == "github_user"


# ---------------------------------------------------------------------------
# Pass C — refresh wrapper lock-steal race
# ---------------------------------------------------------------------------


class TestRefreshLockStealRace:
    """Issue: when stealing a stale lock, the second ``os.open`` wasn't
    wrapped in a try/except, so two concurrent refreshes that both
    saw the stale lock would race — one would crash with FileExistsError
    instead of bailing cleanly with the documented "another refresh
    is running" SystemExit.
    """

    def test_lost_steal_race_raises_systemexit_not_filesystem_error(self, tmp_path):
        from ovp_pipeline.commands.refresh_source_authority import (
            _LOCKFILE_NAME,
            _exclusive_lock,
        )

        vault_logs = tmp_path / "60-Logs"
        vault_logs.mkdir(parents=True)
        lock_path = vault_logs / _LOCKFILE_NAME
        # Stale lock points at a dead PID.
        lock_path.write_text("999999", encoding="utf-8")

        with patch(
            "ovp_pipeline.commands.refresh_source_authority._pid_alive",
            return_value=False,
        ):
            # Inject a race: between unlink and second open, another
            # refresh re-creates the file.  We simulate with an
            # os.open patch that fakes the second call.
            real_open = os.open
            calls = {"n": 0}

            def fake_open(path, flags, *args, **kwargs):
                calls["n"] += 1
                if calls["n"] == 2:    # second call (post-unlink)
                    raise FileExistsError("simulated steal race")
                return real_open(path, flags, *args, **kwargs)

            with patch("ovp_pipeline.commands.refresh_source_authority.os.open",
                       side_effect=fake_open):
                with pytest.raises(SystemExit) as exc:
                    with _exclusive_lock(lock_path):
                        pass
                assert "race" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Pass D — schema + semantic fixes
# ---------------------------------------------------------------------------


class TestDeletePreservesHistory:
    """Issue: ``EntityStore.delete`` cascaded through
    ``entity_signals_history``, contradicting the module docstring's
    "append-only time series" contract.  Fix: keep history rows
    (orphaned by entity_id) as a forensic trail.
    """

    def test_history_survives_after_delete(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "k.db")
        store.upsert(
            entity_type=TWITTER_TYPE, identity_key="x",
            canonical_name="X", signals={"v": 1},
            derived_authority=0.5, fetch_source="twitterapi.io",
        )
        e = store.get(TWITTER_TYPE, "x")
        assert e is not None
        store.delete(TWITTER_TYPE, "x")
        # Entity row gone.
        assert store.get(TWITTER_TYPE, "x") is None
        # History row remains.
        rows = list(store.history(e.entity_id))
        assert len(rows) == 1


class TestExcludedHostReturnsTaggedSignal:
    """Issue: ``DomainRulesProvider.score`` returned None for excluded
    hosts, so the audit log never recorded the exclusion.
    overrides.py docstring promised a 0.45 Signal with ``excluded:
    True``.  Fix: return the tagged signal.
    """

    def test_excluded_host_signal_is_tagged(self, tmp_path):
        from ovp_pipeline.source_signals.domain_rules import DomainRulesProvider

        f = tmp_path / "overrides.yaml"
        f.write_text("""
excluded_hosts:
  - localhost
""", encoding="utf-8")
        provider = DomainRulesProvider(overrides_path=f)
        sig = provider.score("http://localhost/foo", {})
        assert sig is not None
        assert sig.value == 0.45
        assert sig.raw["excluded"] is True


class TestScanGithubOwnerOnly:
    """Issue: ``scan_github_mentions`` only matched ``owner/repo``
    URLs, so a profile-only mention like
    ``Visit https://github.com/karpathy`` was invisible to backfill,
    and the karpathy github_user entity was never created from such
    notes.  Fix: a parallel owner-only regex.
    """

    def test_bare_profile_url_is_picked_up(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text(
            "I follow https://github.com/karpathy and also "
            "https://github.com/karpathy/nanoGPT for research.",
            encoding="utf-8",
        )
        result = scan_github_mentions(tmp_path)
        repo_owners = {m.owner for m in result if m.repo is not None}
        owner_only = {m.owner for m in result if m.repo is None}
        # Bare profile triggers an owner-only mention.
        assert "karpathy" in owner_only
        # Repo URL still tracked separately.
        assert "karpathy" in repo_owners

    def test_profile_only_owner_with_no_repo_appears(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("Profile: https://github.com/dotey", encoding="utf-8")
        result = scan_github_mentions(tmp_path)
        owner_only = [m for m in result if m.repo is None]
        assert any(m.owner == "dotey" for m in owner_only)


class TestSourceCoverageEntityResolved:
    """Issue: ``collect_unrecognized_x_handles`` only consulted
    authors.jsonl, so handles already resolvable via the entity
    table (PR-E3) showed up as "unknown" noise in the dashboard.
    Fix: accept ``entity_resolved`` set; treat those as known too.
    """

    def test_entity_resolved_handle_excluded(self, tmp_path):
        from ovp_pipeline.commands.source_coverage import (
            collect_unrecognized_x_handles,
        )

        # Build minimal knowledge.db with one source_authority row.
        import sqlite3
        db_path = tmp_path / "60-Logs" / "knowledge.db"
        db_path.parent.mkdir(parents=True)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("""
                CREATE TABLE source_authority (
                    source_id TEXT PRIMARY KEY,
                    authority REAL NOT NULL,
                    signals_json TEXT NOT NULL,
                    scored_at TEXT NOT NULL,
                    scorer_version TEXT NOT NULL
                )
            """)
            conn.execute(
                "INSERT INTO source_authority VALUES "
                "('https://x.com/karpathy/status/1', 0.7, '[]', '', '')"
            )
            conn.commit()
        finally:
            conn.close()

        # karpathy is NOT in authors.jsonl but IS in the entity-resolved
        # set — must not appear as "unknown".
        unknowns = collect_unrecognized_x_handles(
            tmp_path, known_authors=set(),
            entity_resolved={"karpathy"},
        )
        assert all(h != "karpathy" for h, _ in unknowns)

    def test_entity_resolved_default_preserves_old_behavior(self, tmp_path):
        # Pass entity_resolved=None → identical to pre-fix behavior.
        from ovp_pipeline.commands.source_coverage import (
            collect_unrecognized_x_handles,
        )

        import sqlite3
        db_path = tmp_path / "60-Logs" / "knowledge.db"
        db_path.parent.mkdir(parents=True)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("""
                CREATE TABLE source_authority (
                    source_id TEXT PRIMARY KEY,
                    authority REAL NOT NULL,
                    signals_json TEXT NOT NULL,
                    scored_at TEXT NOT NULL,
                    scorer_version TEXT NOT NULL
                )
            """)
            conn.execute(
                "INSERT INTO source_authority VALUES "
                "('https://x.com/karpathy/status/1', 0.7, '[]', '', '')"
            )
            conn.commit()
        finally:
            conn.close()

        unknowns = collect_unrecognized_x_handles(
            tmp_path, known_authors=set(),
        )
        # Without entity_resolved, karpathy IS unknown.
        assert ("karpathy", 1) in unknowns


class TestScoreSourcesDomainsOnlyHonorsOverrides:
    """Issue: ``--domains-only`` hand-built ``DomainRulesProvider()`` /
    ``AuthorRulesProvider()`` without the overrides paths, so a domain
    upgraded via ``domain_overrides.yaml`` got the unknown-host
    default 0.45 in offline mode.  Fix: reuse default_providers and
    filter to non-network providers.
    """

    def test_domains_only_includes_domain_and_author_rules(self, tmp_path):
        from ovp_pipeline.commands.score_sources import _build_providers

        providers = _build_providers(tmp_path, domains_only=True)
        names = {p.name for p in providers}
        assert "domain_rules" in names
        assert "author_rules" in names
        # Network providers excluded.
        assert "github_stars" not in names
        assert "twitter_engagement" not in names

    def test_domain_rules_carries_overrides_path(self, tmp_path):
        # The domain_rules provider in --domains-only mode must have
        # the overrides_path wired (otherwise yaml-defined domains
        # don't apply).  We can't introspect the field name without
        # depending on internals; check by behavior.
        (tmp_path / "60-Logs").mkdir()
        (tmp_path / "60-Logs" / "domain_overrides.yaml").write_text("""
domains:
  example.com:
    authority: 0.91
    bucket: canonical
""", encoding="utf-8")
        from ovp_pipeline.commands.score_sources import _build_providers

        providers = _build_providers(tmp_path, domains_only=True)
        domain_rules = next(p for p in providers if p.name == "domain_rules")
        sig = domain_rules.score("https://example.com/x", {})
        assert sig is not None
        # The override would have been ignored if we'd hand-built the
        # provider without overrides_path (would have returned 0.45).
        assert sig.value == 0.91
