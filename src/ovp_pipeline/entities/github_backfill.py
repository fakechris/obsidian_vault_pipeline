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

    # 1. stars (max 30) — log10 bands so 100k-star repos don't crowd
    # out 1k-star repos that are still meaningful.
    stars = int(payload.get("stargazers_count") or 0)
    if stars >= 50_000:
        breakdown["stars"] = 30
    elif stars >= 10_000:
        breakdown["stars"] = 25
    elif stars >= 1_000:
        breakdown["stars"] = 20
    elif stars >= 100:
        breakdown["stars"] = 12
    elif stars >= 10:
        breakdown["stars"] = 5
    else:
        breakdown["stars"] = 0

    # 2. forks (max 10)
    forks = int(payload.get("forks_count") or 0)
    if forks >= 1_000:
        breakdown["forks"] = 10
    elif forks >= 100:
        breakdown["forks"] = 7
    elif forks >= 10:
        breakdown["forks"] = 3
    else:
        breakdown["forks"] = 0

    # 3. recency (max 15) — last push window
    pushed_at = payload.get("pushed_at") or payload.get("updated_at")
    days = _days_since(pushed_at)
    if days <= 30:
        breakdown["recency"] = 15
    elif days <= 180:
        breakdown["recency"] = 10
    elif days <= 365:
        breakdown["recency"] = 5
    elif days <= 365 * 3:
        breakdown["recency"] = 2
    else:
        breakdown["recency"] = 0

    # 4. age (max 10) — mature repos earn trust
    years = _years_since(payload.get("created_at"))
    if years >= 5:
        breakdown["age"] = 10
    elif years >= 2:
        breakdown["age"] = 6
    elif years >= 1:
        breakdown["age"] = 3
    else:
        breakdown["age"] = 0

    # 5. license (max 5) — legitimate OSS signal
    license_obj = payload.get("license")
    if isinstance(license_obj, dict) and license_obj.get("spdx_id"):
        breakdown["license"] = 5
    else:
        breakdown["license"] = 0

    # 6. not archived (max 5)
    breakdown["not_archived"] = 0 if payload.get("archived") else 5

    # 7. not a fork (max 5) — originals over mirrors
    breakdown["not_fork"] = 0 if payload.get("fork") else 5

    # 8. owner lift (max 20) — provided by caller after fetching user
    breakdown["owner_lift"] = max(0, min(20, owner_lift))

    score = max(0, sum(breakdown.values()))
    return score, breakdown


def compute_user_authority(payload: dict[str, Any]) -> tuple[int, dict[str, int]]:
    """Score one GitHub user / org (0-75)."""
    breakdown: dict[str, int] = {}

    # 1. followers (max 25)
    followers = int(payload.get("followers") or 0)
    if followers >= 10_000:
        breakdown["followers"] = 25
    elif followers >= 1_000:
        breakdown["followers"] = 20
    elif followers >= 100:
        breakdown["followers"] = 12
    elif followers >= 10:
        breakdown["followers"] = 5
    else:
        breakdown["followers"] = 0

    # 2. public_repos (max 10) — output volume
    repos = int(payload.get("public_repos") or 0)
    if repos >= 100:
        breakdown["public_repos"] = 10
    elif repos >= 30:
        breakdown["public_repos"] = 7
    elif repos >= 5:
        breakdown["public_repos"] = 3
    else:
        breakdown["public_repos"] = 0

    # 3. age (max 10)
    years = _years_since(payload.get("created_at"))
    if years >= 7:
        breakdown["age"] = 10
    elif years >= 3:
        breakdown["age"] = 6
    elif years >= 1:
        breakdown["age"] = 3
    else:
        breakdown["age"] = 0

    # 4. has bio (max 5) — populated profile is a humanity / care signal
    breakdown["has_bio"] = 5 if (payload.get("bio") or "").strip() else 0

    # 5. has blog/url (max 5)
    breakdown["has_blog"] = 5 if (payload.get("blog") or "").strip() else 0

    # 6. has company affiliation (max 10)
    breakdown["has_company"] = 10 if (payload.get("company") or "").strip() else 0

    # 7. account type Organization (max 10)
    breakdown["is_organization"] = 10 if (
        (payload.get("type") or "").lower() == "organization"
    ) else 0

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
