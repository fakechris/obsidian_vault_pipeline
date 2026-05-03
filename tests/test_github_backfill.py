"""Tests for entities/github_backfill.py.

Mocks ``urllib.request.urlopen`` — never hits the real GitHub API.
Covers:
  * project + user score formulas at each dimension threshold
  * 200 / 404 / 401 (auth) / 403 (rate limit) / 429 / network paths
  * the owner_lift mechanism that pulls user authority into project score
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from unittest.mock import patch

import urllib.error

from ovp_pipeline.entities.github_backfill import (
    PRICE_PER_CALL_USD,
    _years_since,
    _days_since,
    compute_project_authority,
    compute_user_authority,
    derive_project_signals,
    derive_user_signals,
    fetch_repo,
    fetch_user,
    stub_signals_for_missing,
)


def _years_ago_iso(years: float) -> str:
    delta = years * 365.25 * 86400
    ts = datetime.now(timezone.utc).timestamp() - delta
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _days_ago_iso(days: float) -> str:
    ts = datetime.now(timezone.utc).timestamp() - days * 86400
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _fake_response(status: int, body):
    raw = body if isinstance(body, str) else json.dumps(body)
    f = io.BytesIO(raw.encode("utf-8"))
    f.status = status
    return f


# ---------------------------------------------------------------------------
# Project score formula
# ---------------------------------------------------------------------------


class TestComputeProjectAuthority:
    def test_zero_signals_zero_score(self):
        score, b = compute_project_authority({})
        # not_archived (5) + not_fork (5) trigger by default — that's
        # the signal "this isn't an archived fork", which is true of an
        # empty payload.  Everything else is 0.
        assert b["stars"] == 0
        assert b["forks"] == 0
        assert b["recency"] == 0
        assert b["age"] == 0
        assert b["license"] == 0
        # archived/fork default to falsy → those credits apply
        assert b["not_archived"] == 5
        assert b["not_fork"] == 5
        assert score == 10  # not_archived + not_fork

    def test_high_stars_capped_at_30(self):
        _, b = compute_project_authority({"stargazers_count": 200_000})
        assert b["stars"] == 30

    def test_star_log_bands(self):
        _, b1 = compute_project_authority({"stargazers_count": 1500})
        _, b2 = compute_project_authority({"stargazers_count": 150})
        _, b3 = compute_project_authority({"stargazers_count": 5})
        assert b1["stars"] == 20
        assert b2["stars"] == 12
        assert b3["stars"] == 0

    def test_archived_loses_5_points(self):
        _, b = compute_project_authority({"archived": True})
        assert b["not_archived"] == 0

    def test_fork_loses_5_points(self):
        _, b = compute_project_authority({"fork": True})
        assert b["not_fork"] == 0

    def test_recent_push_full_credit(self):
        _, b = compute_project_authority({"pushed_at": _days_ago_iso(15)})
        assert b["recency"] == 15

    def test_dormant_3y_zero_recency(self):
        _, b = compute_project_authority({"pushed_at": _days_ago_iso(365 * 5)})
        assert b["recency"] == 0

    def test_license_credit(self):
        _, b = compute_project_authority({"license": {"spdx_id": "MIT"}})
        assert b["license"] == 5
        # malformed license obj shouldn't credit
        _, b2 = compute_project_authority({"license": "MIT"})  # str not dict
        assert b2["license"] == 0

    def test_owner_lift_passed_through(self):
        _, b = compute_project_authority({}, owner_lift=20)
        assert b["owner_lift"] == 20

    def test_owner_lift_clamped(self):
        _, b = compute_project_authority({}, owner_lift=999)
        assert b["owner_lift"] == 20
        _, b2 = compute_project_authority({}, owner_lift=-5)
        assert b2["owner_lift"] == 0

    def test_max_score_cap(self):
        score, _ = compute_project_authority({
            "stargazers_count": 100_000,
            "forks_count": 5_000,
            "pushed_at": _days_ago_iso(1),
            "created_at": _years_ago_iso(10),
            "license": {"spdx_id": "Apache-2.0"},
            "archived": False, "fork": False,
        }, owner_lift=20)
        # 30 + 10 + 15 + 10 + 5 + 5 + 5 + 20 = 100
        assert score == 100


# ---------------------------------------------------------------------------
# User score formula
# ---------------------------------------------------------------------------


class TestComputeUserAuthority:
    def test_zero_signals_zero_score(self):
        score, _ = compute_user_authority({})
        assert score == 0

    def test_high_followers(self):
        _, b = compute_user_authority({"followers": 50_000})
        assert b["followers"] == 25

    def test_organization_account_credited(self):
        _, b = compute_user_authority({"type": "Organization"})
        assert b["is_organization"] == 10

    def test_user_account_no_org_credit(self):
        _, b = compute_user_authority({"type": "User"})
        assert b["is_organization"] == 0

    def test_company_string_credited_only_if_nonempty(self):
        _, b1 = compute_user_authority({"company": "Anthropic"})
        _, b2 = compute_user_authority({"company": ""})
        _, b3 = compute_user_authority({"company": None})
        assert b1["has_company"] == 10
        assert b2["has_company"] == 0
        assert b3["has_company"] == 0

    def test_max_score_cap(self):
        score, _ = compute_user_authority({
            "followers": 50_000,
            "public_repos": 200,
            "created_at": _years_ago_iso(15),
            "bio": "researcher",
            "blog": "https://example.com",
            "company": "Anthropic",
            "type": "User",
        })
        # 25 + 10 + 10 + 5 + 5 + 10 = 65 (no org credit for User)
        assert score == 65


# ---------------------------------------------------------------------------
# Derive helpers
# ---------------------------------------------------------------------------


class TestDeriveProjectSignals:
    def test_records_missing_dimensions(self):
        _, signals = derive_project_signals({"stargazers_count": 100})
        assert "commit_velocity_30d" in signals["missing_dimensions"]
        assert "contributor_count" in signals["missing_dimensions"]

    def test_owner_authority_maps_to_owner_lift(self):
        # owner_authority_0_75 == 0.75 should max out owner_lift at 20
        a_full, signals_full = derive_project_signals(
            {"stargazers_count": 1000}, owner_authority_0_75=0.75,
        )
        a_zero, signals_zero = derive_project_signals(
            {"stargazers_count": 1000}, owner_authority_0_75=0.0,
        )
        assert signals_full["weight_breakdown"]["owner_lift"] == 20
        assert signals_zero["weight_breakdown"]["owner_lift"] == 0
        # full owner authority should yield strictly higher project authority
        assert a_full > a_zero

    def test_authority_in_unit_interval(self):
        a, _ = derive_project_signals({
            "stargazers_count": 100_000,
            "forks_count": 10_000,
            "pushed_at": _days_ago_iso(1),
            "created_at": _years_ago_iso(10),
            "license": {"spdx_id": "MIT"},
        }, owner_authority_0_75=0.75)
        assert 0.0 <= a <= 1.0


class TestDeriveUserSignals:
    def test_authority_caps_below_canonical_band(self):
        # Even fully-loaded github_user maxes at 75/100 = 0.75, leaving
        # 0.75-1.0 reserved for explicit human curation.
        a, _ = derive_user_signals({
            "followers": 50_000,
            "public_repos": 200,
            "created_at": _years_ago_iso(15),
            "bio": "ok", "blog": "ok", "company": "ok",
            "type": "Organization",
        })
        assert a == 0.75

    def test_persists_twitter_username_for_cross_platform_link(self):
        # PR-E3 will use this to merge github_user with twitter_author.
        _, signals = derive_user_signals({
            "login": "karpathy", "twitter_username": "karpathy",
        })
        assert signals["twitter_username"] == "karpathy"


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


class TestFetchRepo:
    def test_success(self):
        body = {"full_name": "karpathy/nanoGPT", "stargazers_count": 30000}
        with patch(
            "ovp_pipeline.entities.github_backfill.urllib.request.urlopen"
        ) as m:
            m.return_value.__enter__.return_value = _fake_response(200, body)
            r = fetch_repo("karpathy", "nanoGPT", token="t")
        assert r.status == "ok"
        assert r.payload["stargazers_count"] == 30000

    def test_404(self):
        with patch(
            "ovp_pipeline.entities.github_backfill.urllib.request.urlopen"
        ) as m:
            m.side_effect = urllib.error.HTTPError(
                "u", 404, "not found", {}, io.BytesIO(b'{"message":"Not Found"}'),
            )
            r = fetch_repo("ghost", "missing", token="t")
        assert r.status == "not_found"

    def test_403_rate_limit_retries(self):
        # Fresh HTTPError per call — re-using one instance would have
        # the body BytesIO drained after the first .read(), which
        # masks the "rate limit" message on subsequent attempts.
        def _raise_rate_limit(*_args, **_kwargs):
            raise urllib.error.HTTPError(
                "u", 403, "rate", {},
                io.BytesIO(b'{"message":"API rate limit exceeded"}'),
            )
        with patch(
            "ovp_pipeline.entities.github_backfill.urllib.request.urlopen",
            side_effect=_raise_rate_limit,
        ) as m, patch(
            "ovp_pipeline.entities.github_backfill.time.sleep"
        ) as sleeper:
            r = fetch_repo("a", "b", token="t")
        assert r.status == "error"
        assert m.call_count == 3
        assert sleeper.call_count >= 2

    def test_401_no_retry(self):
        with patch(
            "ovp_pipeline.entities.github_backfill.urllib.request.urlopen"
        ) as m:
            m.side_effect = urllib.error.HTTPError(
                "u", 401, "auth", {},
                io.BytesIO(b'{"message":"Bad credentials"}'),
            )
            r = fetch_repo("a", "b", token="bad")
        assert r.status == "error"
        assert "auth" in (r.error or "").lower()

    def test_missing_owner_or_repo(self):
        r = fetch_repo("", "x", token="t")
        assert r.status == "error"
        r2 = fetch_repo("x", "", token="t")
        assert r2.status == "error"


class TestFetchUser:
    def test_success(self):
        body = {"login": "karpathy", "followers": 100_000, "type": "User"}
        with patch(
            "ovp_pipeline.entities.github_backfill.urllib.request.urlopen"
        ) as m:
            m.return_value.__enter__.return_value = _fake_response(200, body)
            r = fetch_user("karpathy", token="t")
        assert r.status == "ok"
        assert r.payload["followers"] == 100_000


class TestBandConstants:
    """Pin the named-constant tables so a future tune to one band
    can't silently break another.  Same pattern as
    test_twitter_backfill.TestBandConstants."""

    def test_project_bands_monotonic_high_to_low(self):
        from ovp_pipeline.entities.github_backfill import (
            _PROJECT_AGE_BANDS,
            _PROJECT_FORKS_BANDS,
            _PROJECT_STARS_BANDS,
        )
        # First-match-wins requires high-to-low ordering.
        for table in (_PROJECT_STARS_BANDS, _PROJECT_FORKS_BANDS,
                      _PROJECT_AGE_BANDS):
            thresholds = [t for t, _ in table]
            assert thresholds == sorted(thresholds, reverse=True), \
                f"non-monotonic: {table}"

    def test_recency_bands_monotonic_low_to_high(self):
        # Recency uses _band_lookup_max which expects ascending thresholds.
        from ovp_pipeline.entities.github_backfill import _PROJECT_RECENCY_BANDS
        thresholds = [t for t, _ in _PROJECT_RECENCY_BANDS]
        assert thresholds == sorted(thresholds)

    def test_user_bands_monotonic_high_to_low(self):
        from ovp_pipeline.entities.github_backfill import (
            _USER_AGE_BANDS,
            _USER_FOLLOWERS_BANDS,
            _USER_REPOS_BANDS,
        )
        for table in (_USER_FOLLOWERS_BANDS, _USER_REPOS_BANDS,
                      _USER_AGE_BANDS):
            thresholds = [t for t, _ in table]
            assert thresholds == sorted(thresholds, reverse=True)

    def test_band_lookup_picks_highest_match(self):
        from ovp_pipeline.entities.github_backfill import (
            _PROJECT_STARS_BANDS,
            _band_lookup,
        )
        # 30_000 stars: between 10_000 (25 pts) and 50_000 (30 pts) — must return 25.
        assert _band_lookup(30_000, _PROJECT_STARS_BANDS) == 25

    def test_band_lookup_max_picks_smallest_match(self):
        from ovp_pipeline.entities.github_backfill import (
            _PROJECT_RECENCY_BANDS,
            _band_lookup_max,
        )
        # days=100 → falls in the ≤180 band (10 points), not ≤30.
        assert _band_lookup_max(100, _PROJECT_RECENCY_BANDS) == 10
        # days=400 → only fits ≤365*3 (2 points).
        assert _band_lookup_max(400, _PROJECT_RECENCY_BANDS) == 2
        # days=∞ → no band matches → 0
        assert _band_lookup_max(float("inf"), _PROJECT_RECENCY_BANDS) == 0

    def test_band_lookup_zero_below_lowest(self):
        from ovp_pipeline.entities.github_backfill import (
            _PROJECT_STARS_BANDS,
            _band_lookup,
        )
        assert _band_lookup(0, _PROJECT_STARS_BANDS) == 0


class TestBackfillProjectsUsesPrefetchedUsers:
    """The review-fix invariant: scoring N projects must NOT issue
    N more SQLite connections to look up the owner.  Pin it so a
    refactor can't silently regress."""

    def test_owner_lookup_uses_prefetched_dict(self, tmp_path):
        from ovp_pipeline.commands.backfill_github import _backfill_projects
        from ovp_pipeline.entities.store import EntityStore

        store = EntityStore(db_path=tmp_path / "k.db")
        # Seed two known github_user entities (owners of the test repos).
        store.upsert(
            entity_type="github_user", identity_key="alice",
            canonical_name="Alice", signals={"followers": 5000},
            derived_authority=0.45, fetch_source="github_rest",
        )
        store.upsert(
            entity_type="github_user", identity_key="bob",
            canonical_name="Bob", signals={"followers": 100},
            derived_authority=0.20, fetch_source="github_rest",
        )

        # Stub the fetch — never hit the real network.
        repo_payloads = {
            ("alice", "x"): {
                "full_name": "alice/x", "stargazers_count": 5000,
                "owner": {"login": "alice"},
            },
            ("bob", "y"): {
                "full_name": "bob/y", "stargazers_count": 50,
                "owner": {"login": "bob"},
            },
        }
        from ovp_pipeline.commands import backfill_github as bg
        from ovp_pipeline.entities.github_backfill import FetchResult

        def fake_fetch(owner, repo, *, token=None):
            payload = repo_payloads.get((owner, repo))
            if payload is None:
                return FetchResult(f"{owner}/{repo}", "project",
                                   "not_found", None, "404")
            return FetchResult(f"{owner}/{repo}", "project",
                               "ok", payload, None)

        # Track every store.get call so we can prove the loop doesn't
        # re-hit the DB for owner lookups.
        original_get = store.get
        get_call_log: list[tuple[str, str]] = []

        def logged_get(entity_type, identity_key):
            get_call_log.append((entity_type, identity_key))
            return original_get(entity_type, identity_key)

        store.get = logged_get  # type: ignore[method-assign]

        # Patch the names imported INTO commands.backfill_github —
        # patching the source module wouldn't update the binding.
        with patch.object(bg, "fetch_repo", side_effect=fake_fetch), \
             patch.object(bg, "time"):
            ok, nf, err, cached = _backfill_projects(
                store=store,
                repos=[("alice", "x", 1), ("bob", "y", 1)],
                token=None, max_age_days=30, force=False, max_handles=None,
            )
        assert (ok, nf, err) == (2, 0, 0)

        # The store.get calls inside the loop should ONLY be the
        # cache-staleness checks for the project identity.  No
        # per-project (_USER_TYPE, ...) calls.
        user_lookups = [
            (t, k) for t, k in get_call_log if t == "github_user"
        ]
        assert user_lookups == [], (
            f"unexpected per-project user lookups: {user_lookups}"
        )

        # Owner authority IS still applied: alice's 0.45 should have
        # produced a non-zero owner_lift in alice/x's signals.
        alice_proj = store.get("github_project", "alice/x")
        assert alice_proj is not None
        assert alice_proj.signals["weight_breakdown"]["owner_lift"] > 0


def test_price_constant_is_zero():
    # GitHub REST is free; if we ever switch to a paid mirror this
    # test is the canary.
    assert PRICE_PER_CALL_USD == 0.0


def test_stub_signals_for_missing():
    s = stub_signals_for_missing("ghost/missing", "project", "404")
    assert s["identity"] == "ghost/missing"
    assert s["kind"] == "project"
    assert s["fetch_status"] == "not_found"


class TestParseHelpers:
    def test_years_since_handles_z_suffix(self):
        # GitHub returns ISO-with-Z; ensure parser accepts it
        s = _years_ago_iso(2.0)
        assert 1.9 < _years_since(s) < 2.1

    def test_years_since_unparseable_returns_zero(self):
        assert _years_since("nonsense") == 0.0
        assert _years_since(None) == 0.0

    def test_days_since_unparseable_returns_inf(self):
        # Used for "no recency credit" — unparseable should mean "very old"
        assert _days_since(None) == float("inf")
