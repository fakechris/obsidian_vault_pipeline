"""ovp-backfill-entities — Batch entity extraction from existing deep dives.

Scans ``20-Areas/`` for deep dives (``*_深度解读.md``), runs entity extraction
(alias matching + optional LLM NER), and populates the EntityRegistry with
candidates.  Results are also appended to ``60-Logs/entity-extractions.jsonl``
so that ``ovp-knowledge-index`` can write ``entity_mentions`` rows.

Quality gate: only entities with confidence >= threshold are registered.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _build_llm_call(vault_dir: Path) -> Any | None:
    # Don't silently swallow ImportError — this is exactly how the
    # missing-llm_client.py bug hid silently in production for months.
    # ``get_litellm_client`` itself returns None when no API key is
    # configured; that's the only graceful-fallback path we want.
    from ..llm_client import get_litellm_client

    client = get_litellm_client(vault_dir=vault_dir)
    if client:
        return client.call
    return None


def run(
    vault_dir: Path,
    *,
    dry_run: bool = False,
    limit: int = 0,
    batch_size: int = 20,
    confidence_threshold: float = 0.7,
    use_llm: bool = True,
    rate_limit_rpm: int = 0,
    inter_call_sleep_s: float = 0.0,
    force: bool = False,
) -> dict[str, Any]:
    from ..entity_extractor import make_extractor
    from ..entity_registry import EntityRegistry
    from ..identity import canonicalize_note_id

    areas_dir = vault_dir / "20-Areas"
    if not areas_dir.is_dir():
        print(f"Areas directory not found: {areas_dir}")
        return {"error": "directory_not_found"}

    md_files = sorted(areas_dir.rglob("*_深度解读.md"))
    total = len(md_files)
    print(f"Found {total} deep dives in {areas_dir}")

    if limit > 0:
        md_files = md_files[:limit]
        print(f"Limited to {limit} files")

    registry = EntityRegistry(vault_dir).load()
    before_count = len(registry)

    if dry_run:
        print("[dry-run] Would process these files:")
        for fp in md_files[:20]:
            print(f"  {fp.name}")
        if len(md_files) > 20:
            print(f"  ... and {len(md_files) - 20} more")
        return {
            "dry_run": True,
            "files_to_process": len(md_files),
            "registry_count": before_count,
        }

    llm_call = _build_llm_call(vault_dir) if use_llm else None
    if use_llm and llm_call is None:
        print("  ⚠ No LLM client available — using alias-only matching")

    extractor = make_extractor(
        vault_dir, llm_call=llm_call, confidence_threshold=confidence_threshold
    )

    extraction_log = vault_dir / "60-Logs" / "entity-extractions.jsonl"
    extraction_log.parent.mkdir(parents=True, exist_ok=True)

    # Dedup against prior runs: skip files already extracted unless --force.
    # Without this, ``ovp-backfill-entities`` would re-process every file
    # on every run (we hit this in the May 2026 history rerun: 274 files
    # got 2-4 entries in the log, doubling token cost for no new data).
    #
    # Both sides of the comparison go through ``_canonical_path`` so a log
    # entry written as a relative or non-resolved path still matches.
    def _canonical_path(p: str | Path) -> str:
        try:
            return str(Path(p).resolve(strict=False))
        except (OSError, ValueError):
            return str(p)

    already_extracted: set[str] = set()
    if not force and extraction_log.exists():
        with extraction_log.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sf = obj.get("source_file", "")
                if sf:
                    already_extracted.add(_canonical_path(sf))
        if already_extracted:
            print(f"  Skipping {len(already_extracted)} previously-extracted files (use --force to re-process)")

    processed = 0
    skipped_already_extracted = 0
    total_mentions = 0
    total_candidates = 0
    errors = 0
    t0 = time.time()

    # Rate limiting:
    #   rate_limit_rpm — hard ceiling of LLM calls per rolling 60s window
    #   inter_call_sleep_s — minimum gap between consecutive calls
    # Only relevant when use_llm=True; alias-only mode skips both.
    call_timestamps: list[float] = []

    def _throttle() -> None:
        if not use_llm or llm_call is None:
            return
        if inter_call_sleep_s > 0:
            time.sleep(inter_call_sleep_s)
        if rate_limit_rpm > 0:
            now = time.time()
            cutoff = now - 60.0
            call_timestamps[:] = [t for t in call_timestamps if t > cutoff]
            if len(call_timestamps) >= rate_limit_rpm:
                wait = 60.0 - (now - call_timestamps[0]) + 0.05
                if wait > 0:
                    time.sleep(wait)
            call_timestamps.append(time.time())

    for i, fpath in enumerate(md_files):
        if _canonical_path(fpath) in already_extracted:
            skipped_already_extracted += 1
            continue
        _throttle()
        try:
            extraction = extractor.extract_entities_from_file(fpath)
        except Exception as exc:
            print(f"  [{i+1}/{len(md_files)}] ERROR {fpath.name}: {exc}")
            errors += 1
            continue

        high_conf = [
            m for m in extraction.mentions if m.confidence >= confidence_threshold
        ]
        total_mentions += len(high_conf)
        total_candidates += extraction.candidates_created

        if high_conf:
            record = {
                "source_slug": canonicalize_note_id(fpath.stem),
                "source_file": str(fpath),
                "mentions": [m.to_dict() for m in high_conf],
                "backfilled_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(extraction_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        processed += 1

        if (i + 1) % batch_size == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            print(
                f"  [{i+1}/{len(md_files)}] "
                f"mentions={total_mentions} candidates={total_candidates} "
                f"errors={errors} rate={rate:.1f}/s"
            )

    extractor.registry.save()
    after_count = len(extractor.registry)

    elapsed = time.time() - t0
    summary = {
        "files_total": total,
        "files_processed": processed,
        "files_skipped_already_extracted": skipped_already_extracted,
        "errors": errors,
        "mentions_extracted": total_mentions,
        "candidates_created": total_candidates,
        "registry_before": before_count,
        "registry_after": after_count,
        "new_entities": after_count - before_count,
        "elapsed_seconds": round(elapsed, 1),
        "confidence_threshold": confidence_threshold,
    }

    print(f"\nDone.")
    print(f"  Files processed: {processed}/{total}")
    print(f"  Mentions extracted: {total_mentions}")
    print(f"  New entity candidates: {after_count - before_count}")
    print(f"  Registry: {before_count} → {after_count}")
    print(f"  Errors: {errors}")
    print(f"  Elapsed: {elapsed:.1f}s")

    log_path = vault_dir / "60-Logs" / "pipeline.jsonl"
    try:
        from ..auto_moc_updater import PipelineLogger

        PipelineLogger(log_path).log("entity_backfill_summary", summary)
    except Exception:
        pass

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill entity extraction from existing deep dives"
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=Path.cwd(),
        help="Vault root directory (default: cwd)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without modifying files")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of files to process (0=all)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Print progress every N files",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.7,
        help="Min confidence to register a mention (default: 0.7)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM NER, use alias-only matching",
    )
    parser.add_argument(
        "--rate-limit-rpm",
        type=int,
        default=0,
        help="Hard ceiling for LLM calls per rolling 60s window (0=unlimited). "
             "Set to your provider's RPM budget to avoid 429 errors.",
    )
    parser.add_argument(
        "--inter-call-sleep",
        type=float,
        default=0.0,
        help="Seconds to sleep between consecutive LLM calls (default 0). "
             "Useful for providers with strict per-second limits.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-process files that already have entries in the extraction "
             "log.  By default, dedup against the log so reruns only "
             "process new files.",
    )
    args = parser.parse_args()
    result = run(
        args.vault_dir,
        dry_run=args.dry_run,
        limit=args.limit,
        batch_size=args.batch_size,
        confidence_threshold=args.confidence_threshold,
        use_llm=not args.no_llm,
        rate_limit_rpm=args.rate_limit_rpm,
        inter_call_sleep_s=args.inter_call_sleep,
        force=args.force,
    )
    if result.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
