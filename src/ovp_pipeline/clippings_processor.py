#!/usr/bin/env python3
"""
Clippings Processor - 全自动Clippings处理Pipeline
自动扫描、迁移、处理Clippings目录中的内容

Usage:
    python3 clippings_processor.py --dry-run          # 预览模式
    python3 clippings_processor.py --process          # 实际处理
    python3 clippings_processor.py --batch-size 5     # 批量处理5篇

Features:
    - 自动扫描Clippings/目录
    - 文件名清理（移除特殊字符）
    - obsidian move迁移（非mv）
    - 自动深度解读生成
    - 幂等处理（manifest跟踪）
    - 统一日志记录
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from .runtime import VaultLayout, resolve_vault_dir
except ImportError:
    from runtime import VaultLayout, resolve_vault_dir  # type: ignore

try:
    from .source_lifecycle import clipping_raw_name
except ImportError:
    from source_lifecycle import clipping_raw_name  # type: ignore


VAULT_DIR = resolve_vault_dir()
DEFAULT_LAYOUT = VaultLayout.from_vault(VAULT_DIR)


def load_env_file(vault_dir: Path) -> None:
    env_file = vault_dir / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=env_file, override=True)
    except ImportError:
        pass

# Import litellm for LLM calls
sys.path.insert(0, str(Path(__file__).parent / "auto_vault"))
try:
    import litellm
except ImportError:
    litellm = None

# ========== 配置 ==========
CLIPPINGS_DIR = DEFAULT_LAYOUT.clippings_dir
RAW_DIR = DEFAULT_LAYOUT.raw_dir
PROCESSED_DIR = DEFAULT_LAYOUT.processed_dir
MANIFEST_FILE = DEFAULT_LAYOUT.vault_dir / "50-Inbox" / ".manifest.json"
LOG_FILE = DEFAULT_LAYOUT.pipeline_log
TXN_DIR = DEFAULT_LAYOUT.transactions_dir

# 特殊字符映射表
CHARACTER_MAP = {
    '"': '',
    "'": '',
    '—': '-',
    '–': '-',
    '…': '...',
    '《': '',
    '》': '',
    '「': '',
    '」': '',
    '【': '[',
    '】': ']',
    '｜': '|',
    '?': '',
    '!': '',
    ':': '-',
    '/': '-',
    '\\': '-',
}


class PipelineLogger:
    """统一过程日志记录器"""

    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.session_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()}"

    def log(self, event_type: str, data: dict[str, Any]):
        """记录结构化日志"""
        entry = {
            "timestamp": datetime.now().isoformat(),
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

    def start(self, workflow_type: str, description: str) -> str:
        """创建新事务"""
        txn_id = f"txn-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()[:8]}"
        txn_file = self.txn_dir / f"{txn_id}.json"

        txn_data = {
            "id": txn_id,
            "type": workflow_type,
            "description": description,
            "start_time": datetime.now().isoformat(),
            "status": "in_progress",
            "steps": {},
            "checkpoint": "initialized",
            "last_updated": datetime.now().isoformat()
        }

        txn_file.parent.mkdir(parents=True, exist_ok=True)
        with open(txn_file, "w", encoding="utf-8") as f:
            json.dump(txn_data, f, indent=2, ensure_ascii=False)

        return txn_id

    def step(self, txn_id: str, step_name: str, status: str, output: str = ""):
        """更新事务步骤"""
        txn_file = self.txn_dir / f"{txn_id}.json"
        if not txn_file.exists():
            return

        with open(txn_file, "r", encoding="utf-8") as f:
            txn_data = json.load(f)

        txn_data["steps"][step_name] = {
            "status": status,
            "output": output,
            "updated_at": datetime.now().isoformat()
        }
        txn_data["checkpoint"] = step_name
        txn_data["last_updated"] = datetime.now().isoformat()

        with open(txn_file, "w", encoding="utf-8") as f:
            json.dump(txn_data, f, indent=2, ensure_ascii=False)

    def complete(self, txn_id: str):
        """完成事务"""
        txn_file = self.txn_dir / f"{txn_id}.json"
        if not txn_file.exists():
            return

        with open(txn_file, "r", encoding="utf-8") as f:
            txn_data = json.load(f)

        txn_data["status"] = "completed"
        txn_data["completed_at"] = datetime.now().isoformat()
        txn_data["last_updated"] = datetime.now().isoformat()

        with open(txn_file, "w", encoding="utf-8") as f:
            json.dump(txn_data, f, indent=2, ensure_ascii=False)


class ManifestManager:
    """Manifest管理器 - 跟踪所有文件处理状态"""

    def __init__(self, manifest_file: Path):
        self.manifest_file = manifest_file
        self.data = self._load()

    def _load(self) -> dict:
        """加载manifest"""
        if self.manifest_file.exists():
            with open(self.manifest_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"files": {}, "version": "2.0", "last_updated": ""}

    def save(self):
        """保存manifest"""
        self.data["last_updated"] = datetime.now().isoformat()
        self.manifest_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.manifest_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def get_status(self, file_id: str) -> str:
        """获取文件处理状态"""
        return self.data.get("files", {}).get(file_id, {}).get("status", "unknown")

    def update(self, file_id: str, info: dict):
        """更新文件信息"""
        if "files" not in self.data:
            self.data["files"] = {}
        self.data["files"][file_id] = {
            **info,
            "last_updated": datetime.now().isoformat()
        }
        self.save()

    def list_unprocessed(self) -> list[str]:
        """列出未处理的文件"""
        unprocessed = []
        for file_id, info in self.data.get("files", {}).items():
            if info.get("status") in ["unprocessed", "failed", "pending"]:
                unprocessed.append(file_id)
        return unprocessed


class ClippingsProcessor:
    """Clippings处理器"""

    def __init__(self, vault_dir: Path, logger: PipelineLogger, txn: TransactionManager):
        self.layout = VaultLayout.from_vault(vault_dir)
        self.vault_dir = self.layout.vault_dir
        self.clippings_dir = self.layout.clippings_dir
        self.raw_dir = self.layout.raw_dir
        self.logger = logger
        self.txn = txn

    def sanitize_filename(self, filename: str) -> str:
        """清理文件名中的特殊字符"""
        # 应用字符映射
        for char, replacement in CHARACTER_MAP.items():
            filename = filename.replace(char, replacement)

        # 移除或替换其他危险字符
        filename = re.sub(r'[<>:"/\\|?*]', '-', filename)
        filename = re.sub(r'\s+', '_', filename)  # 空格转下划线
        filename = re.sub(r'-+', '-', filename)  # 多个连字符合并

        return filename.strip('-_')

    def scan_clippings(self) -> list[Path]:
        """Recursively scan ``Clippings/`` for ``.md`` files.

        Pre-2026-05 the scan used non-recursive ``glob("*.md")`` which
        silently ignored common subdirectory layouts such as
        ``Clippings/Twitter/*.md`` (Pinboard's Twitter clip default
        target).  18 Twitter clips sat unprocessed for weeks before
        the gap was noticed during the BL-058 rollout.  ``rglob``
        picks them up; the per-source-type signal (it came from a
        Twitter subdir) is **not** preserved at this layer because
        the source URL in frontmatter (``x.com/...``) is the
        authoritative type signal — see ``source_authority.py``.
        """
        if not self.clippings_dir.exists():
            return []

        files: list[Path] = []
        for f in self.clippings_dir.rglob("*.md"):
            if not f.is_file():
                continue
            files.append(f)

        return sorted(files)

    def _target_already_exists(self, new_name: str) -> bool:
        """Avoid silently overwriting an existing file in 01-Raw or
        03-Processed (any month).  Same basename across multiple
        intake runs almost always means "user re-clipped the same
        article" — we don't want to clobber the older copy without
        explicit intent.
        """
        if (self.raw_dir / new_name).exists():
            return True
        processed = self.layout.processed_dir
        if processed.exists():
            for month in processed.iterdir():
                if month.is_dir() and (month / new_name).exists():
                    return True
        return False

    def obsidian_move(self, source: Path, dest_dir: Path, new_name: str | None = None) -> bool:
        """使用obsidian move迁移文件（非mv）"""
        try:
            rel_source = source.relative_to(self.vault_dir)
            dest_path = dest_dir / (new_name or source.name)
            rel_dest = dest_path.relative_to(self.vault_dir)

            cmd = [
                "obsidian", "move",
                f"file={rel_source}",
                f"to={rel_dest}"
            ]

            result = subprocess.run(
                cmd, cwd=self.vault_dir,
                capture_output=True, text=True, timeout=30
            )

            if result.returncode == 0:
                self.logger.log("file_moved", {
                    "source": str(rel_source),
                    "destination": str(rel_dest),
                    "method": "obsidian_move"
                })
                return True
            else:
                # 如果obsidian move失败，尝试文件系统move（降级）
                import shutil
                shutil.move(str(source), str(dest_path))
                self.logger.log("file_moved", {
                    "source": str(rel_source),
                    "destination": str(rel_dest),
                    "method": "filesystem_fallback",
                    "warning": "obsidian_move failed, used filesystem move"
                })
                return True

        except Exception as e:
            self.logger.log("move_error", {"error": str(e), "source": str(source)})
            return False

    def process_clippings(self, dry_run: bool = False, batch_size: int | None = None) -> dict:
        """处理所有Clippings文件"""
        results = {
            "scanned": 0,
            "migrated": 0,
            "failed": 0,
            "skipped": 0,
            "files": []
        }

        # 扫描
        files = self.scan_clippings()
        results["scanned"] = len(files)

        if batch_size:
            files = files[:batch_size]

        # Build a single global URL→path index of the active
        # staging set so we don't re-clip a URL that's already
        # somewhere downstream (01-Raw / 02-Processing / 03-
        # Processed) or sitting in another Clippings entry from
        # the same batch.  We grow ``staged_urls`` in-process as
        # we accept new clippings, since the on-disk index won't
        # see migrations that happen during this loop.
        from .source_dedup import (
            build_active_url_index,
            extract_source_url,
            read_file_head,
        )
        active_index = build_active_url_index(self.vault_dir)
        staged_urls: set[str] = set()

        for file_path in files:
            file_info = {
                "original": str(file_path.name),
                "status": "pending"
            }

            new_name = clipping_raw_name(file_path, self.sanitize_filename)

            file_info["new_name"] = new_name

            # URL-level dedupe guard.  Catches the cross-stage case
            # the basename guard misses (e.g. user re-clips the
            # same x.com URL via a different filename, or a URL
            # that's already in 03-Processed/2026-04 under last
            # run's basename).  Self-match (the Clippings file
            # itself was already in the index because it lives
            # under ``Clippings/``) is filtered explicitly.
            try:
                head = read_file_head(file_path)
                source_url = extract_source_url(head)
            except OSError:
                source_url = None
            existing: Path | None = None
            if source_url:
                if source_url in staged_urls:
                    existing = file_path  # placeholder; reason below
                else:
                    candidate = active_index.get(source_url)
                    if candidate is not None:
                        try:
                            if candidate.resolve() != file_path.resolve():
                                existing = candidate
                        except OSError:
                            existing = candidate
            if existing is not None:
                file_info["status"] = "skipped_url_dedup"
                file_info["reason"] = (
                    f"URL {source_url} already claimed by "
                    f"{existing.relative_to(self.vault_dir) if existing != file_path else 'a previous Clippings entry in this batch'}"
                )
                file_info["source_url"] = source_url
                results["skipped"] += 1
                results["files"].append(file_info)
                self.logger.log("source_dedup_skipped", {
                    "source": str(file_path),
                    "url": source_url,
                    "existing": str(existing) if existing != file_path else "in_batch",
                    "stage": "clippings_intake",
                })
                continue

            # Dedupe guard: if the same basename already lives in
            # 01-Raw (pending intake) or any 03-Processed/<YYYY-MM>/
            # subdir, skip rather than silently overwrite.  Kept as
            # a defence-in-depth check for files without parseable
            # frontmatter URLs (extract_source_url returns None) —
            # the URL gate above is the main defence.
            if self._target_already_exists(new_name):
                file_info["status"] = "skipped_collision"
                file_info["reason"] = (
                    f"{new_name} already present under 50-Inbox/01-Raw "
                    f"or 50-Inbox/03-Processed; not overwriting"
                )
                results["skipped"] += 1
                results["files"].append(file_info)
                self.logger.log("clipping_collision_skipped", {
                    "original": str(file_path.name),
                    "new_name": new_name,
                })
                continue

            if dry_run:
                file_info["status"] = "dry_run"
                results["files"].append(file_info)
                continue

            # 确保目标目录存在
            self.raw_dir.mkdir(parents=True, exist_ok=True)

            # 迁移文件
            if self.obsidian_move(file_path, self.raw_dir, new_name):
                file_info["status"] = "migrated"
                results["migrated"] += 1
                if source_url:
                    staged_urls.add(source_url)
            else:
                file_info["status"] = "failed"
                results["failed"] += 1

            results["files"].append(file_info)

        self.logger.log("clippings_processed", results)
        return results


def main():
    parser = argparse.ArgumentParser(description="全自动Clippings处理器")
    parser.add_argument("--dry-run", action="store_true", help="预览模式")
    parser.add_argument("--batch-size", type=int, help="批量处理数量")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault根目录")
    args = parser.parse_args()

    layout = VaultLayout.from_vault(args.vault_dir or VAULT_DIR)
    load_env_file(layout.vault_dir)

    # 初始化组件
    logger = PipelineLogger(layout.pipeline_log)
    txn = TransactionManager(layout.transactions_dir)
    manifest = ManifestManager(layout.vault_dir / "50-Inbox" / ".manifest.json")

    # 创建事务
    txn_id = txn.start("clippings-processing", f"Process clippings {datetime.now().isoformat()}")
    logger.log("transaction_started", {"txn_id": txn_id, "type": "clippings-processing"})

    # 初始化处理器
    processor = ClippingsProcessor(layout.vault_dir, logger, txn)

    # 执行处理
    txn.step(txn_id, "scan", "in_progress", "Scanning Clippings directory")
    results = processor.process_clippings(dry_run=args.dry_run, batch_size=args.batch_size)
    txn.step(txn_id, "scan", "completed", f"Scanned {results['scanned']}, migrated {results['migrated']}")

    # 输出结果
    print("\n" + "="*60)
    print("CLIPPINGS PROCESSING RESULTS")
    print("="*60)
    print(f"Scanned: {results['scanned']}")
    print(f"Migrated: {results['migrated']}")
    print(f"Failed: {results['failed']}")
    print(f"Skipped: {results['skipped']}")

    if results['files']:
        print("\nFiles:")
        for f in results['files'][:10]:  # 只显示前10个
            print(f"  [{f['status']}] {f['original']} -> {f.get('new_name', 'N/A')}")

    # 完成事务
    txn.complete(txn_id)
    logger.log("transaction_completed", {"txn_id": txn_id})

    return 0 if results['failed'] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
