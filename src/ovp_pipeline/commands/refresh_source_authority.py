"""ovp-refresh-source-authority — chained refresh for the entity layer.

Runs the three pieces in sequence:

  1. ``ovp-backfill-twitter-authors`` (paid, ~$0.10 per full vault)
  2. ``ovp-backfill-github``          (free, GitHub PAT recommended)
  3. ``ovp-merge-identities``         (local, no network)

Each step is idempotent — a fresh run only re-fetches entities older
than ``--max-age-days`` (default 30).  Status (per-step success +
timing + counts) is written to ``60-Logs/entity_refresh_status.json``
so a launchd / cron run can be inspected after the fact.

Why one wrapper instead of three cron lines
-------------------------------------------

* Single lockfile prevents two runs colliding on the entity table.
* Twitter cost ($0.10) shouldn't be paid weekly forever; the wrapper
  prints a one-line warning when the running monthly cost crosses
  ``--monthly-budget``.
* The identity-merge step is cheap but useless before the two
  backfills land — sequencing them in a single binary avoids the
  "merge-then-backfill" misorder.

launchd plist (weekly, Mondays at 03:30 UTC)
--------------------------------------------

Drop into ``~/Library/LaunchAgents/ai.ovp.refresh-source-authority.plist``::

    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
        "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
        <key>Label</key>
        <string>ai.ovp.refresh-source-authority</string>
        <key>ProgramArguments</key>
        <array>
            <string>/path/to/ovp-refresh-source-authority</string>
            <string>--vault-dir</string>
            <string>/Users/USERNAME/Documents/ovp-vault</string>
            <string>--max-age-days</string>
            <string>14</string>
        </array>
        <key>EnvironmentVariables</key>
        <dict>
            <key>TWITTERAPI_IO_KEY</key> <string>...</string>
            <key>GITHUB_TOKEN</key>      <string>...</string>
        </dict>
        <key>StartCalendarInterval</key>
        <dict>
            <key>Weekday</key> <integer>1</integer>
            <key>Hour</key>    <integer>3</integer>
            <key>Minute</key>  <integer>30</integer>
        </dict>
        <key>StandardOutPath</key>
        <string>/tmp/ovp-refresh-source-authority.log</string>
        <key>StandardErrorPath</key>
        <string>/tmp/ovp-refresh-source-authority.log</string>
    </dict>
    </plist>

Then::

    launchctl load -w ~/Library/LaunchAgents/ai.ovp.refresh-source-authority.plist

Failure mode: the wrapper exits 0 even when one step fails, so cron
doesn't bail.  Failure detail lives in the status JSON.  Use
``--strict`` to bubble individual failures out as non-zero exit.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


_DEFAULT_MAX_AGE_DAYS = 14
_LOCKFILE_NAME = ".refresh-source-authority.lock"
_STATUS_FILENAME = "entity_refresh_status.json"
_TWITTER_COST_PER_CALL_USD = 0.00018
_DEFAULT_MONTHLY_BUDGET_USD = 1.0

# Cost-canary inputs.  ``_ESTIMATED_MAX_HANDLES`` is the upper bound on
# unique X handles in the OVP vault as of PR-E1's recon.  Using a
# constant — rather than recomputing per run — keeps the cost
# warning fast (no scan) and conservative (real refreshes will hit
# fewer handles after caching).
_ESTIMATED_MAX_HANDLES = 521
_DAYS_PER_MONTH = 30


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _exclusive_lock(lock_path: Path) -> Iterator[None]:
    """Atomic O_CREAT|O_EXCL lockfile with the PID inside.

    Released on context exit (delete file).  If a stale lock points
    to a no-longer-running PID, we steal it — the previous run
    crashed before cleanup and we don't want refreshes to hang
    forever.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # Steal stale lock if its PID is dead.
        try:
            existing_pid = int(lock_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            existing_pid = -1
        if existing_pid > 0 and _pid_alive(existing_pid):
            raise SystemExit(
                f"another refresh is running (pid {existing_pid}); "
                f"remove {lock_path} after confirming it crashed",
            )
        # Stale — overwrite.
        lock_path.unlink(missing_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM   # exists but unowned
    return True


def _run_step(
    name: str, fn, *, args: list[str], capture: dict[str, Any],
) -> int:
    """Invoke a sub-CLI's ``main(argv)`` with timing + per-step status."""
    started = time.monotonic()
    print(f"\n=== step: {name} ===", flush=True)
    print(f"   argv: {' '.join(args)}")
    try:
        rc = int(fn(args))
        ok = rc == 0
    except SystemExit as exc:
        rc = int(exc.code or 0)
        ok = rc == 0
    except Exception as exc:  # noqa: BLE001 - one failed step shouldn't abort
        rc = 1
        ok = False
        print(f"   error: {exc}", file=sys.stderr)
    elapsed = time.monotonic() - started
    capture[name] = {
        "rc": rc,
        "ok": ok,
        "elapsed_s": round(elapsed, 2),
        "ran_at": _iso_now(),
    }
    print(f"   {'OK' if ok else 'FAILED'}  {elapsed:.1f}s")
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh source-authority entity layer (twitter + github + merge)",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument("--max-age-days", type=int, default=_DEFAULT_MAX_AGE_DAYS,
                        help="Forwarded to each sub-backfill (default 14)")
    parser.add_argument("--skip-twitter", action="store_true")
    parser.add_argument("--skip-github", action="store_true")
    parser.add_argument("--skip-merge", action="store_true")
    parser.add_argument("--strict", action="store_true",
                        help="Exit non-zero if any step failed (default: keep "
                             "running, surface failures in the status JSON)")
    parser.add_argument("--monthly-budget-usd", type=float,
                        default=_DEFAULT_MONTHLY_BUDGET_USD,
                        help="Print a warning when running cost crosses this "
                             "(rough proxy: assumes a full re-fetch each run)")
    args = parser.parse_args(argv)

    vault = args.vault_dir.resolve()
    if not vault.is_dir():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2

    lock_path = vault / "60-Logs" / _LOCKFILE_NAME
    status_path = vault / "60-Logs" / _STATUS_FILENAME

    # Lazy imports so an import-time failure in one sub-CLI doesn't
    # block the others.
    from .backfill_github import main as backfill_github
    from .backfill_twitter_authors import main as backfill_twitter
    from .merge_identities import main as merge_identities

    capture: dict[str, Any] = {}
    started = _iso_now()

    with _exclusive_lock(lock_path):
        if not args.skip_twitter:
            _run_step(
                "twitter_backfill", backfill_twitter,
                args=[
                    "--vault-dir", str(vault),
                    "--max-age-days", str(args.max_age_days),
                ],
                capture=capture,
            )

        if not args.skip_github:
            _run_step(
                "github_backfill", backfill_github,
                args=[
                    "--vault-dir", str(vault),
                    "--max-age-days", str(args.max_age_days),
                ],
                capture=capture,
            )

        if not args.skip_merge:
            _run_step(
                "identity_merge", merge_identities,
                args=["--vault-dir", str(vault)],
                capture=capture,
            )

    finished = _iso_now()

    # Persist status.
    status = {
        "started_at": started,
        "finished_at": finished,
        "max_age_days": args.max_age_days,
        "steps": capture,
        "all_ok": all(s.get("ok") for s in capture.values()),
    }
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Cost canary — rough estimate (we don't track per-call cost
    # across refreshes; this is a "yell when it might be a problem"
    # signal, not an accountant).
    twitter_step = capture.get("twitter_backfill") or {}
    if twitter_step.get("ok"):
        # Each weekly run *could* re-fetch up to _ESTIMATED_MAX_HANDLES
        # handles ≈ $0.10.  4 runs/month ≈ $0.40 — well below default
        # budget.  The warning fires if the user dropped
        # --max-age-days way down.
        approx_monthly = (
            _ESTIMATED_MAX_HANDLES
            * _TWITTER_COST_PER_CALL_USD
            * (_DAYS_PER_MONTH / max(args.max_age_days, 1))
        )
        if approx_monthly > args.monthly_budget_usd:
            print(
                f"\nwarning: estimated monthly twitter cost "
                f"${approx_monthly:.2f} exceeds budget "
                f"${args.monthly_budget_usd:.2f} "
                f"(--max-age-days={args.max_age_days})",
                file=sys.stderr,
            )

    print(f"\nstatus written to {status_path}")
    print(f"all_ok: {status['all_ok']}")

    if args.strict and not status["all_ok"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
