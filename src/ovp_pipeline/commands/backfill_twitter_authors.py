"""ovp-backfill-twitter-authors — populate entities for X/Twitter handles.

Scans the vault for X handles, calls twitterapi.io for each one not
already cached (or stale), persists the result into the ``entities``
table, and prints a progress + cost summary.

Usage::

    ovp-backfill-twitter-authors --vault-dir ~/Documents/ovp-vault
    ovp-backfill-twitter-authors --vault-dir ~/Documents/ovp-vault --dry-run
    ovp-backfill-twitter-authors --only karpathy --force

Authentication
--------------

API key is read in this order, first match wins:
  1. ``--api-key`` CLI arg
  2. ``TWITTERAPI_IO_KEY`` environment variable
  3. First non-comment line of ``<vault-dir>/60-Logs/.twitterapi-io-key``

Cost
----

Single-user calls cost $0.00018 each.  The CLI tracks running cost
and prints it in the summary; pass ``--max-handles N`` to cap the
spend if you're nervous.

Idempotence
-----------

Handles already in ``entities`` are skipped unless their
``last_fetched_at`` is older than ``--max-age-days`` (default 30).
``--force`` re-fetches everything regardless.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..entities.scan import scan_twitter_handles
from ..entities.store import EntityStore
from ..entities.twitter_backfill import (
    PRICE_PER_CALL_USD,
    derive_authority_from_payload,
    fetch_user_info,
    stub_signals_for_missing,
)


_ENTITY_TYPE = "twitter_author"
_FETCH_SOURCE = "twitterapi.io"
_DEFAULT_MAX_AGE_DAYS = 30
# Tiny pause between calls to be polite even if rate limit isn't hit.
_PER_CALL_SLEEP_S = 0.1


def _resolve_api_key(cli_arg: str | None, vault_dir: Path) -> str | None:
    if cli_arg:
        return cli_arg.strip()
    env = os.environ.get("TWITTERAPI_IO_KEY")
    if env:
        return env.strip()
    keyfile = vault_dir / "60-Logs" / ".twitterapi-io-key"
    if keyfile.exists():
        for line in keyfile.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    return None


def _is_stale(last_fetched_at: str, max_age_days: int) -> bool:
    try:
        dt = datetime.fromisoformat(last_fetched_at)
    except ValueError:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt) > timedelta(days=max_age_days)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill twitter_author entities via twitterapi.io",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument("--api-key", default=None,
                        help="twitterapi.io API key (else env / keyfile)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Plan + cost only, no API calls, no DB writes")
    parser.add_argument("--max-handles", type=int, default=None,
                        help="Stop after N new fetches (cost cap)")
    parser.add_argument("--max-age-days", type=int, default=_DEFAULT_MAX_AGE_DAYS,
                        help="Re-fetch entities older than this many days")
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch every handle, even if cached + fresh")
    parser.add_argument("--only", default=None,
                        help="Only fetch this single handle (debug)")
    parser.add_argument("--min-mentions", type=int, default=1,
                        help="Skip handles with fewer than N total mentions "
                             "(default 1 = all)")
    args = parser.parse_args(argv)

    vault = args.vault_dir.resolve()
    if not vault.is_dir():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2

    db_path = vault / "60-Logs" / "knowledge.db"
    store = EntityStore(db_path=db_path)

    # ---- discover handles --------------------------------------------------

    if args.only:
        target_handles = [(args.only.lower().lstrip("@"), 0)]
    else:
        mentions = scan_twitter_handles(vault)
        target_handles = [
            (m.handle, m.mention_count)
            for m in mentions
            if m.mention_count >= args.min_mentions
        ]

    if not target_handles:
        print("no handles to process")
        return 0

    # ---- filter against cache ---------------------------------------------

    to_fetch: list[tuple[str, int]] = []
    cached_fresh: int = 0
    for handle, mentions in target_handles:
        existing = store.get(_ENTITY_TYPE, handle)
        if existing and not args.force:
            if not _is_stale(existing.last_fetched_at, args.max_age_days):
                cached_fresh += 1
                continue
        to_fetch.append((handle, mentions))

    if args.max_handles is not None:
        to_fetch = to_fetch[: args.max_handles]

    # ---- preflight summary -------------------------------------------------

    cost_estimate = len(to_fetch) * PRICE_PER_CALL_USD
    print(f"vault:                {vault}")
    print(f"db:                   {db_path}")
    print(f"handles discovered:   {len(target_handles)}")
    print(f"already cached fresh: {cached_fresh}")
    print(f"to fetch this run:    {len(to_fetch)}")
    print(f"estimated cost:       ${cost_estimate:.4f}  "
          f"(at ${PRICE_PER_CALL_USD}/call)")

    if args.dry_run:
        print("\n--dry-run set; not calling API or writing DB")
        return 0

    if not to_fetch:
        print("\nnothing to do — all handles cached fresh")
        return 0

    # ---- auth --------------------------------------------------------------

    api_key = _resolve_api_key(args.api_key, vault)
    if not api_key:
        print("error: no API key found (--api-key, TWITTERAPI_IO_KEY env, "
              "or 60-Logs/.twitterapi-io-key file)", file=sys.stderr)
        return 2

    # ---- fetch loop --------------------------------------------------------

    n_ok = n_not_found = n_error = 0
    for i, (handle, mentions) in enumerate(to_fetch, 1):
        result = fetch_user_info(handle, api_key=api_key)
        if result.status == "ok" and result.payload is not None:
            authority, signals = derive_authority_from_payload(result.payload)
            signals["mention_count_in_vault"] = mentions
            store.upsert(
                entity_type=_ENTITY_TYPE,
                identity_key=handle,
                canonical_name=result.payload.get("name"),
                signals=signals,
                derived_authority=authority,
                fetch_source=_FETCH_SOURCE,
            )
            n_ok += 1
        elif result.status == "not_found":
            store.upsert(
                entity_type=_ENTITY_TYPE,
                identity_key=handle,
                canonical_name=None,
                signals=stub_signals_for_missing(handle, result.error or ""),
                derived_authority=None,
                fetch_source=_FETCH_SOURCE,
            )
            n_not_found += 1
        else:
            print(f"  [error] @{handle}: {result.error}", file=sys.stderr)
            n_error += 1

        if i % 25 == 0 or i == len(to_fetch):
            done = n_ok + n_not_found + n_error
            print(f"  progress: {done:>4}/{len(to_fetch)}  "
                  f"ok={n_ok} not_found={n_not_found} error={n_error}")
        time.sleep(_PER_CALL_SLEEP_S)

    actual_cost = (n_ok + n_not_found + n_error) * PRICE_PER_CALL_USD

    print()
    print("=== Summary ===")
    print(f"  ok:        {n_ok:>5}")
    print(f"  not_found: {n_not_found:>5}")
    print(f"  error:     {n_error:>5}")
    print(f"  cached:    {cached_fresh:>5}  (skipped — already fresh)")
    print(f"  cost:      ${actual_cost:.4f}  (actual, including failed calls)")
    print()
    print("Top 10 by partial authority:")
    top = store.list_by_type(_ENTITY_TYPE, limit=10)
    for e in top:
        if e.derived_authority is None:
            continue
        print(f"  {e.derived_authority:.2f}  @{e.identity_key:<25} "
              f"{(e.canonical_name or '')[:40]}")

    return 0 if n_error == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
