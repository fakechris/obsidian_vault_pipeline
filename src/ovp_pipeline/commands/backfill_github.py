"""ovp-backfill-github — populate github_project + github_user entities.

Scans the vault for ``github.com/<owner>/<repo>`` and ``github.com/<owner>``
mentions, calls the GitHub REST API for each unique entity, persists
results in the ``entities`` table.

Two-pass strategy:
  1. Fetch all unique users first (so per-user authority is known).
  2. Fetch all unique repos, looking up each repo's owner_login in
     the just-populated user store to compute owner_lift.

This means a fresh repo whose owner you've never seen still gets
scored from its intrinsic stars/forks/recency — owner_lift just
becomes a bonus signal when available.

Usage::

    ovp-backfill-github --vault-dir ~/Documents/ovp-vault          # both passes
    ovp-backfill-github --kind users                               # users only
    ovp-backfill-github --kind projects                            # projects only
    ovp-backfill-github --dry-run                                  # plan + budget

Auth
----

Public-repo reads work without a token at 60 req/hour, but for the
~280 entity OVP vault that's a 5-hour stagger.  Provide a token via:

  1. ``--token`` CLI arg
  2. ``GITHUB_TOKEN`` env var
  3. First non-comment line of ``<vault>/60-Logs/.github-token``

A no-scope (public-only) PAT is sufficient — we never write.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..entities.github_backfill import (
    PRICE_PER_CALL_USD,
    derive_project_signals,
    derive_user_signals,
    fetch_repo,
    fetch_user,
    stub_signals_for_missing,
)
from ..entities.scan import scan_github_mentions
from ..entities.store import EntityStore


_PROJECT_TYPE = "github_project"
_USER_TYPE = "github_user"
_FETCH_SOURCE = "github_rest"
_DEFAULT_MAX_AGE_DAYS = 30
# GitHub allows ~50 req/sec authenticated; we go slower to be polite
# and to dodge any secondary rate limits.
_PER_CALL_SLEEP_S = 0.05


def _resolve_token(cli_arg: str | None, vault_dir: Path) -> str | None:
    if cli_arg:
        return cli_arg.strip()
    env = os.environ.get("GITHUB_TOKEN")
    if env:
        return env.strip()
    keyfile = vault_dir / "60-Logs" / ".github-token"
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


def _backfill_users(
    *,
    store: EntityStore,
    usernames: list[tuple[str, int]],
    token: str | None,
    max_age_days: int,
    force: bool,
    max_handles: int | None,
) -> tuple[int, int, int, int]:
    """Returns (n_ok, n_not_found, n_error, n_cached)."""
    to_fetch: list[tuple[str, int]] = []
    cached = 0
    for login, mentions in usernames:
        existing = store.get(_USER_TYPE, login)
        if existing and not force:
            if not _is_stale(existing.last_fetched_at, max_age_days):
                cached += 1
                continue
        to_fetch.append((login, mentions))
    if max_handles is not None:
        to_fetch = to_fetch[:max_handles]

    print(f"  users: {len(usernames)} discovered, {cached} fresh-cached, "
          f"{len(to_fetch)} to fetch")
    if not to_fetch:
        return 0, 0, 0, cached

    n_ok = n_nf = n_err = 0
    for i, (login, mentions) in enumerate(to_fetch, 1):
        result = fetch_user(login, token=token)
        if result.status == "ok" and result.payload is not None:
            authority, signals = derive_user_signals(result.payload)
            signals["mention_count_in_vault"] = mentions
            store.upsert(
                entity_type=_USER_TYPE, identity_key=login,
                canonical_name=result.payload.get("name") or login,
                signals=signals, derived_authority=authority,
                fetch_source=_FETCH_SOURCE,
            )
            n_ok += 1
        elif result.status == "not_found":
            store.upsert(
                entity_type=_USER_TYPE, identity_key=login,
                canonical_name=None,
                signals=stub_signals_for_missing(login, "user", result.error or ""),
                derived_authority=None, fetch_source=_FETCH_SOURCE,
            )
            n_nf += 1
        else:
            print(f"    [error] {login}: {result.error}", file=sys.stderr)
            n_err += 1
        if i % 25 == 0 or i == len(to_fetch):
            print(f"    users progress: {i:>4}/{len(to_fetch)}  "
                  f"ok={n_ok} not_found={n_nf} error={n_err}", flush=True)
        time.sleep(_PER_CALL_SLEEP_S)
    return n_ok, n_nf, n_err, cached


def _backfill_projects(
    *,
    store: EntityStore,
    repos: list[tuple[str, str, int]],   # (owner, repo, mention_count)
    token: str | None,
    max_age_days: int,
    force: bool,
    max_handles: int | None,
) -> tuple[int, int, int, int]:
    to_fetch: list[tuple[str, str, int]] = []
    cached = 0
    for owner, repo, mentions in repos:
        identity = f"{owner}/{repo}"
        existing = store.get(_PROJECT_TYPE, identity)
        if existing and not force:
            if not _is_stale(existing.last_fetched_at, max_age_days):
                cached += 1
                continue
        to_fetch.append((owner, repo, mentions))
    if max_handles is not None:
        to_fetch = to_fetch[:max_handles]

    print(f"  projects: {len(repos)} discovered, {cached} fresh-cached, "
          f"{len(to_fetch)} to fetch")
    if not to_fetch:
        return 0, 0, 0, cached

    n_ok = n_nf = n_err = 0
    for i, (owner, repo, mentions) in enumerate(to_fetch, 1):
        identity = f"{owner}/{repo}"
        result = fetch_repo(owner, repo, token=token)
        if result.status == "ok" and result.payload is not None:
            owner_login = (result.payload.get("owner") or {}).get("login") or owner
            owner_entity = store.get(_USER_TYPE, owner_login.lower())
            owner_authority = (
                owner_entity.derived_authority if owner_entity is not None else 0.0
            ) or 0.0
            authority, signals = derive_project_signals(
                result.payload, owner_authority_0_75=owner_authority,
            )
            signals["mention_count_in_vault"] = mentions
            store.upsert(
                entity_type=_PROJECT_TYPE, identity_key=identity,
                canonical_name=result.payload.get("full_name") or identity,
                signals=signals, derived_authority=authority,
                fetch_source=_FETCH_SOURCE,
            )
            n_ok += 1
        elif result.status == "not_found":
            store.upsert(
                entity_type=_PROJECT_TYPE, identity_key=identity,
                canonical_name=None,
                signals=stub_signals_for_missing(identity, "project", result.error or ""),
                derived_authority=None, fetch_source=_FETCH_SOURCE,
            )
            n_nf += 1
        else:
            print(f"    [error] {identity}: {result.error}", file=sys.stderr)
            n_err += 1
        if i % 25 == 0 or i == len(to_fetch):
            print(f"    projects progress: {i:>4}/{len(to_fetch)}  "
                  f"ok={n_ok} not_found={n_nf} error={n_err}", flush=True)
        time.sleep(_PER_CALL_SLEEP_S)
    return n_ok, n_nf, n_err, cached


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill github_project + github_user entities",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument("--token", default=None,
                        help="GitHub PAT (else env GITHUB_TOKEN or keyfile)")
    parser.add_argument("--kind", choices=["both", "users", "projects"],
                        default="both",
                        help="Which entity type(s) to backfill (default both)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Plan + rate-limit budget only, no API calls")
    parser.add_argument("--max-handles", type=int, default=None,
                        help="Cap fetches per kind (debug / partial run)")
    parser.add_argument("--max-age-days", type=int, default=_DEFAULT_MAX_AGE_DAYS,
                        help="Re-fetch entities older than this many days")
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch every entity, even if cached + fresh")
    args = parser.parse_args(argv)

    vault = args.vault_dir.resolve()
    if not vault.is_dir():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2

    db_path = vault / "60-Logs" / "knowledge.db"
    store = EntityStore(db_path=db_path)

    # ---- discover entities -------------------------------------------------

    print("scanning vault...")
    mentions = scan_github_mentions(vault)
    repos: list[tuple[str, str, int]] = [
        (m.owner, m.repo, m.mention_count)
        for m in mentions if m.repo is not None
    ]
    # Owners come from the union of (a) all repo owners and (b) any
    # github.com/<owner> URLs that didn't carry a /repo segment.
    owner_counts: dict[str, int] = {}
    for owner, _repo, c in repos:
        owner_counts[owner] = owner_counts.get(owner, 0) + c
    users: list[tuple[str, int]] = sorted(
        owner_counts.items(), key=lambda kv: -kv[1],
    )

    print(f"vault: {vault}")
    print(f"db:    {db_path}")
    print(f"discovered: {len(repos)} unique repos, {len(users)} unique owners")

    # ---- preflight summary -------------------------------------------------

    if args.dry_run:
        print("\n--dry-run set; no API calls, no DB writes")
        print(f"would fetch: kind={args.kind}")
        if args.kind in {"both", "users"}:
            print(f"  users:    {len(users)} calls (cost $0)")
        if args.kind in {"both", "projects"}:
            print(f"  projects: {len(repos)} calls (cost $0)")
        return 0

    token = _resolve_token(args.token, vault)
    if not token:
        print("warning: no GITHUB_TOKEN; falling back to 60 req/hr unauthenticated.",
              file=sys.stderr)
        print("         For >60 entities, set --token or env GITHUB_TOKEN.",
              file=sys.stderr)

    # ---- run pass(es) ------------------------------------------------------

    totals = {"ok": 0, "not_found": 0, "error": 0, "cached": 0}

    if args.kind in {"both", "users"}:
        print("\n=== users pass ===")
        ok, nf, err, cached = _backfill_users(
            store=store, usernames=users, token=token,
            max_age_days=args.max_age_days, force=args.force,
            max_handles=args.max_handles,
        )
        totals["ok"] += ok
        totals["not_found"] += nf
        totals["error"] += err
        totals["cached"] += cached

    if args.kind in {"both", "projects"}:
        print("\n=== projects pass ===")
        ok, nf, err, cached = _backfill_projects(
            store=store, repos=repos, token=token,
            max_age_days=args.max_age_days, force=args.force,
            max_handles=args.max_handles,
        )
        totals["ok"] += ok
        totals["not_found"] += nf
        totals["error"] += err
        totals["cached"] += cached

    print()
    print("=== Summary ===")
    print(f"  ok:        {totals['ok']:>5}")
    print(f"  not_found: {totals['not_found']:>5}")
    print(f"  error:     {totals['error']:>5}")
    print(f"  cached:    {totals['cached']:>5}")
    print(f"  cost:      ${PRICE_PER_CALL_USD * (totals['ok'] + totals['not_found']):.4f}")

    print("\nTop 10 projects by authority:")
    for e in store.list_by_type(_PROJECT_TYPE, limit=10):
        if e.derived_authority is None:
            continue
        print(f"  {e.derived_authority:.2f}  {e.identity_key:<35} "
              f"{(e.canonical_name or '')[:40]}")

    print("\nTop 10 users/orgs by authority:")
    for e in store.list_by_type(_USER_TYPE, limit=10):
        if e.derived_authority is None:
            continue
        print(f"  {e.derived_authority:.2f}  {e.identity_key:<25} "
              f"{(e.canonical_name or '')[:40]}")

    return 0 if totals["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
