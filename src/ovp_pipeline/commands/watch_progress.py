from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..runtime import VaultLayout, resolve_vault_dir
from ..runtime_processes import detect_runtime_process_lines
from ..txn import classify_run_ledgers


def _read_last_json_line(path: Path) -> dict[str, Any] | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    with path.open("rb") as fh:
        fh.seek(0, 2)
        position = fh.tell()
        buffer = bytearray()
        while position > 0:
            position -= 1
            fh.seek(position)
            byte = fh.read(1)
            if byte == b"\n" and buffer:
                break
            if byte != b"\n":
                buffer.extend(byte)
        line = bytes(reversed(buffer)).decode("utf-8").strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def detect_ovp_process_lines(vault_dir: Path) -> list[str]:
    return detect_runtime_process_lines(vault_dir)


def _count_state(layout: VaultLayout) -> dict[str, int]:
    candidates_dir = layout.evergreen_dir / "_Candidates"
    return {
        "raw": len(list(layout.raw_dir.glob("*.md"))),
        "processing": len(list(layout.processing_dir.glob("*.md"))),
        "processed": len(list(layout.processed_dir.rglob("*.md"))),
        "deep_dives": len(list((layout.vault_dir / "20-Areas").rglob("*_深度解读.md"))),
        "evergreen": len(list(layout.evergreen_dir.glob("*.md"))),
        "candidates": len(list(candidates_dir.glob("*.md"))),
        "atlas": len(list(layout.atlas_dir.glob("*.md"))),
    }


def collect_progress_snapshot(vault_dir: Path, process_lines: list[str] | None = None) -> dict[str, Any]:
    layout = VaultLayout.from_vault(vault_dir)
    process_lines = detect_ovp_process_lines(layout.vault_dir) if process_lines is None else process_lines
    classified_runs = classify_run_ledgers(layout.transactions_dir)
    active_runs = classified_runs["active"]
    stale_runs = classified_runs["stale"]

    def _safe_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return float("-inf")

    reports = sorted(
        layout.pipeline_reports_dir.glob("pipeline-report-*.md"),
        key=_safe_mtime,
        reverse=True,
    )
    reports = [path for path in reports if _safe_mtime(path) != float("-inf")]
    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vault_dir": str(layout.vault_dir),
        "counts": _count_state(layout),
        "active_processes": len(process_lines),
        "process_lines": process_lines,
        "active_transactions": active_runs,
        "active_run": active_runs[0] if active_runs else None,
        "stale_transactions": stale_runs,
        "latest_event": _read_last_json_line(layout.pipeline_log),
        "latest_report": str(reports[0]) if reports else None,
    }


def _format_delta(current: int, previous: int | None) -> str:
    if previous is None:
        return str(current)
    delta = current - previous
    if delta == 0:
        return f"{current} (+0)"
    sign = "+" if delta > 0 else ""
    return f"{current} ({sign}{delta})"


def format_progress_snapshot(snapshot: dict[str, Any], previous: dict[str, Any] | None = None) -> str:
    prev_counts = previous["counts"] if previous else {}
    lines = [
        "Vault progress",
        f"Time: {snapshot['timestamp']}",
        f"Vault: {snapshot['vault_dir']}",
        f"Running processes: {snapshot['active_processes']}",
    ]
    counts = snapshot["counts"]
    lines.extend(
        [
            f"Raw: {_format_delta(counts['raw'], prev_counts.get('raw'))}",
            f"Processing: {_format_delta(counts['processing'], prev_counts.get('processing'))}",
            f"Processed: {_format_delta(counts['processed'], prev_counts.get('processed'))}",
            f"Deep dives: {_format_delta(counts['deep_dives'], prev_counts.get('deep_dives'))}",
            f"Evergreen: {_format_delta(counts['evergreen'], prev_counts.get('evergreen'))}",
            f"Candidates: {_format_delta(counts['candidates'], prev_counts.get('candidates'))}",
            f"Atlas: {_format_delta(counts['atlas'], prev_counts.get('atlas'))}",
        ]
    )
    active_txns = snapshot["active_transactions"]
    if active_txns:
        txn = active_txns[0]
        lines.append(
            "Active txn: "
            f"{txn.get('id')} type={txn.get('type')} checkpoint={txn.get('checkpoint')} updated={txn.get('last_updated')}"
        )
        ledger = txn.get("run_ledger") or {}
        current = ledger.get("current_step") or {}
        progress_summary = current.get("progress_summary")
        if progress_summary:
            lines.append(f"Step progress: {progress_summary}")
        current_item = current.get("current_item")
        if current_item:
            lines.append(f"Current item: {current_item}")
    else:
        lines.append("Active txn: none")
    lines.append(f"Stale txns: {len(snapshot.get('stale_transactions', []))}")
    latest_event = snapshot.get("latest_event")
    if latest_event:
        event_bits = [
            latest_event.get("timestamp", "?"),
            latest_event.get("event_type", "?"),
        ]
        if latest_event.get("file"):
            event_bits.append(str(latest_event["file"]))
        elif latest_event.get("source"):
            event_bits.append(str(latest_event["source"]))
        lines.append(f"Latest event: {' | '.join(event_bits)}")
    else:
        lines.append("Latest event: none")
    if snapshot.get("latest_report"):
        lines.append(f"Latest report: {snapshot['latest_report']}")
    return "\n".join(lines)


def _is_idle(snapshot: dict[str, Any]) -> bool:
    return snapshot["active_processes"] == 0 and not snapshot["active_transactions"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Watch vault pipeline progress in the foreground")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument("--interval", type=int, default=600, help="Polling interval in seconds (default: 600)")
    parser.add_argument("--once", action="store_true", help="Print one snapshot and exit")
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Stop after N snapshots (default: run until idle or interrupted)",
    )
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    previous: dict[str, Any] | None = None
    iteration = 0

    while True:
        snapshot = collect_progress_snapshot(vault_dir)
        print(format_progress_snapshot(snapshot, previous), flush=True)
        previous = snapshot
        iteration += 1

        if args.once:
            return 0
        if args.max_iterations is not None and iteration >= args.max_iterations:
            return 0
        if _is_idle(snapshot):
            print("Monitor exiting: vault is idle.", flush=True)
            return 0
        time.sleep(max(args.interval, 1))


if __name__ == "__main__":
    raise SystemExit(main())
