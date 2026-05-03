"""GitHub REST API → entities (github_project + github_user).

Two endpoints, two entity types:

  * ``GET /repos/{owner}/{repo}``  → ``github_project``
  * ``GET /users/{username}``      → ``github_user``

The existing ``source_signals/github.py`` (T2 SignalProvider) computes
a single 0-1 score from /repos for ingest-time use.  This module is
broader: it persists the full payload + a multi-dimension authority
score for use across the entity layer.  The two will converge in
PR-E3 when SignalProvider starts reading from the entity table; for
now they coexist.

Auth & rate limit
-----------------

GitHub's REST API is **free** but rate-limited.  Unauthenticated:
60 req/hour per IP.  With a personal access token (no scopes
required for public-repo reads): 5000 req/hour.

For the OVP vault (~281 unique repos+owners) we need a PAT — the
unauth limit would force a 5-hour stagger.  Pass the token via
``--token``, env ``GITHUB_TOKEN``, or ``60-Logs/.github-token``.

Score formulas
--------------

``github_project`` (max 100 → derived_authority ≤ 1.0):
    stars        max 30  (logarithmic bands)
    forks        max 10  (log bands)
    recency      max 15  (active <30d full credit, decays to 0 at 3y)
    age          max 10  (mature wins, but cap so brand-new can score)
    has_license  max  5
    not_archived max  5  (archived projects lose this)
    not_a_fork   max  5  (originals weighted over mirrors)
    owner_lift   max 20  (filled in once github_user is known)

``github_user`` (max 75 → derived_authority ≤ 0.75):
    followers       max 25
    public_repos    max 10
    age             max 10
    has_bio         max  5
    has_blog        max  5
    has_company     max 10  (affiliation signal — same logic as Twitter)
    is_organization max 10  (Org accounts are aggregator-of-record)

The 0.75 ceiling on github_user reserves the 0.75-1.0 band for
explicit human curation (anthropic, openai etc. via
``domain_overrides.yaml``) — same pattern as the twitter backfill's
0.70 ceiling.

Failure handling
----------------

  * 401/403   → if rate limit, surface clearly; if auth, abort
  * 404       → entity stub (deleted/renamed), no retry
  * 429/secondary rate limit → exponential backoff
  * network   → retry up to 3 times then mark error
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

logger = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"
_USER_AGENT = "ovp-backfill/1.0 (https://github.com/fakechris/obsidian_vault_pipeline)"
_DEFAULT_TIMEOUT_S = 15.0
_MAX_RETRIES = 3
_BACKOFF_BASE_S = 1.5

# GitHub REST is free; we track no monetary cost, only rate-limit budget.
PRICE_PER_CALL_USD = 0.0


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Outcome of a single GitHub REST call."""

    identity: str           # 'owner/repo' for projects, 'login' for users
    kind: Literal["project", "user"]
    status: str             # "ok" | "not_found" | "error"
    payload: dict[str, Any] | None
    error: str | None


def _request(
    path: str, *, token: str | None, timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> tuple[int, dict[str, Any] | str]:
    """Single GET, returns (status_code, parsed_or_raw)."""
    url = f"{_API_BASE}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": _USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8")
        try:
            return 200, json.loads(body)
        except json.JSONDecodeError:
            return 200, body
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 - best-effort error decoding
            err_body = {"message": str(e)}
        return e.code, err_body
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return -1, {"message": str(e)}


def _retryable_fetch(
    path: str, *, token: str | None, identity: str, kind: str,
) -> FetchResult:
    last_err = "unknown"
    for attempt in range(_MAX_RETRIES):
        code, body = _request(path, token=token)
        if code == 200 and isinstance(body, dict):
            return FetchResult(identity, kind, "ok", body, None)
        if code == 404:
            msg = (body or {}).get("message", "not found") if isinstance(body, dict) else "not found"
            return FetchResult(identity, kind, "not_found", None, msg)
        if code in (401, 403):
            # Distinguish auth failure from rate limiting by message body.
            msg = (body or {}).get("message", "") if isinstance(body, dict) else ""
            if "rate limit" in msg.lower() or "abuse" in msg.lower():
                wait = _BACKOFF_BASE_S * (2 ** attempt)
                logger.info("rate limited on %s, sleeping %.1fs", identity, wait)
                time.sleep(wait)
                last_err = f"HTTP {code} (rate limit)"
                continue
            return FetchResult(
                identity, kind, "error", None,
                f"auth failed: HTTP {code} ({msg or 'check token'})",
            )
        if code == 429:
            wait = _BACKOFF_BASE_S * (2 ** attempt)
            time.sleep(wait)
            last_err = "HTTP 429"
            continue
        # transport / 5xx
        last_err = f"HTTP {code}: {body}" if code > 0 else f"network: {body}"
        time.sleep(_BACKOFF_BASE_S * (2 ** attempt))
    return FetchResult(identity, kind, "error", None, last_err)


def fetch_repo(
    owner: str, repo: str, *, token: str | None = None,
) -> FetchResult:
    if not owner or not repo:
        return FetchResult(f"{owner}/{repo}", "project", "error", None,
                           "missing owner or repo")
    return _retryable_fetch(
        f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}",
        token=token, identity=f"{owner}/{repo}", kind="project",
    )


def fetch_user(
    username: str, *, token: str | None = None,
) -> FetchResult:
    if not username:
        return FetchResult("", "user", "error", None, "missing username")
    return _retryable_fetch(
        f"/users/{urllib.parse.quote(username)}",
        token=token, identity=username, kind="user",
    )


# ---------------------------------------------------------------------------
# Score formula constants
# ---------------------------------------------------------------------------
# Each band table is ``(threshold, points)`` pairs evaluated top-to-bottom;
# first matching threshold wins.  Tuples (vs. inline elif chains) make the
# bands trivial to inspect, tune, and unit-test.

_PROJECT_STARS_BANDS: tuple[tuple[int, int], ...] = (
    (50_000, 30),
    (10_000, 25),
    (1_000,  20),
    (100,    12),
    (10,      5),
)

_PROJECT_FORKS_BANDS: tuple[tuple[int, int], ...] = (
    (1_000, 10),
    (100,    7),
    (10,     3),
)

# (max_days_since_push, points) — first-match wins; days are non-negative.
_PROJECT_RECENCY_BANDS: tuple[tuple[float, int], ...] = (
    (30,         15),
    (180,        10),
    (365,         5),
    (365 * 3,     2),
)

_PROJECT_AGE_BANDS: tuple[tuple[float, int], ...] = (
    (5.0, 10),
    (2.0,  6),
    (1.0,  3),
)

_PROJECT_LICENSE_POINTS = 5
_PROJECT_NOT_ARCHIVED_POINTS = 5
_PROJECT_NOT_FORK_POINTS = 5
_PROJECT_OWNER_LIFT_MAX = 20

_USER_FOLLOWERS_BANDS: tuple[tuple[int, int], ...] = (
    (10_000, 25),
    (1_000,  20),
    (100,    12),
    (10,      5),
)

_USER_REPOS_BANDS: tuple[tuple[int, int], ...] = (
    (100, 10),
    (30,   7),
    (5,    3),
)

_USER_AGE_BANDS: tuple[tuple[float, int], ...] = (
    (7.0, 10),
    (3.0,  6),
    (1.0,  3),
)

_USER_BIO_POINTS = 5
_USER_BLOG_POINTS = 5
_USER_COMPANY_POINTS = 10
_USER_ORGANIZATION_POINTS = 10

_PROJECT_AUTHORITY_DENOMINATOR = 100.0
_USER_AUTHORITY_DENOMINATOR = 100.0


def _band_lookup(value: float, bands: tuple[tuple[float, int], ...]) -> int:
    """Walk ``bands`` top-to-bottom; return the first band whose
    threshold ``value`` meets or exceeds.  Returns 0 if no band matches.
    """
    for threshold, points in bands:
        if value >= threshold:
            return points
    return 0


def _band_lookup_max(value: float, bands: tuple[tuple[float, int], ...]) -> int:
    """Like ``_band_lookup`` but for "≤ threshold" semantics — used by
    the recency table where the score is determined by *how recent*,
    not "at least N points of activity".
    """
    for threshold, points in bands:
        if value <= threshold:
            return points
    return 0


# ---------------------------------------------------------------------------
# Score derivation
# ---------------------------------------------------------------------------


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _years_since(ts: str | None) -> float:
    dt = _parse_iso(ts)
    if dt is None:
        return 0.0
    return max((datetime.now(timezone.utc) - dt).days / 365.25, 0.0)


def _days_since(ts: str | None) -> float:
    dt = _parse_iso(ts)
    if dt is None:
        return float("inf")
    return max((datetime.now(timezone.utc) - dt).days, 0.0)


def compute_project_authority(
    payload: dict[str, Any],
    *,
    owner_lift: int = 0,
) -> tuple[int, dict[str, int]]:
    """Score one repo (0-100).

    ``owner_lift`` is the github_user's contribution (0-20).  Pass 0
    when the user hasn't been fetched yet — the project still gets
    a useful score from its own intrinsic signals.
    """
    breakdown: dict[str, int] = {}

    # 1. stars — log10-style bands so 100k-star repos don't crowd out
    # 1k-star repos that are still meaningful.
    breakdown["stars"] = _band_lookup(
        int(payload.get("stargazers_count") or 0), _PROJECT_STARS_BANDS,
    )

    # 2. forks
    breakdown["forks"] = _band_lookup(
        int(payload.get("forks_count") or 0), _PROJECT_FORKS_BANDS,
    )

    # 3. recency — last push window (smaller-is-better, hence _band_lookup_max)
    pushed_at = payload.get("pushed_at") or payload.get("updated_at")
    breakdown["recency"] = _band_lookup_max(_days_since(pushed_at), _PROJECT_RECENCY_BANDS)

    # 4. age — mature repos earn trust
    breakdown["age"] = _band_lookup(
        _years_since(payload.get("created_at")), _PROJECT_AGE_BANDS,
    )

    # 5. license — legitimate OSS signal
    license_obj = payload.get("license")
    breakdown["license"] = (
        _PROJECT_LICENSE_POINTS
        if isinstance(license_obj, dict) and license_obj.get("spdx_id")
        else 0
    )

    # 6. not archived
    breakdown["not_archived"] = (
        0 if payload.get("archived") else _PROJECT_NOT_ARCHIVED_POINTS
    )

    # 7. not a fork — originals over mirrors
    breakdown["not_fork"] = 0 if payload.get("fork") else _PROJECT_NOT_FORK_POINTS

    # 8. owner lift — provided by caller after fetching the github_user
    breakdown["owner_lift"] = max(0, min(_PROJECT_OWNER_LIFT_MAX, owner_lift))

    score = max(0, sum(breakdown.values()))
    return score, breakdown


def compute_user_authority(payload: dict[str, Any]) -> tuple[int, dict[str, int]]:
    """Score one GitHub user / org (0-75)."""
    breakdown: dict[str, int] = {}

    # 1. followers
    breakdown["followers"] = _band_lookup(
        int(payload.get("followers") or 0), _USER_FOLLOWERS_BANDS,
    )

    # 2. public_repos — output volume
    breakdown["public_repos"] = _band_lookup(
        int(payload.get("public_repos") or 0), _USER_REPOS_BANDS,
    )

    # 3. age
    breakdown["age"] = _band_lookup(
        _years_since(payload.get("created_at")), _USER_AGE_BANDS,
    )

    # 4. populated profile is a humanity / care signal
    breakdown["has_bio"] = _USER_BIO_POINTS if (payload.get("bio") or "").strip() else 0
    breakdown["has_blog"] = _USER_BLOG_POINTS if (payload.get("blog") or "").strip() else 0
    breakdown["has_company"] = (
        _USER_COMPANY_POINTS if (payload.get("company") or "").strip() else 0
    )

    # 5. account type Organization
    breakdown["is_organization"] = (
        _USER_ORGANIZATION_POINTS
        if (payload.get("type") or "").lower() == "organization"
        else 0
    )

    score = max(0, sum(breakdown.values()))
    return score, breakdown


def derive_project_signals(
    payload: dict[str, Any], *, owner_authority_0_75: float = 0.0,
) -> tuple[float, dict[str, Any]]:
    """High-level: compute project authority + signals dict to persist.

    ``owner_authority_0_75`` is the github_user's derived_authority on
    a 0-0.75 scale; we map it to a 0-20 contribution to the project.
    Pass 0.0 when the owner hasn't been fetched yet.
    """
    owner_lift = int(round(20 * owner_authority_0_75 / 0.75)) if owner_authority_0_75 > 0 else 0
    score, breakdown = compute_project_authority(payload, owner_lift=owner_lift)
    authority = round(score / 100.0, 4)

    signals = {
        "owner_login": (payload.get("owner") or {}).get("login"),
        "name": payload.get("name"),
        "full_name": payload.get("full_name"),
        "description": payload.get("description"),
        "html_url": payload.get("html_url"),
        "homepage": payload.get("homepage"),
        "language": payload.get("language"),
        "stargazers_count": payload.get("stargazers_count"),
        "forks_count": payload.get("forks_count"),
        "watchers_count": payload.get("watchers_count"),
        "subscribers_count": payload.get("subscribers_count"),
        "open_issues_count": payload.get("open_issues_count"),
        "created_at": payload.get("created_at"),
        "pushed_at": payload.get("pushed_at"),
        "updated_at": payload.get("updated_at"),
        "archived": payload.get("archived"),
        "fork": payload.get("fork"),
        "disabled": payload.get("disabled"),
        "license_spdx": (payload.get("license") or {}).get("spdx_id")
        if isinstance(payload.get("license"), dict) else None,
        "default_branch": payload.get("default_branch"),
        "topics": payload.get("topics"),
        # derived
        "project_authority_score": score,
        "weight_breakdown": breakdown,
        "missing_dimensions": [
            "commit_velocity_30d",       # would need /commits or /stats endpoints
            "contributor_count",         # would need /contributors
            "issue_resolution_velocity", # would need /issues
            "release_cadence",           # would need /releases
            "ci_status",                 # would need /actions
        ],
    }
    return authority, signals


def derive_user_signals(payload: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """High-level: compute user/org authority + signals dict to persist."""
    score, breakdown = compute_user_authority(payload)
    # 75 is the theoretical max; we normalize to 0-1 via /100, leaving
    # 0.75-1.0 for explicit human curation (e.g. canonical orgs).
    authority = round(score / 100.0, 4)

    signals = {
        "login": payload.get("login"),
        "name": payload.get("name"),
        "id": payload.get("id"),
        "type": payload.get("type"),                # User | Organization
        "company": payload.get("company"),
        "blog": payload.get("blog"),
        "location": payload.get("location"),
        "email": payload.get("email"),
        "bio": payload.get("bio"),
        "twitter_username": payload.get("twitter_username"),  # ⚡ cross-platform link!
        "public_repos": payload.get("public_repos"),
        "public_gists": payload.get("public_gists"),
        "followers": payload.get("followers"),
        "following": payload.get("following"),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        # derived
        "user_authority_score": score,
        "weight_breakdown": breakdown,
        "missing_dimensions": [
            "total_stars_across_repos",   # would need /repos enumeration
            "contribution_streak",        # would need /events
            "sponsor_count",              # would need /sponsors
        ],
    }
    return authority, signals


def stub_signals_for_missing(identity: str, kind: str, reason: str) -> dict[str, Any]:
    """For 404s — record the fact without authority."""
    return {
        "identity": identity,
        "kind": kind,
        "fetch_status": "not_found",
        "fetch_reason": reason,
    }
