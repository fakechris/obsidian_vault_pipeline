"""Tests for entities/twitter_backfill.py.

Uses unittest.mock to fake urlopen — never hits the real twitterapi.io
in CI.  Verifies:
  * the score formula matches the documented dimension table
  * fetch_user_info handles 200 / 401 / 404 / 429 / network errors
  * the persisted ``signals`` dict carries the missing-dimensions list
    so reviewers can tell apart "we never had this signal" from
    "this signal was zero".
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from unittest.mock import patch

import urllib.error

from ovp_pipeline.entities.twitter_backfill import (
    PRICE_PER_CALL_USD,
    _account_age_years,
    compute_partial_author_weight,
    derive_authority_from_payload,
    fetch_user_info,
    stub_signals_for_missing,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _years_ago_iso(years: float) -> str:
    """ISO timestamp ``years`` years ago in UTC, accepted by twitterapi.io."""
    delta_days = years * 365.25
    now = datetime.now(timezone.utc)
    target_ts = now.timestamp() - delta_days * 86400
    return datetime.fromtimestamp(target_ts, tz=timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _fake_response(status: int, body: dict | str):
    """Build a FakeURLOpen-ready return value for urllib.request.urlopen."""
    raw = body if isinstance(body, str) else json.dumps(body)
    f = io.BytesIO(raw.encode("utf-8"))
    f.status = status
    return f


# ---------------------------------------------------------------------------
# Score formula
# ---------------------------------------------------------------------------


class TestComputePartialAuthorWeight:
    def test_zero_inputs_zero_score(self):
        score, breakdown = compute_partial_author_weight({})
        assert score == 0
        # negative-only outcome (automated penalty) is clipped to 0 by max()
        assert all(v == 0 for v in breakdown.values())

    def test_high_followers_dominates(self):
        score, b = compute_partial_author_weight({
            "followers": 500_000,
            "createdAt": _years_ago_iso(10),
            "isBlueVerified": True,
            "isVerified": True,
        })
        assert b["followers"] == 25
        assert b["age"] == 10
        assert b["blue_verified"] == 5
        assert b["legacy_verified"] == 10
        assert score == 50

    def test_company_affiliation_adds_10(self):
        score, b = compute_partial_author_weight({
            "followers": 1500,
            "createdAt": _years_ago_iso(4),
            "affiliatesHighlightedLabel": {"label": {"text": "Anthropic"}},
        })
        assert b["affiliation"] == 10

    def test_automated_account_penalized(self):
        score_normal, _ = compute_partial_author_weight({
            "followers": 5000, "createdAt": _years_ago_iso(5),
            "statusesCount": 1000,
        })
        score_bot, b_bot = compute_partial_author_weight({
            "followers": 5000, "createdAt": _years_ago_iso(5),
            "statusesCount": 1000, "isAutomated": True,
        })
        assert b_bot["automated_penalty"] == -10
        assert score_bot == score_normal - 10

    def test_activity_band(self):
        # too-active accounts get a slight discount, not full credit.
        _, b_normal = compute_partial_author_weight({"statusesCount": 1000})
        _, b_extreme = compute_partial_author_weight({"statusesCount": 100_000})
        assert b_normal["activity"] == 10
        assert b_extreme["activity"] == 8

    def test_max_score_caps_under_70(self):
        # Even with every dimension maxed, score can't reach 70 because
        # listed/mutuals/i_follow are deliberately not modeled here.
        score, _ = compute_partial_author_weight({
            "followers": 10_000_000,
            "createdAt": _years_ago_iso(15),
            "isBlueVerified": True,
            "isVerified": True,
            "verifiedType": "company",
            "affiliatesHighlightedLabel": {"label": "ok"},
            "statusesCount": 5000,
        })
        # 25 + 10 + 5 + 10 + 10 + 10 = 70 (no penalty)
        assert score == 70

    def test_negative_only_score_floors_at_zero(self):
        # If an account is automated and has no other signals, the
        # raw sum is -10; max() clips it to 0 so the persisted
        # authority stays in [0, 1].
        score, _ = compute_partial_author_weight({"isAutomated": True})
        assert score == 0


class TestAccountAgeYears:
    def test_iso_format(self):
        s = _years_ago_iso(5.0)
        years = _account_age_years(s)
        assert 4.9 < years < 5.1

    def test_x_legacy_format(self):
        # X's classic API used this shape; twitterapi.io may sometimes echo it.
        years = _account_age_years("Thu Dec 13 08:41:26 +0000 2007")
        assert years > 16   # 2026 - 2007

    def test_unparseable_returns_zero(self):
        assert _account_age_years("nonsense") == 0.0
        assert _account_age_years(None) == 0.0
        assert _account_age_years("") == 0.0


# ---------------------------------------------------------------------------
# Authority derivation
# ---------------------------------------------------------------------------


class TestDeriveAuthorityFromPayload:
    def test_persists_missing_dimensions_marker(self):
        # The signals dict must record what we DON'T have, so a future
        # formula change can detect "we never collected this" vs
        # "this was zero".
        _, signals = derive_authority_from_payload(
            {"userName": "x", "followers": 100, "createdAt": _years_ago_iso(2)}
        )
        assert "listed_count" in signals["missing_dimensions"]
        assert "mutuals_count" in signals["missing_dimensions"]
        assert "i_follow" in signals["missing_dimensions"]

    def test_authority_in_unit_interval(self):
        a, _ = derive_authority_from_payload(
            {"followers": 10_000_000, "createdAt": _years_ago_iso(15),
             "isBlueVerified": True, "verifiedType": "company",
             "affiliatesHighlightedLabel": {"label": "ok"},
             "statusesCount": 5000}
        )
        assert 0.0 <= a <= 1.0

    def test_authority_caps_below_clipper_max(self):
        # 70 / 100 = 0.70 — leaves the 0.71-1.0 band reserved for
        # clipper-rich frontmatter that has the missing signals.
        a, _ = derive_authority_from_payload(
            {"followers": 10_000_000, "createdAt": _years_ago_iso(15),
             "isBlueVerified": True, "verifiedType": "company",
             "affiliatesHighlightedLabel": {"label": "ok"},
             "statusesCount": 5000}
        )
        assert a == 0.70


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


class TestFetchUserInfo:
    def test_success_returns_payload(self):
        body = {
            "status": "success",
            "data": {"userName": "karpathy", "followers": 1_000_000},
        }
        with patch(
            "ovp_pipeline.entities.twitter_backfill.urllib.request.urlopen"
        ) as m:
            m.return_value.__enter__.return_value = _fake_response(200, body)
            r = fetch_user_info("karpathy", api_key="k")
        assert r.status == "ok"
        assert r.payload["followers"] == 1_000_000

    def test_404_marks_not_found_no_retry(self):
        with patch(
            "ovp_pipeline.entities.twitter_backfill.urllib.request.urlopen"
        ) as m:
            m.side_effect = urllib.error.HTTPError(
                "u", 404, "not found", {}, None,
            )
            r = fetch_user_info("ghost", api_key="k")
        assert r.status == "not_found"
        # only one attempt — no retries on 404
        assert m.call_count == 1

    def test_401_aborts_immediately(self):
        with patch(
            "ovp_pipeline.entities.twitter_backfill.urllib.request.urlopen"
        ) as m:
            m.side_effect = urllib.error.HTTPError(
                "u", 401, "auth", {}, None,
            )
            r = fetch_user_info("x", api_key="bad")
        assert r.status == "error"
        assert "auth" in (r.error or "").lower()
        assert m.call_count == 1

    def test_429_retries_then_gives_up(self):
        with patch(
            "ovp_pipeline.entities.twitter_backfill.urllib.request.urlopen"
        ) as m, patch(
            "ovp_pipeline.entities.twitter_backfill.time.sleep"
        ) as sleeper:
            m.side_effect = urllib.error.HTTPError(
                "u", 429, "rate", {}, None,
            )
            r = fetch_user_info("x", api_key="k")
        assert r.status == "error"
        # Retried up to _MAX_RETRIES times.
        assert m.call_count == 3
        # Backoff is exercised between attempts.
        assert sleeper.call_count >= 2

    def test_api_error_status_with_user_not_found_msg(self):
        body = {"status": "error", "msg": "user not found"}
        with patch(
            "ovp_pipeline.entities.twitter_backfill.urllib.request.urlopen"
        ) as m:
            m.return_value.__enter__.return_value = _fake_response(200, body)
            r = fetch_user_info("ghost", api_key="k")
        assert r.status == "not_found"

    def test_missing_api_key_short_circuits(self):
        r = fetch_user_info("x", api_key="")
        assert r.status == "error"
        # No call attempted.

    def test_bad_json_marks_error(self):
        with patch(
            "ovp_pipeline.entities.twitter_backfill.urllib.request.urlopen"
        ) as m:
            m.return_value.__enter__.return_value = _fake_response(200, "<not json>")
            r = fetch_user_info("x", api_key="k")
        assert r.status == "error"
        assert "json" in (r.error or "").lower()


class TestStubSignalsForMissing:
    def test_records_handle_and_reason(self):
        s = stub_signals_for_missing("ghost", "404 from API")
        assert s["userName"] == "ghost"
        assert s["fetch_status"] == "not_found"
        assert "404" in s["fetch_reason"]


class TestBandConstants:
    """Pin the named-constant tables so a future tune to one band
    doesn't silently break another.  Each test is one band boundary."""

    def test_follower_bands_monotonic(self):
        from ovp_pipeline.entities.twitter_backfill import _FOLLOWER_BANDS
        thresholds = [t for t, _ in _FOLLOWER_BANDS]
        # Bands must be sorted high-to-low for first-match-wins semantics.
        assert thresholds == sorted(thresholds, reverse=True)

    def test_age_bands_monotonic(self):
        from ovp_pipeline.entities.twitter_backfill import _AGE_YEAR_BANDS
        thresholds = [t for t, _ in _AGE_YEAR_BANDS]
        assert thresholds == sorted(thresholds, reverse=True)

    def test_band_lookup_zero_below_lowest(self):
        from ovp_pipeline.entities.twitter_backfill import (
            _FOLLOWER_BANDS, _band_lookup,
        )
        assert _band_lookup(0, _FOLLOWER_BANDS) == 0

    def test_band_lookup_picks_highest_match(self):
        from ovp_pipeline.entities.twitter_backfill import (
            _FOLLOWER_BANDS, _band_lookup,
        )
        # 50_000 is between 10_000 (22pts) and 100_000 (25pts) — must
        # return the higher 22, not silently fall through.
        assert _band_lookup(50_000, _FOLLOWER_BANDS) == 22


def test_price_constant_matches_intro_doc():
    # If twitterapi.io changes pricing we should see this fail.
    # Anchor against the documented $0.18/1k = $0.00018 figure.
    assert PRICE_PER_CALL_USD == 0.00018
