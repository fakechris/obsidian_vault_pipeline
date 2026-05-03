"""ovp-backup-db — point-in-time snapshot of knowledge.db.

Uses SQLite's online Backup API (``sqlite3.Connection.backup``) which
takes a consistent snapshot **without locking out concurrent writers**.
Safe to run while ``ovp-autopilot`` or any ingest job is active.

Why we need this
----------------

knowledge.db is the single source of truth for ~7000 evergreen
artifacts, ~25k audit events, ~13k relations, the source-authority
table, and the new entity layer.  A single corrupted write or
accidental ``DROP TABLE`` would lose months of curation.  The
markdown notes are recoverable from git, but the derived state
(embeddings, FTS index, graph_clusters, audit_events,
source_authority, entities) is expensive to rebuild.

Default schedule (no install — suggested launchd plist below)::

    ovp-backup-db --vault-dir ~/Documents/ovp-vault --keep 14
        # daily at 03:00; keeps the 14 most recent snapshots
        # ~211 MB × 14 ≈ 3 GB at the current DB size

Output layout::

    <vault>/60-Logs/backups/
        knowledge-2026-05-03T03-00-00.db      # full snapshot
        knowledge-2026-05-02T03-00-00.db
        ...
        knowledge-2026-05-03T03-00-00.sha256  # checksum manifest

The sha256 manifest lets you verify a snapshot decades later without
re-opening it through SQLite.

Schedule via launchd (macOS)
----------------------------

Drop the following at ``~/Library/LaunchAgents/ai.ovp.backup-db.plist``
and run ``launchctl load -w ~/Library/LaunchAgents/ai.ovp.backup-db.plist``::

    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
        "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
        <key>Label</key>          <string>ai.ovp.backup-db</string>
        <key>ProgramArguments</key>
        <array>
            <string>/path/to/ovp-backup-db</string>
            <string>--vault-dir</string>
            <string>/Users/USERNAME/Documents/ovp-vault</string>
            <string>--keep</string>
            <string>14</string>
        </array>
        <key>StartCalendarInterval</key>
        <dict>
            <key>Hour</key>    <integer>3</integer>
            <key>Minute</key>  <integer>0</integer>
        </dict>
        <key>StandardOutPath</key>
        <string>/tmp/ovp-backup-db.log</string>
        <key>StandardErrorPath</key>
        <string>/tmp/ovp-backup-db.log</string>
    </dict>
    </plist>
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


_DEFAULT_KEEP = 14
_BUFFER_PAGES = 200    # Pages copied per Backup API step (200 ≈ 800KB)
# ``sqlite3.Connection.backup`` defaults to ``sleep=0.25`` between
# batches.  At our buffer size that would stretch a 215 MB snapshot
# from ~3 s to ~60 s.  A tiny non-zero sleep still lets concurrent
# autopilot writers slip in between batches without paying the full
# default cost.
_BACKUP_SLEEP_INTERVAL_S = 0.05


def _iso_compact_now() -> str:
    """Filesystem-safe ISO timestamp: ``2026-05-03T03-00-00``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def _sha256_of(path: Path, *, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            buf = f.read(chunk_size)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def backup_one(src: Path, dst: Path) -> int:
    """Atomically online-backup ``src`` → ``dst``.

    Writes to a ``.tmp`` first then renames so a partially-written
    backup can never masquerade as a complete one.  Returns the
    final file's size in bytes.
    """
    if not src.exists():
        raise FileNotFoundError(f"source DB not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    src_conn = sqlite3.connect(src)
    dst_conn = sqlite3.connect(tmp)
    try:
        # ``pages=N`` lets long-running writers slip in between
        # batches; ``progress=None`` because we don't need callbacks.
        src_conn.backup(
            dst_conn,
            pages=_BUFFER_PAGES,
            sleep=_BACKUP_SLEEP_INTERVAL_S,
        )
    finally:
        dst_conn.close()
        src_conn.close()
    tmp.replace(dst)
    return dst.stat().st_size


def prune(backup_dir: Path, *, keep: int) -> list[Path]:
    """Keep the ``keep`` most recent ``knowledge-*.db`` snapshots,
    delete older ones (and their sidecar ``.sha256`` files).

    Returns the list of pruned paths.
    """
    if keep < 1:
        return []
    snapshots = sorted(
        backup_dir.glob("knowledge-*.db"),
        key=lambda p: p.name,           # ISO timestamps sort naturally
        reverse=True,
    )
    pruned: list[Path] = []
    for old in snapshots[keep:]:
        sidecar = old.with_suffix(".sha256")
        if sidecar.exists():
            sidecar.unlink()
            pruned.append(sidecar)
        old.unlink()
        pruned.append(old)
    return pruned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Snapshot knowledge.db via SQLite online backup",
    )
    parser.add_argument("--vault-dir", type=Path, default=Path.cwd())
    parser.add_argument("--keep", type=int, default=_DEFAULT_KEEP,
                        help=f"Retain N most recent snapshots (default {_DEFAULT_KEEP})")
    parser.add_argument("--no-checksum", action="store_true",
                        help="Skip the .sha256 sidecar (faster on huge DBs)")
    parser.add_argument("--no-prune", action="store_true",
                        help="Don't delete old snapshots after writing the new one")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress per-file progress output")
    args = parser.parse_args(argv)

    vault = args.vault_dir.resolve()
    src = vault / "60-Logs" / "knowledge.db"
    backup_dir = vault / "60-Logs" / "backups"

    if not src.exists():
        print(f"error: knowledge.db not found at {src}", file=sys.stderr)
        return 2

    started = time.monotonic()
    src_size = src.stat().st_size
    timestamp = _iso_compact_now()
    dst = backup_dir / f"knowledge-{timestamp}.db"

    if not args.quiet:
        print(f"source: {src}  ({src_size / 1024 / 1024:.1f} MB)")
        print(f"target: {dst}")

    try:
        written = backup_one(src, dst)
    except Exception as exc:
        print(f"error: backup failed: {exc}", file=sys.stderr)
        return 1

    elapsed_s = time.monotonic() - started

    sha = ""
    if not args.no_checksum:
        sha = _sha256_of(dst)
        sidecar = dst.with_suffix(".sha256")
        sidecar.write_text(f"{sha}  {dst.name}\n", encoding="utf-8")

    if not args.quiet:
        print(f"wrote   {written / 1024 / 1024:.1f} MB in {elapsed_s:.1f}s")
        if sha:
            print(f"sha256  {sha[:16]}…")

    pruned: list[Path] = []
    if not args.no_prune:
        pruned = prune(backup_dir, keep=args.keep)
        if pruned and not args.quiet:
            kept = sorted(
                p.name for p in backup_dir.glob("knowledge-*.db")
            )
            print(f"pruned  {len(pruned)} old files; "
                  f"{len(kept)} snapshots remain (oldest: {kept[0]})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
