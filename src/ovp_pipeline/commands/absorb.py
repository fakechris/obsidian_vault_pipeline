from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import time

from ..auto_evergreen_extractor import run_absorb_workflow
from ..auto_article_processor import AutoArticleProcessor, PipelineLogger, TransactionManager
from ..clippings_processor import ClippingsProcessor
from ..evidence import build_evidence_payload
from ..runtime import VaultLayout, resolve_vault_dir
from ..source_lifecycle import clipping_raw_name

ISO_DATE_PREFIX_LEN = 10


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def _path_needs_source_lifecycle(layout: VaultLayout, path: Path) -> bool:
    return any(
        _is_under(path, root)
        for root in (
            layout.clippings_dir,
            layout.raw_dir,
            layout.processing_dir,
            layout.processed_dir,
        )
    )


def _expand_markdown_sources(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(candidate for candidate in path.rglob("*.md") if candidate.is_file())
    return [path]


def _expand_deep_dive_targets(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(candidate for candidate in path.glob("*_深度解读.md") if candidate.is_file())
    return [path]


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        key = path.resolve(strict=False)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _unique_child(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        next_candidate = directory / f"{stem}-{counter}{suffix}"
        if not next_candidate.exists():
            return next_candidate
        counter += 1


def _unique_child_with_reservations(directory: Path, name: str, reserved: set[Path]) -> Path:
    candidate = directory / name
    if not candidate.exists() and candidate.resolve(strict=False) not in reserved:
        reserved.add(candidate.resolve(strict=False))
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        next_candidate = directory / f"{stem}-{counter}{suffix}"
        if not next_candidate.exists() and next_candidate.resolve(strict=False) not in reserved:
            reserved.add(next_candidate.resolve(strict=False))
            return next_candidate
        counter += 1


def _clipping_raw_name(
    processor: ClippingsProcessor,
    source: Path,
    *,
    when: datetime | None = None,
) -> str:
    return clipping_raw_name(source, processor.sanitize_filename, when=when)


def _move_clipping_to_raw(
    layout: VaultLayout,
    processor: ClippingsProcessor,
    source: Path,
    *,
    settle_timeout_s: float = 5.0,
) -> Path:
    new_name = _clipping_raw_name(processor, source)
    destination = _unique_child(layout.raw_dir, new_name)
    if not processor.obsidian_move(source, layout.raw_dir, destination.name):
        raise RuntimeError(f"failed to move clipping into raw intake: {source}")
    deadline = time.monotonic() + settle_timeout_s
    while not destination.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    if not destination.exists():
        if source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        else:
            raise FileNotFoundError(
                f"obsidian move reported success but destination did not appear: {destination}"
            )
    return destination


def _source_lifecycle_zone(layout: VaultLayout, source: Path) -> str:
    if _is_under(source, layout.clippings_dir):
        return "clippings"
    if _is_under(source, layout.raw_dir):
        return "raw"
    if _is_under(source, layout.processing_dir):
        return "processing"
    if _is_under(source, layout.processed_dir):
        return "processed"
    return "outside_source_lifecycle"


def _preview_clipping_raw_destination(
    layout: VaultLayout,
    processor: ClippingsProcessor,
    source: Path,
    reserved: set[Path],
    *,
    when: datetime,
) -> Path:
    new_name = _clipping_raw_name(processor, source, when=when)
    return _unique_child_with_reservations(layout.raw_dir, new_name, reserved)


def _archive_preview_path(layout: VaultLayout, source: Path) -> Path:
    match = source.name[:ISO_DATE_PREFIX_LEN]
    try:
        source_date = datetime.strptime(match, "%Y-%m-%d")
    except ValueError:
        source_date = datetime.now()
    return layout.processed_month_dir(source_date) / source.name


def build_source_lifecycle_routing_preview(layout: VaultLayout, targets: list[Path]) -> dict:
    """Build a non-mutating preview of source lifecycle routing for absorb."""
    logger = PipelineLogger(layout.pipeline_log)
    txn = TransactionManager(layout.transactions_dir)
    clippings = ClippingsProcessor(layout.vault_dir, logger, txn)
    preview_time = datetime.now()
    items: list[dict] = []
    reserved_raw_destinations: set[Path] = set()
    sources = _dedupe_paths(
        [source for target in targets for source in _expand_markdown_sources(target)]
    )
    for source in sources:
        zone = _source_lifecycle_zone(layout, source)
        planned_actions: list[dict[str, str]] = []
        route = "unsupported_source_lifecycle_route"
        reason = "source is outside the supported source lifecycle directories"
        working_source = source

        if zone == "clippings":
            raw_target = _preview_clipping_raw_destination(
                layout,
                clippings,
                source,
                reserved_raw_destinations,
                when=preview_time,
            )
            staged_target = layout.processing_dir / raw_target.name
            route = "clippings_to_raw_to_processing_to_deep_dive_absorb"
            reason = "clipping must be finalized into Raw before article interpretation and absorb"
            planned_actions.extend(
                [
                    {"action": "move_to_raw", "target": str(raw_target)},
                    {"action": "stage_for_processing", "target": str(staged_target)},
                ]
            )
            working_source = staged_target
        elif zone == "raw":
            staged_target = layout.processing_dir / source.name
            route = "raw_to_processing_to_deep_dive_absorb"
            reason = "raw source must be staged before article interpretation and absorb"
            planned_actions.append({"action": "stage_for_processing", "target": str(staged_target)})
            working_source = staged_target
        elif zone == "processing":
            route = "processing_to_deep_dive_absorb"
            reason = "processing source can be interpreted directly before absorb"
        elif zone == "processed":
            route = "processed_to_deep_dive_absorb"
            reason = "processed source can be re-interpreted directly before absorb"

        if zone in {"clippings", "raw", "processing", "processed"}:
            planned_actions.append({"action": "process_article", "target": "generated_deep_dive"})
            if zone in {"clippings", "raw", "processing"}:
                planned_actions.append(
                    {
                        "action": "archive_source_to_processed",
                        "target": str(_archive_preview_path(layout, working_source)),
                    }
                )
            planned_actions.append(
                {"action": "absorb_generated_deep_dive", "target": "generated_deep_dive"}
            )

        items.append(
            {
                "source": str(source),
                "source_zone": zone,
                "route": route,
                "processor": "auto_article_processor",
                "will_mutate_on_execute": zone in {"clippings", "raw", "processing", "processed"},
                "planned_actions": planned_actions,
                "reason": reason,
            }
        )

    return {
        "preview_schema_version": 1,
        "items": items,
    }


def _record_source_lifecycle_failure(
    logger: PipelineLogger,
    failures: list[dict[str, str]] | None,
    *,
    source: Path,
    stage: str,
    exc: Exception,
) -> None:
    failure = {
        "source": str(source),
        "stage": stage,
        "error": str(exc),
    }
    if failures is not None:
        failures.append(failure)
    logger.log("source_lifecycle_finalize_error", failure)


def _safe_archive_source_to_processed(
    processor: AutoArticleProcessor,
    logger: PipelineLogger,
    failures: list[dict[str, str]] | None,
    source: Path,
) -> None:
    try:
        processor._archive_source_to_processed(source)
    except Exception as exc:
        _record_source_lifecycle_failure(
            logger,
            failures,
            source=source,
            stage="archive_to_processed",
            exc=exc,
        )


def _safe_restore_source_to_raw(
    processor: AutoArticleProcessor,
    logger: PipelineLogger,
    failures: list[dict[str, str]] | None,
    source: Path,
) -> None:
    try:
        processor._restore_source_to_raw(source)
    except Exception as exc:
        _record_source_lifecycle_failure(
            logger,
            failures,
            source=source,
            stage="restore_to_raw",
            exc=exc,
        )


def run_source_lifecycle_for_absorb_targets(
    vault_dir: Path,
    targets: list[Path],
    *,
    dry_run: bool,
    failures: list[dict[str, str]] | None = None,
) -> list[Path]:
    layout = VaultLayout.from_vault(vault_dir)
    logger = PipelineLogger(layout.pipeline_log)
    txn = TransactionManager(layout.transactions_dir)
    clippings = ClippingsProcessor(layout.vault_dir, logger, txn)
    processor = AutoArticleProcessor(layout.vault_dir, logger, txn)
    if not dry_run:
        processor.init_llm()

    deep_dive_targets: list[Path] = []
    for target in targets:
        for source in _expand_markdown_sources(target):
            working_source = source
            if _is_under(source, layout.clippings_dir):
                if dry_run:
                    continue
                working_source = _move_clipping_to_raw(layout, clippings, source)

            if dry_run:
                continue

            if _is_under(working_source, layout.raw_dir):
                working_source = processor._stage_source_for_processing(working_source)

            result = processor.process_single_file(working_source, dry_run=False)
            if result.get("status") == "completed" and result.get("output_path"):
                deep_dive_targets.append(Path(str(result["output_path"])))
                if _is_under(working_source, layout.processing_dir):
                    _safe_archive_source_to_processed(processor, logger, failures, working_source)
            elif _is_under(working_source, layout.processing_dir) and working_source.exists():
                _safe_restore_source_to_raw(processor, logger, failures, working_source)

    return deep_dive_targets


def _merge_absorb_payloads(payloads: list[dict]) -> dict:
    summary_keys = (
        "files_processed",
        "concepts_extracted",
        "candidates_added",
        "concepts_promoted",
        "concepts_created",
        "concepts_skipped",
        "errors",
    )
    return {
        "mode": "absorb",
        "dry_run": False,
        "summary": {
            key: sum(int(payload.get("summary", {}).get(key, 0)) for payload in payloads)
            for key in summary_keys
        },
        "results": [
            result
            for payload in payloads
            for result in payload.get("results", [])
        ],
    }


def _run_absorb_for_targets(
    vault_dir: Path,
    targets: list[Path],
    *,
    auto_promote: bool,
    promote_threshold: int,
) -> dict:
    if len(targets) == 1:
        return run_absorb_workflow(
            vault_dir,
            file_path=targets[0],
            dry_run=False,
            auto_promote=auto_promote,
            promote_threshold=promote_threshold,
        )
    return _merge_absorb_payloads(
        [
            run_absorb_workflow(
                vault_dir,
                file_path=target,
                dry_run=False,
                auto_promote=auto_promote,
                promote_threshold=promote_threshold,
            )
            for target in targets
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Absorb interpreted notes into the knowledge layer")
    parser.add_argument("--file", type=Path, help="Absorb one deep-dive file")
    parser.add_argument("--dir", type=Path, help="Absorb a directory of deep-dive files")
    parser.add_argument("--recent", type=int, help="Absorb recent N days of deep-dives")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument("--dry-run", action="store_true", help="Show absorb scope without mutating state")
    parser.add_argument("--auto-promote", action="store_true", help="Allow automatic promotion when threshold is met")
    parser.add_argument("--promote-threshold", type=int, default=3, help="Promotion threshold for auto-promote")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    layout = VaultLayout.from_vault(vault_dir)
    source_lifecycle_targets = []
    direct_absorb_targets = []
    if args.file:
        if _path_needs_source_lifecycle(layout, args.file):
            source_lifecycle_targets.extend(_expand_markdown_sources(args.file))
        else:
            direct_absorb_targets.append(args.file)
    if args.dir:
        if _path_needs_source_lifecycle(layout, args.dir):
            source_lifecycle_targets.extend(_expand_markdown_sources(args.dir))
        else:
            direct_absorb_targets.extend(_expand_deep_dive_targets(args.dir))
    source_lifecycle_targets = _dedupe_paths(source_lifecycle_targets)
    direct_absorb_targets = _dedupe_paths(direct_absorb_targets)
    routing_preview = build_source_lifecycle_routing_preview(layout, source_lifecycle_targets)
    payload = {
        "mode": "absorb",
        "vault_dir": str(vault_dir),
        "file": str(args.file) if args.file else None,
        "dir": str(args.dir) if args.dir else None,
        "recent": args.recent,
        "dry_run": args.dry_run,
        "auto_promote": args.auto_promote,
        "promote_threshold": args.promote_threshold,
        "source_lifecycle": {
            "required": bool(source_lifecycle_targets),
            "source_targets": [str(target) for target in source_lifecycle_targets],
            "routing_preview": routing_preview,
            "absorb_targets": [],
            "failures": [],
        },
    }

    if args.dry_run:
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("absorb dry-run")
        return 0

    absorb_targets = list(direct_absorb_targets)
    if source_lifecycle_targets:
        lifecycle_failures: list[dict[str, str]] = []
        lifecycle_targets = run_source_lifecycle_for_absorb_targets(
            vault_dir,
            source_lifecycle_targets,
            dry_run=False,
            failures=lifecycle_failures,
        )
        payload["source_lifecycle"]["absorb_targets"] = [str(target) for target in lifecycle_targets]
        payload["source_lifecycle"]["failures"] = lifecycle_failures
        absorb_targets.extend(lifecycle_targets)
        absorb_targets = _dedupe_paths(absorb_targets)
        if not absorb_targets:
            workflow_payload = {
                "mode": "absorb",
                "dry_run": False,
                "source_lifecycle": payload["source_lifecycle"],
                "summary": {
                    "files_processed": 0,
                    "concepts_extracted": 0,
                    "candidates_added": 0,
                    "concepts_promoted": 0,
                    "concepts_created": 0,
                    "concepts_skipped": 0,
                    "errors": 1,
                },
                "results": [],
                "error": "source lifecycle produced no absorb targets",
            }
            if args.json:
                print(json.dumps(workflow_payload, ensure_ascii=False, indent=2))
            else:
                print("error: source lifecycle produced no absorb targets")
            return 1

    file_path = args.file
    directory = args.dir
    recent = args.recent
    if source_lifecycle_targets or (args.file and args.dir):
        file_path = None
        directory = None
        recent = None
        workflow_payload = _run_absorb_for_targets(
            vault_dir,
            absorb_targets,
            auto_promote=args.auto_promote,
            promote_threshold=args.promote_threshold,
        )
    else:
        workflow_payload = run_absorb_workflow(
            vault_dir,
            file_path=file_path,
            directory=directory,
            recent=recent,
            dry_run=False,
            auto_promote=args.auto_promote,
            promote_threshold=args.promote_threshold,
        )
    if source_lifecycle_targets:
        workflow_payload["source_lifecycle"] = payload["source_lifecycle"]
    mentions = [
        str(concept.get("name") or "")
        for result in workflow_payload.get("results", [])
        for concept in result.get("concepts", [])
        if concept.get("name")
    ]
    workflow_payload["evidence"] = build_evidence_payload(
        vault_dir,
        mentions=mentions[:10],
        limit=5,
    )

    if args.json:
        print(json.dumps(workflow_payload, ensure_ascii=False, indent=2))
    else:
        summary = workflow_payload["summary"]
        print("absorb complete")
        print(f"files processed: {summary['files_processed']}")
        print(f"concepts extracted: {summary['concepts_extracted']}")
        print(f"candidates added: {summary['candidates_added']}")
        if args.auto_promote:
            print(f"concepts promoted: {summary['concepts_promoted']}")
            print(f"files created: {summary['concepts_created']}")
        print(f"concepts skipped: {summary['concepts_skipped']}")
        if summary["errors"]:
            print(f"errors: {summary['errors']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
