"""twitterapi.io fetch + partial author_weight computation.

The obsidian-clipper Twitter adapter computes a 7-dimension
``author_weight`` (0-100) at clip time.  This module reconstructs an
approximation from the fields twitterapi.io exposes — necessarily
weaker because two of the strongest dimensions (``listed_count`` and
mutual-network signals) aren't available via that API.

Fidelity tradeoff (vs. clipper's 100-point scale):
  * Clipper:  followers + listed + mutuals + i_follow + age +
              verified/company + automated  → 100 max
  * Backfill: followers + age + verified + company-affiliation +
              automated penalty             → ~70 max

Therefore the partial score is **always strictly weaker than** the
clipper's, and the runtime authority resolver (PR-E3) should prefer
clipper-sourced values when both exist.

API details:
  * GET /twitter/user/info?userName=<handle>
  * Header: X-API-Key
  * $0.00018 / call (single-user)
  * 521 handles in the OVP vault → ~$0.094 one-shot

Failure modes handled:
  * 401 / 403  — bad key, raise immediately
  * 429        — exponential backoff
  * 404 / "user not found" — record as suspended, don't retry
  * Network    — retry up to 3 times then mark failed
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
from typing import Any

logger = logging.getLogger(__name__)

_API_BASE = "https://api.twitterapi.io"
_USER_INFO_PATH = "/twitter/user/info"
_USER_AGENT = "ovp-backfill/1.0 (https://github.com/fakechris/obsidian_vault_pipeline)"
_DEFAULT_TIMEOUT_S = 15.0
_MAX_RETRIES = 3
_BACKOFF_BASE_S = 1.5

# Cost in USD per single-user call (per twitterapi.io intro page).
PRICE_PER_CALL_USD = 0.00018


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Outcome of one twitterapi.io call.

    Three terminal states:
      * ``ok``        — got a user payload; ``payload`` is non-None
      * ``not_found`` — handle suspended/deleted; record stub entity
      * ``error``     — network/auth/quota; do NOT mark as authoritative

    The CLI counts these into the final summary.
    """

    handle: str
    status: str          # "ok" | "not_found" | "error"
    payload: dict[str, Any] | None
    error: str | None    # human-readable diagnosis if status != "ok"


def fetch_user_info(
    handle: str, *, api_key: str, timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> FetchResult:
    """Single-call wrapper around GET /twitter/user/info.

    Idempotent / safe to call repeatedly — twitterapi.io has no
    mutating side effects on this endpoint.
    """
    if not handle or not api_key:
        return FetchResult(handle, "error", None, "missing handle or api_key")

    qs = urllib.parse.urlencode({"userName": handle})
    url = f"{_API_BASE}{_USER_INFO_PATH}?{qs}"
    headers = {
        "X-API-Key": api_key,
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }

    last_error: str = "unknown"
    for attempt in range(_MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            # 401/403 are auth — bail immediately.
            if e.code in (401, 403):
                return FetchResult(
                    handle, "error", None,
                    f"auth failed: HTTP {e.code} (check API key)",
                )
            # 404 or "user not found"-style.
            if e.code == 404:
                return FetchResult(handle, "not_found", None, "404 from API")
            # 429 → backoff
            if e.code == 429:
                wait = _BACKOFF_BASE_S * (2 ** attempt)
                logger.info("rate limited on %s, sleeping %.1fs", handle, wait)
                time.sleep(wait)
                last_error = "HTTP 429"
                continue
            last_error = f"HTTP {e.code}"
            time.sleep(_BACKOFF_BASE_S * (2 ** attempt))
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = f"network: {e}"
            time.sleep(_BACKOFF_BASE_S * (2 ** attempt))
            continue

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as e:
            return FetchResult(handle, "error", None, f"bad JSON: {e}")

        status = parsed.get("status")
        if status == "success":
            data = parsed.get("data") or {}
            if not isinstance(data, dict) or not data:
                # API returned success but empty body — treat as not found.
                return FetchResult(handle, "not_found", None, "empty data")
            return FetchResult(handle, "ok", data, None)

        # status == "error" or unknown.
        msg = str(parsed.get("msg") or parsed.get("message") or "")
        if "not found" in msg.lower() or "suspended" in msg.lower() \
                or "unavailable" in msg.lower():
            return FetchResult(handle, "not_found", None, msg or "user not found")
        return FetchResult(handle, "error", None, msg or "API returned error")

    return FetchResult(handle, "error", None, last_error)


# ---------------------------------------------------------------------------
# Score derivation
# ---------------------------------------------------------------------------


def _account_age_years(created_at: str | None) -> float:
    """Parse ``createdAt`` and return age in years (0 if unparseable).

    twitterapi.io can return either ISO 8601
    (``2016-03-20T17:01:04.000000Z``) or X's classic format
    (``Thu Dec 13 08:41:26 +0000 2007``).
    """
    if not created_at:
        return 0.0
    candidates = [
        ("%Y-%m-%dT%H:%M:%S.%f%z", created_at.replace("Z", "+0000")),
        ("%Y-%m-%dT%H:%M:%S%z", created_at.replace("Z", "+0000")),
        ("%a %b %d %H:%M:%S %z %Y", created_at),
    ]
    for fmt, val in candidates:
        try:
            dt = datetime.strptime(val, fmt)
        except ValueError:
            continue
        delta = datetime.now(timezone.utc) - dt
        return max(delta.days / 365.25, 0.0)
    return 0.0


def compute_partial_author_weight(payload: dict[str, Any]) -> tuple[int, dict[str, int]]:
    """Compute partial author_weight (0-100 scale) + per-dimension breakdown.

    Returns ``(score, {dimension: points})`` so the breakdown can be
    persisted alongside the score for explainability.

    The dimensions deliberately mirror the obsidian-clipper formula
    where a corresponding signal exists, and zero-out the dimensions
    that twitterapi.io can't supply (listed, mutuals, i_follow).
    Total possible: 70.  Anything higher comes from clipper-frontmatter,
    not from this code.
    """
    breakdown: dict[str, int] = {}

    # 1. followers (max 25)
    fol = int(payload.get("followers") or 0)
    if fol >= 100_000:
        breakdown["followers"] = 25
    elif fol >= 10_000:
        breakdown["followers"] = 22
    elif fol >= 1_000:
        breakdown["followers"] = 17
    elif fol >= 500:
        breakdown["followers"] = 10
    elif fol >= 100:
        breakdown["followers"] = 5
    else:
        breakdown["followers"] = 0

    # 2. account age in years (max 10)
    age_y = _account_age_years(payload.get("createdAt"))
    if age_y >= 7:
        breakdown["age"] = 10
    elif age_y >= 3:
        breakdown["age"] = 7
    elif age_y >= 1:
        breakdown["age"] = 3
    else:
        breakdown["age"] = 0

    # 3. blue-verified flag (max 5).
    # Per the clipper docs: "蓝标现在已经没用" — kept low intentionally.
    breakdown["blue_verified"] = 5 if payload.get("isBlueVerified") else 0

    # 4. legacy verified / verifiedType — closer to "old blue check"
    # which the clipper docs flag as a strong signal (max 10).
    verified_type = (payload.get("verifiedType") or "").strip().lower()
    is_legacy_verified = bool(payload.get("isVerified"))
    if verified_type in {"government", "business", "company"} or is_legacy_verified:
        breakdown["legacy_verified"] = 10
    else:
        breakdown["legacy_verified"] = 0

    # 5. affiliation label (max 10).
    # twitterapi.io exposes ``affiliatesHighlightedLabel`` which
    # corresponds to the company-badge UI feature on X.  Treat any
    # non-empty mapping as "has affiliation".
    aff = payload.get("affiliatesHighlightedLabel")
    if isinstance(aff, dict) and aff:
        breakdown["affiliation"] = 10
    else:
        breakdown["affiliation"] = 0

    # 6. activity / statusesCount (max 10).
    # Anti-correlated with brand-new accounts and with bots.  Cap at
    # the high end so a tweet-spam farm doesn't get a free 10 points.
    statuses = int(payload.get("statusesCount") or 0)
    if 500 <= statuses <= 50_000:
        breakdown["activity"] = 10
    elif 50 <= statuses < 500:
        breakdown["activity"] = 5
    elif statuses > 50_000:
        breakdown["activity"] = 8       # too active, slight discount
    else:
        breakdown["activity"] = 0

    # 7. automated penalty (negative).
    breakdown["automated_penalty"] = -10 if payload.get("isAutomated") else 0

    score = max(0, sum(breakdown.values()))
    return score, breakdown


def derive_authority_from_payload(payload: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """High-level helper used by the CLI.

    Returns ``(authority_0_to_1, signals_dict_to_persist)``.  The
    signals dict is what gets stored in ``entities.signals_json``.
    """
    score, breakdown = compute_partial_author_weight(payload)
    # Map 0-70 (theoretical max) → 0-1 by dividing by 100, NOT 70.
    # That reserves the 0.71-1.0 band for clipper-rich frontmatter
    # which has the missing-dimensions signal.  See PR-E3 design.
    authority = round(score / 100.0, 4)

    signals = {
        # raw fields we care about for downstream score reconstruction
        "userName": payload.get("userName"),
        "id": payload.get("id"),
        "name": payload.get("name"),
        "description": payload.get("description"),
        "location": payload.get("location"),
        "createdAt": payload.get("createdAt"),
        "followers": payload.get("followers"),
        "following": payload.get("following"),
        "statusesCount": payload.get("statusesCount"),
        "mediaCount": payload.get("mediaCount"),
        "favouritesCount": payload.get("favouritesCount"),
        "isBlueVerified": payload.get("isBlueVerified"),
        "isVerified": payload.get("isVerified"),
        "verifiedType": payload.get("verifiedType"),
        "isAutomated": payload.get("isAutomated"),
        "affiliatesHighlightedLabel": payload.get("affiliatesHighlightedLabel"),
        # derived
        "partial_author_weight": score,
        "weight_breakdown": breakdown,
        # missing dimensions (kept explicit so future formula changes
        # don't silently fail to detect "we never had this data"):
        "missing_dimensions": ["listed_count", "mutuals_count",
                                "mutuals_top", "i_follow", "follows_me"],
    }
    return authority, signals


def stub_signals_for_missing(handle: str, reason: str) -> dict[str, Any]:
    """For 'not_found' / suspended handles — record the fact without authority."""
    return {
        "userName": handle,
        "fetch_status": "not_found",
        "fetch_reason": reason,
    }
