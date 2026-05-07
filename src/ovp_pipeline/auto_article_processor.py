#!/usr/bin/env python3
"""
Auto Article Processor - 全自动文章深度解读生成器
基于LLM API自动生成6维度深度解读

Usage:
    python3 auto_article_processor.py --input urls.txt
    python3 auto_article_processor.py --single https://example.com/article
    python3 auto_article_processor.py --process-inbox  # 处理50-Inbox/01-Raw/

Features:
    - WebFetch自动获取文章内容
    - 6维度深度解读生成
    - 自动分类（AI/工具/投资/编程）
    - 幂等处理（跳过已处理）
    - 统一日志记录
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .runtime import VaultLayout, resolve_vault_dir
except ImportError:  # pragma: no cover - script mode fallback
    from runtime import VaultLayout, resolve_vault_dir

try:
    from .source_lifecycle import (
        archive_pinboard_source,
        clipping_raw_name,
        is_under as lifecycle_is_under,
        unique_child as lifecycle_unique_child,
    )
    from .processing_backups import cleanup_processing_backup_for_archived_source
except ImportError:  # pragma: no cover - script mode fallback
    from source_lifecycle import (  # type: ignore
        archive_pinboard_source,
        clipping_raw_name,
        is_under as lifecycle_is_under,
        unique_child as lifecycle_unique_child,
    )
    from processing_backups import cleanup_processing_backup_for_archived_source  # type: ignore

try:
    from .txn import (
        build_transaction_payload,
        heartbeat_transaction,
        mark_transaction_completed,
        mark_transaction_failed,
        update_transaction_step,
    )
except ImportError:  # pragma: no cover - script mode fallback
    from txn import (  # type: ignore
        build_transaction_payload,
        heartbeat_transaction,
        mark_transaction_completed,
        mark_transaction_failed,
        update_transaction_step,
    )

try:
    from .source_dedup import (
        build_active_url_index,
        extract_source_url,
        find_existing_by_url,
        read_file_head,
    )
except ImportError:  # pragma: no cover - script mode fallback
    from source_dedup import (  # type: ignore
        build_active_url_index,
        extract_source_url,
        find_existing_by_url,
        read_file_head,
    )

# 自动加载 .env 文件（尝试多个位置）
def _load_env_files():
    """加载 .env 文件，尝试多个位置"""
    env_paths = [
        Path.cwd() / ".env",  # 当前工作目录（优先）
        Path(__file__).parent.parent.parent / ".env",  # 脚本相对路径
    ]
    for env_path in env_paths:
        if env_path.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(dotenv_path=env_path, override=True)
                return env_path
            except ImportError:
                pass
    return None

_LOADED_ENV = _load_env_files()

# 确定 VAULT_DIR（优先使用当前工作目录）
VAULT_DIR = resolve_vault_dir()
DEFAULT_LAYOUT = VaultLayout.from_vault(VAULT_DIR)

# ========== 配置 ==========
RAW_DIR = DEFAULT_LAYOUT.raw_dir
PROCESSED_DIR = DEFAULT_LAYOUT.processed_dir
MANIFEST_FILE = DEFAULT_LAYOUT.vault_dir / "50-Inbox" / ".manifest.json"
LOG_FILE = DEFAULT_LAYOUT.pipeline_log
TXN_DIR = DEFAULT_LAYOUT.transactions_dir
EVERGREEN_DIR = DEFAULT_LAYOUT.evergreen_dir


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PipelineLogger:
    """统一过程日志记录器"""

    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.session_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()}"

    def log(self, event_type: str, data: dict[str, Any]):
        entry = {
            "timestamp": _utc_timestamp(),
            "session_id": self.session_id,
            "event_type": event_type,
            **data
        }
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class TransactionManager:
    """事务管理器"""

    def __init__(self, txn_dir: Path):
        self.txn_dir = txn_dir

    def _read(self, txn_id: str) -> dict[str, Any] | None:
        txn_file = self.txn_dir / f"{txn_id}.json"
        if not txn_file.exists():
            return None
        return json.loads(txn_file.read_text(encoding="utf-8"))

    def _write(self, txn_id: str, txn_data: dict[str, Any]) -> None:
        txn_file = self.txn_dir / f"{txn_id}.json"
        txn_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = txn_file.with_suffix(txn_file.suffix + ".tmp")
        temp_file.write_text(json.dumps(txn_data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_file.replace(txn_file)

    def start(
        self,
        workflow_type: str,
        description: str,
        *,
        pack_name: str | None = None,
        workflow_profile: str | None = None,
        planned_steps: list[str] | None = None,
    ) -> str:
        txn_id = f"txn-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()[:8]}"
        txn_data = build_transaction_payload(
            txn_id,
            workflow_type,
            description,
            pack_name=pack_name,
            workflow_profile=workflow_profile,
            planned_steps=planned_steps,
        )
        self._write(txn_id, txn_data)

        return txn_id

    def step(self, txn_id: str, step_name: str, status: str, output: str = "", **progress_kwargs: Any):
        txn_data = self._read(txn_id)
        if txn_data is None:
            return
        update_transaction_step(txn_data, step_name, status, output=output, **progress_kwargs)
        self._write(txn_id, txn_data)

    def heartbeat(self, txn_id: str, *, step_name: str | None = None, **progress_kwargs: Any):
        txn_data = self._read(txn_id)
        if txn_data is None:
            return
        heartbeat_transaction(txn_data, step_name=step_name, **progress_kwargs)
        self._write(txn_id, txn_data)

    def complete(self, txn_id: str):
        txn_data = self._read(txn_id)
        if txn_data is None:
            return
        mark_transaction_completed(txn_data)
        self._write(txn_id, txn_data)

    def fail(self, txn_id: str, reason: str):
        txn_data = self._read(txn_id)
        if txn_data is None:
            return
        mark_transaction_failed(txn_data, reason)
        self._write(txn_id, txn_data)


class AutoArticleProcessor:
    """Article intake processor.

    Post-BL-029 the legacy 13-section LLM-driven deep-dive layer is
    gone.  This processor now does the irreducible intake side-
    effects only — image download + frontmatter parse for the audit
    event — and delegates knowledge extraction to
    ``auto_evergreen_extractor`` running v2 absorb on the raw.  This
    matches the flow ``auto_github_processor`` already uses
    post-BL-066.

    Lifecycle moves (``01-Raw → 02-Processing → 03-Processed``)
    happen in ``process_single_source`` / ``process_inbox``.  URL
    dedup lives at every intake site (see
    :class:`AutoArticleProcessor._check_url_dedup` and
    :mod:`ovp_pipeline.source_dedup`).
    """

    def __init__(
        self,
        vault_dir: Path,
        logger: PipelineLogger,
        txn: TransactionManager,
    ):
        self.layout = VaultLayout.from_vault(vault_dir)
        self.vault_dir = self.layout.vault_dir
        self.raw_dir = self.layout.raw_dir
        self.processing_dir = self.layout.processing_dir
        self.processed_dir = self.layout.processed_dir
        self.logger = logger
        self.txn = txn

    def _extract_source_date(self, file_path: Path) -> datetime:
        match = re.match(r"^(\d{4}-\d{2}-\d{2})_", file_path.name)
        if match:
            try:
                return datetime.strptime(match.group(1), "%Y-%m-%d")
            except ValueError:
                pass
        return datetime.now()

    def _move_source_file(self, source: Path, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        return source.rename(destination)

    def _stage_source_for_processing(self, file_path: Path) -> Path:
        self.processing_dir.mkdir(parents=True, exist_ok=True)
        if file_path.parent == self.processing_dir:
            return file_path
        staged_path = self.processing_dir / file_path.name
        staged = self._move_source_file(file_path, staged_path)
        self.logger.log("source_staged_for_processing", {
            "source": str(file_path),
            "staged": str(staged),
        })
        return staged

    def _restore_source_to_raw(self, file_path: Path) -> Path:
        restored = self._move_source_file(file_path, self.raw_dir / file_path.name)
        self.logger.log("source_restored_to_raw", {
            "source": str(file_path),
            "restored": str(restored),
        })
        return restored

    def _archive_source_to_processed(self, file_path: Path) -> Path:
        destination = self.layout.processed_month_dir(self._extract_source_date(file_path)) / file_path.name
        archived = self._move_source_file(file_path, destination)
        backup_cleanup = cleanup_processing_backup_for_archived_source(file_path, archived)
        self.logger.log("source_archived_to_processed", {
            "source": str(file_path),
            "archived": str(archived),
            "backup_cleanup": {
                "backup": str(backup_cleanup.backup_path),
                "ok": backup_cleanup.ok,
                "reason": backup_cleanup.reason,
            },
        })
        return archived

    def _source_lifecycle_zone(self, source: Path) -> str:
        if lifecycle_is_under(source, self.layout.clippings_dir):
            return "clippings"
        if lifecycle_is_under(source, self.layout.pinboard_dir):
            return "pinboard"
        if lifecycle_is_under(source, self.raw_dir):
            return "raw"
        if lifecycle_is_under(source, self.processing_dir):
            return "processing"
        if lifecycle_is_under(source, self.processed_dir):
            return "processed"
        return "outside_source_lifecycle"

    def _move_clipping_to_raw(self, source: Path) -> Path:
        try:
            from .clippings_processor import ClippingsProcessor
        except ImportError:  # pragma: no cover - script mode fallback
            from clippings_processor import ClippingsProcessor

        clippings = ClippingsProcessor(self.vault_dir, self.logger, self.txn)
        new_name = clipping_raw_name(source, clippings.sanitize_filename)
        destination = lifecycle_unique_child(self.raw_dir, new_name)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        if not clippings.obsidian_move(source, self.raw_dir, destination.name):
            raise RuntimeError(f"failed to move clipping into raw intake: {source}")
        if not destination.exists():
            # Obsidian CLI can report success before the destination is visible;
            # keep the fallback so a completed move cannot leave Clippings live.
            if source.exists():
                source.rename(destination)
            else:
                raise FileNotFoundError(
                    f"obsidian move reported success but destination did not appear: {destination}"
                )
        return destination

    def _finalize_lifecycle_source(self, working_path: Path, result: dict, dry_run: bool = False) -> Path | None:
        if dry_run:
            return None
        # C2: ``intake_only`` is a success — raw was parsed, no
        # deep-dive was generated, and the file should still be
        # archived to 03-Processed so absorb v2 can pick it up.
        success_statuses = {"completed", "intake_only"}
        if lifecycle_is_under(working_path, self.layout.pinboard_dir):
            if result["status"] in success_statuses and working_path.exists():
                archived = archive_pinboard_source(self.layout, working_path)
                result["source_path"] = str(archived)
                return archived
            return None
        if not lifecycle_is_under(working_path, self.processing_dir):
            return None
        if result["status"] in success_statuses:
            archived = self._archive_source_to_processed(working_path)
            result["source_path"] = str(archived)
            return archived
        if working_path.exists():
            restored = self._restore_source_to_raw(working_path)
            result["source_path"] = str(restored)
            return restored
        return None

    def _check_url_dedup(
        self,
        source: Path,
        *,
        index: dict | None = None,
        stage: str = "single_source",
    ) -> dict | None:
        """Return a ``skipped_dedup`` result dict when ``source``'s URL
        already appears anywhere in the active staging chain
        (``Clippings/`` + 4 stages under ``50-Inbox/``); ``None``
        otherwise.

        Pre-2026-05-07 the gate scanned ``50-Inbox/03-Processed/`` only
        and was wired to :meth:`process_single_source` only.  That
        narrow design left the Clippings → ``process_inbox`` flow
        without any URL guard — re-clipping the same article via a
        Reader save produced 12 fresh duplicates per BL-058 v0.12.0
        run.  Now: gate is invoked from both :meth:`process_inbox`
        and :meth:`process_single_source`, and uses the global
        active-staging index from :func:`source_dedup.build_active_url_index`.

        ``index`` may be passed in by callers iterating many files
        (``process_inbox``) so we don't rescan the vault per-file.

        ``stage`` is recorded in the audit event so we can tell which
        intake site fired the gate.

        Note: ``70-Archive/`` is excluded by design.  A user who
        archived a prior copy and explicitly wants to re-process the
        URL gets through.
        """
        try:
            text = read_file_head(source)
        except OSError:
            return None
        url = extract_source_url(text)
        if not url:
            return None
        if index is None:
            index = build_active_url_index(self.vault_dir)
        existing = find_existing_by_url(self.vault_dir, url, index=index)
        if existing is None:
            return None
        # Don't flag a self-match — the same file already living in
        # the staging chain isn't a dup of itself.
        try:
            if existing.resolve() == source.resolve():
                return None
        except OSError:
            pass
        self.logger.log("source_dedup_skipped", {
            "source": str(source),
            "url": url,
            "existing": str(existing),
            "stage": stage,
        })
        return {
            "file": str(source),
            "status": "skipped_dedup",
            "output_path": None,
            "tokens_used": 0,
            "images_downloaded": 0,
            "error": None,
            "dedup": {
                "url": url,
                "existing": str(existing.relative_to(self.vault_dir)),
                "stage": stage,
            },
        }

    def process_single_source(self, file_path: Path, dry_run: bool = False) -> dict:
        """Process one source while honoring the same lifecycle as inbox runs."""
        source = Path(file_path)
        zone = self._source_lifecycle_zone(source)

        # URL-dedup gate runs BEFORE any filesystem moves AND before
        # the dry-run early-return — the gate is read-only so it's
        # safe to evaluate in preview mode, and surfacing the skip
        # verdict in dry-run is what makes the preview honest.  A
        # detected duplicate leaves ``50-Inbox/01-Raw`` (or wherever
        # the source currently sits) untouched.
        dedup_result = self._check_url_dedup(source)
        if dedup_result is not None:
            dedup_result["source_lifecycle"] = {"zone": zone, "would_move": False}
            return dedup_result

        if dry_run:
            result = {
                "file": str(source),
                "status": "dry_run",
                "output_path": None,
                "tokens_used": 0,
                "images_downloaded": 0,
                "error": None,
                "source_lifecycle": {
                    "zone": zone,
                    "would_move": zone in {"clippings", "raw", "processing", "pinboard"},
                },
            }
            return result

        working_path = source
        try:
            if zone == "clippings":
                working_path = self._move_clipping_to_raw(source)

            if lifecycle_is_under(working_path, self.raw_dir):
                working_path = self._stage_source_for_processing(working_path)

            result = self.process_single_file(working_path, dry_run=False)
            result["source_lifecycle"] = {
                "zone": zone,
                "working_path": str(working_path),
            }
            self._finalize_lifecycle_source(working_path, result, dry_run=False)
            return result
        except Exception as e:
            result = {
                "file": str(source),
                "status": "error",
                "output_path": None,
                "tokens_used": 0,
                "images_downloaded": 0,
                "error": str(e),
                "source_lifecycle": {
                    "zone": zone,
                    "working_path": str(working_path),
                },
            }
            if lifecycle_is_under(working_path, self.processing_dir) and working_path.exists():
                self._finalize_lifecycle_source(working_path, result, dry_run=False)
            self.logger.log("article_error", {"file": str(source), "error": str(e)})
            return result

    def parse_raw_file(self, file_path: Path) -> dict[str, Any]:
        """解析Raw文件，提取元数据和内容"""
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 解析frontmatter
        frontmatter = {}
        body = content

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                fm_text = parts[1].strip()
                body = parts[2].strip()

                # 简单解析YAML
                for line in fm_text.split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        frontmatter[key.strip()] = value.strip().strip('"').strip("'")

        return {
            "frontmatter": frontmatter,
            "body": body,
            "title": frontmatter.get("title", file_path.stem),
            "author": frontmatter.get("author", "unknown"),
            "source": frontmatter.get("source", ""),
            "date": frontmatter.get("date", datetime.now().strftime("%Y-%m-%d")),
            "tags": frontmatter.get("tags", ""),
        }

    def process_single_file(self, file_path: Path, dry_run: bool = False) -> dict:
        """Intake-only processing for one source.

        Post-BL-029 the legacy 13-section LLM-driven deep-dive layer
        is gone — ``auto_evergreen_extractor`` running v2 absorb on
        the raw produces strictly better units (specifics-preserving,
        no abstraction inflation).  This method now does the
        irreducible side-effects only:

          1. Download remote images so the raw is self-contained
             when archived to ``03-Processed``.
          2. Parse frontmatter (so the audit event carries
             ``source_url``) but write nothing back.

        Lifecycle moves (``01-Raw → 02-Processing → 03-Processed``)
        happen in ``process_single_source`` / ``process_inbox`` —
        this method just returns ``intake_only``.
        """
        result = {
            "file": str(file_path),
            "status": "pending",
            "output_path": None,
            "tokens_used": 0,
            "images_downloaded": 0,
            "error": None,
        }

        try:
            # Step 1: download remote images (best-effort; failures
            # don't block the lifecycle move).
            from .image_downloader import ImageDownloader
            image_downloader = ImageDownloader(self.vault_dir)
            try:
                downloaded_images = image_downloader.process_file(file_path, backup=True)
                result["images_downloaded"] = len(downloaded_images)
                if downloaded_images:
                    self.logger.log("images_downloaded", {
                        "file": str(file_path.name),
                        "count": len(downloaded_images),
                        "images": downloaded_images,
                    })
            except Exception as img_err:
                self.logger.log("image_download_error", {"file": str(file_path), "error": str(img_err)})

            # Step 2: parse frontmatter so the audit event carries
            # the canonical ``source`` URL.  We don't rewrite
            # anything here — absorb v2 reads the raw directly.
            file_data = self.parse_raw_file(file_path)

            if dry_run:
                result["status"] = "dry_run"
                return result

            result["status"] = "intake_only"
            self.logger.log("article_intake_only", {
                "file": str(file_path.name),
                "source_url": str(file_data.get("source") or ""),
            })

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            self.logger.log("article_error", {"file": str(file_path), "error": str(e)})

        return result

    def process_inbox(self, dry_run: bool = False, batch_size: int | None = None, txn_id: str | None = None) -> dict:
        """处理整个inbox"""
        results = {
            "total": 0,
            "queued_total": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "total_tokens": 0,
            "files": []
        }

        if not self.raw_dir.exists():
            self.raw_dir.mkdir(parents=True, exist_ok=True)

        self.processing_dir.mkdir(parents=True, exist_ok=True)

        processing_files = sorted(self.processing_dir.glob("*.md"))
        raw_files = sorted(self.raw_dir.glob("*.md"))
        files = processing_files + raw_files
        results["queued_total"] = len(files)

        if batch_size:
            files = files[:batch_size]
        work_units_total = len(files)
        results["total"] = work_units_total

        if txn_id:
            self.txn.step(
                txn_id,
                "process",
                "in_progress",
                "Processing articles",
                progress_mode="counted",
                work_units_total=work_units_total,
                work_units_done=0,
                work_units_failed=0,
                progress_summary=f"0/{work_units_total} articles processed",
            )

        # Build the URL→path index once for the batch; the gate
        # checks every queued file against it before any
        # filesystem move.  Without this, a re-clip of an already-
        # processed URL silently re-runs the entire pipeline.
        active_index = build_active_url_index(self.vault_dir)

        for position, file_path in enumerate(files, start=1):
            # URL dedup gate is read-only (frontmatter scan + dict
            # lookup) so it runs even in dry-run — operators using
            # ``--dry-run --process-inbox`` get the actual skip
            # verdict in the preview rather than seeing every
            # queued raw counted as if it would process.
            dedup_result = self._check_url_dedup(
                file_path, index=active_index, stage="process_inbox",
            )
            if dedup_result is not None:
                results["files"].append(dedup_result)
                results["skipped"] += 1
                self._finalize_lifecycle_source(
                    file_path, dedup_result, dry_run=dry_run,
                )
                continue

            working_path = file_path
            if not dry_run and file_path.parent != self.processing_dir:
                working_path = self._stage_source_for_processing(file_path)

            if txn_id:
                self.txn.heartbeat(
                    txn_id,
                    step_name="process",
                    progress_mode="counted",
                    work_units_total=work_units_total,
                    work_units_done=position - 1,
                    work_units_failed=results["failed"],
                    current_item=working_path.name,
                    progress_summary=f"{position - 1}/{work_units_total} articles processed",
                )

            result = self.process_single_file(working_path, dry_run)
            results["files"].append(result)

            # C2: ``intake_only`` rolls up under ``completed`` since
            # the raw was successfully archived to 03-Processed and
            # absorb v2 will produce evergreens on the next run.
            if result["status"] in ("completed", "intake_only"):
                results["completed"] += 1
                results["total_tokens"] += result.get("tokens_used", 0)
                self._finalize_lifecycle_source(working_path, result, dry_run=dry_run)
            elif result["status"] == "error":
                results["failed"] += 1
                self._finalize_lifecycle_source(working_path, result, dry_run=dry_run)
            else:
                results["skipped"] += 1
                self._finalize_lifecycle_source(working_path, result, dry_run=dry_run)

            if txn_id:
                self.txn.heartbeat(
                    txn_id,
                    step_name="process",
                    progress_mode="counted",
                    work_units_total=work_units_total,
                    work_units_done=position,
                    work_units_failed=results["failed"],
                    current_item=working_path.name,
                    progress_summary=f"{position}/{work_units_total} articles processed",
                )

        return results


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Article intake processor — downloads images, parses "
            "frontmatter, archives raw to 50-Inbox/03-Processed for "
            "absorb v2.  Post-BL-029 there is no LLM-driven deep-"
            "dive step."
        )
    )
    parser.add_argument("--input", "-i", help="输入文件（每行一个URL）")
    parser.add_argument("--single", "-s", help="单个URL")
    parser.add_argument("--process-inbox", action="store_true", help="处理50-Inbox/01-Raw/")
    parser.add_argument("--process-single", type=Path, help="处理单个本地文件")
    parser.add_argument("--dry-run", action="store_true", help="预览模式")
    parser.add_argument("--batch-size", type=int, help="批量处理数量")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault根目录")
    parser.add_argument("--output-dir", type=Path, default=None, help="兼容旧入口，当前忽略")
    args = parser.parse_args()

    layout = VaultLayout.from_vault(args.vault_dir or VAULT_DIR)

    # 初始化组件
    logger = PipelineLogger(layout.pipeline_log)
    txn = TransactionManager(layout.transactions_dir)

    # 创建事务
    txn_id = txn.start("article-processing", f"Process articles {datetime.now().isoformat()}")
    logger.log("transaction_started", {"txn_id": txn_id, "type": "article-processing"})

    processor = AutoArticleProcessor(layout.vault_dir, logger, txn)
    print("✓ intake-only mode (absorb v2 produces evergreens on the next run)")

    # 执行处理
    txn.step(txn_id, "process", "in_progress", "Processing articles")

    if args.process_inbox:
        results = processor.process_inbox(dry_run=args.dry_run, batch_size=args.batch_size, txn_id=txn_id)
    elif args.process_single:
        result = processor.process_single_source(args.process_single, dry_run=args.dry_run)
        # C2: ``intake_only`` rolls up under ``completed`` since
        # absorb v2 will produce evergreens on the next run.
        # ``skipped_dedup`` keeps its own bucket so it's visible
        # in the summary line.
        ok = result["status"] in ("completed", "intake_only")
        results = {
            "total": 1,
            "completed": 1 if ok else 0,
            "failed": 1 if result["status"] == "error" else 0,
            "skipped": 1 if result["status"] in ("skipped", "skipped_dedup") else 0,
            "total_tokens": result.get("tokens_used", 0)
        }
    elif args.single:
        print("Single URL processing not yet implemented (requires WebFetch)")
        results = {"total": 0, "completed": 0, "failed": 0}
    elif args.input:
        print("Batch URL processing not yet implemented")
        results = {"total": 0, "completed": 0, "failed": 0}
    else:
        parser.print_help()
        sys.exit(1)

    results.setdefault("skipped", 0)
    results.setdefault("total_tokens", 0)
    txn.step(txn_id, "process", "completed", f"Completed {results['completed']}/{results['total']}")

    # 输出结果
    print("\n" + "="*60)
    print("ARTICLE PROCESSING RESULTS")
    print("="*60)
    print(f"Total: {results['total']}")
    queued_total = results.get("queued_total")
    if queued_total is not None and queued_total != results.get("total"):
        print(f"Queued: {queued_total}")
    print(f"Completed: {results['completed']}")
    print(f"Failed: {results['failed']}")
    print(f"Skipped: {results['skipped']}")
    print(f"Total Tokens: {results['total_tokens']}")

    # 完成事务
    txn.complete(txn_id)
    logger.log("transaction_completed", {"txn_id": txn_id, "results": results})

    return 0 if results['failed'] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
