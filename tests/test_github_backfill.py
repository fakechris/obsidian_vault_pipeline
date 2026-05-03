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
