#!/usr/bin/env python3
"""
Auto MOC Updater - 自动MOC索引更新器
自动检测新文件并更新相关MOC

Usage:
    python3 auto_moc_updater.py --scan           # 扫描并更新所有MOC
    python3 auto_moc_updater.py --check-area AI-Research
    python3 auto_moc_updater.py --update-atlas

Features:
    - 自动扫描Areas中的新文件
    - 检测未索引文件
    - 自动更新MOC
    - 幂等处理（避免重复）
    - 统一日志记录
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

# 自动加载 .env 文件
VAULT_DIR = Path(__file__).parent.parent.parent
ENV_FILE = VAULT_DIR / ".env"
if ENV_FILE.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=ENV_FILE, override=True)
    except ImportError:
        pass  # dotenv 未安装，跳过

# ========== 配置 ==========
AREAS_DIR = VAULT_DIR / "20-Areas"
ATLAS_DIR = VAULT_DIR / "10-Knowledge" / "Atlas"
LOG_FILE = VAULT_DIR / "60-Logs" / "pipeline.jsonl"

# MOC映射配置
AREA_MOC_MAPPING = {
    "AI-Research": {
        "topics_moc": "Topics/AI MOC.md",
        "main_moc": "MOC.md",
        "atlas": "AI-Concepts.md",
    },
    "Tools": {
        "topics_moc": "Topics/Tools MOC.md",
        "main_moc": "MOC.md",
        "atlas": "Tool-Concepts.md",
    },
    "Investing": {
        "topics_moc": "Topics/Investing MOC.md",
        "main_moc": "MOC.md",
        "atlas": "Investment-Concepts.md",
    },
    "Programming": {
        "topics_moc": "Topics/Programming MOC.md",
        "main_moc": "MOC.md",
        "atlas": "Programming-Concepts.md",
    },
}


class PipelineLogger:
    """统一过程日志记录器"""

    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.session_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()}"

    def log(self, event_type: str, data: dict[str, Any]):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            "event_type": event_type,
            **data
        }
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class MOCUpdater:
    """MOC更新器"""

    def __init__(self, vault_dir: Path, logger: PipelineLogger):
        self.vault_dir = vault_dir
        self.areas_dir = vault_dir / "20-Areas"
        self.atlas_dir = vault_dir / "10-Knowledge" / "Atlas"
        self.logger = logger

    def find_moc_file(self, area: str, moc_type: str) -> Path | None:
        """查找MOC文件"""
        mapping = AREA_MOC_MAPPING.get(area, {})
        moc_path = mapping.get(moc_type)

        if not moc_path:
            return None

        full_path = self.areas_dir / area / moc_path
        if full_path.exists():
            return full_path

        # 尝试备选路径
        if moc_type == "topics_moc":
            # 尝试直接搜索MOC文件
            topics_dir = self.areas_dir / area / "Topics"
            if topics_dir.exists():
                for moc_file in topics_dir.glob("*MOC.md"):
                    return moc_file

        return None

    def scan_files_in_area(self, area: str) -> list[Path]:
        """扫描Area中的所有深度解读文件"""
        area_dir = self.areas_dir / area
        if not area_dir.exists():
            return []

        topics_dir = area_dir / "Topics"
        if not topics_dir.exists():
            return []

        files = []
        # 递归扫描所有子目录
        for md_file in topics_dir.rglob("*_深度解读.md"):
            files.append(md_file)

        return sorted(files)

    def extract_wikilinks(self, content: str) -> list[str]:
        """提取所有wiki-links"""
        # 匹配 [[Link]] 或 [[Link|Display]]
        pattern = r'\[\[([^\]|]+)(?:\|[^\]]*)?\]\]'
        matches = re.findall(pattern, content)
        return matches

    def check_file_in_moc(self, moc_path: Path, file_stem: str) -> bool:
        """检查文件是否已在MOC中索引"""
        if not moc_path.exists():
            return False

        with open(moc_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 检查多种可能的链接格式
        patterns = [
            f"[[{file_stem}]]",
            f"[[{file_stem}|",
            f"[[{file_stem}.md",
        ]

        return any(p in content for p in patterns)

    def add_to_moc(self, moc_path: Path, file_stem: str, area: str, dry_run: bool = False) -> bool:
        """添加文件到MOC"""
        if not moc_path.exists():
            return False

        if self.check_file_in_moc(moc_path, file_stem):
            return True  # 已存在

        if dry_run:
            return True

        try:
            with open(moc_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 查找合适的插入位置（通常是文件末尾或特定section）
            # 简单策略：在最后一个条目后添加
            link_line = f"- [[{file_stem}]]"

            # 检查是否有月份分类
            month = file_stem[:7] if len(file_stem) >= 7 and file_stem[4] == '-' else "其他"

            # 构建月份section
            month_header = f"## {month}"

            if month_header in content:
                # 找到月份section，在其后添加
                lines = content.split("\n")
                insert_idx = len(lines)

                for i, line in enumerate(lines):
                    if line.startswith(month_header):
                        # 找到该section的结束位置
                        for j in range(i + 1, len(lines)):
                            if lines[j].startswith("## "):
                                insert_idx = j
                                break
                        else:
                            insert_idx = len(lines)
                        break

                lines.insert(insert_idx, link_line)
                new_content = "\n".join(lines)
            else:
                # 添加新的月份section
                new_section = f"\n{month_header}\n\n{link_line}\n"
                new_content = content + new_section

            with open(moc_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            self.logger.log("moc_updated", {
                "moc": str(moc_path),
                "file_added": file_stem,
                "area": area
            })

            return True

        except Exception as e:
            self.logger.log("moc_update_error", {
                "moc": str(moc_path),
                "error": str(e)
            })
            return False

    def update_area_moc(self, area: str, dry_run: bool = False) -> dict:
        """更新单个Area的MOC"""
        result = {
            "area": area,
            "files_scanned": 0,
            "files_indexed": 0,
            "files_added": 0,
            "errors": []
        }

        # 扫描文件
        files = self.scan_files_in_area(area)
        result["files_scanned"] = len(files)

        # 找到MOC文件
        topics_moc = self.find_moc_file(area, "topics_moc")
        main_moc = self.find_moc_file(area, "main_moc")

        for file_path in files:
            file_stem = file_path.stem

            # 检查是否已在MOC中
            is_indexed = False

            if topics_moc and self.check_file_in_moc(topics_moc, file_stem):
                is_indexed = True
            elif main_moc and self.check_file_in_moc(main_moc, file_stem):
                is_indexed = True

            if is_indexed:
                result["files_indexed"] += 1
                continue

            # 添加到MOC
            moc_to_update = topics_moc or main_moc
            if moc_to_update:
                if self.add_to_moc(moc_to_update, file_stem, area, dry_run):
                    result["files_added"] += 1
                else:
                    result["errors"].append(f"Failed to add {file_stem}")

        return result

    def update_atlas_moc(self, dry_run: bool = False) -> dict:
        """更新Atlas MOC"""
        result = {
            "evergreen_scanned": 0,
            "evergreen_linked": 0,
            "errors": []
        }

        evergreen_dir = self.vault_dir / "10-Knowledge" / "Evergreen"
        if not evergreen_dir.exists():
            return result

        # 扫描所有Evergreen笔记
        evergreen_files = list(evergreen_dir.glob("*.md"))
        result["evergreen_scanned"] = len(evergreen_files)

        # 检查Atlas MOC
        atlas_moc = self.atlas_dir / "MOC-Index.md"
        if not atlas_moc.exists():
            return result

        with open(atlas_moc, "r", encoding="utf-8") as f:
            atlas_content = f.read()

        for eg_file in evergreen_files:
            eg_stem = eg_file.stem

            # 检查是否已链接
            if f"[[{eg_stem}]]" in atlas_content or f"[[{eg_stem}|" in atlas_content:
                result["evergreen_linked"] += 1
                continue

            if not dry_run:
                # 添加到Atlas（简化：添加到Concepts section）
                try:
                    with open(atlas_moc, "a", encoding="utf-8") as f:
                        f.write(f"\n- [[{eg_stem}]]")
                    result["evergreen_linked"] += 1
                except Exception as e:
                    result["errors"].append(str(e))

        return result

    def scan_all_areas(self, dry_run: bool = False) -> list[dict]:
        """扫描所有Areas"""
        areas = ["AI-Research", "Tools", "Investing", "Programming"]

        results = []
        for area in areas:
            print(f"\nUpdating {area}...")
            result = self.update_area_moc(area, dry_run)
            results.append(result)
            print(f"  Scanned: {result['files_scanned']}, "
                  f"Indexed: {result['files_indexed']}, "
                  f"Added: {result['files_added']}")

        return results


def main():
    parser = argparse.ArgumentParser(description="自动MOC索引更新器")
    parser.add_argument("--scan", action="store_true", help="扫描并更新所有MOC")
    parser.add_argument("--check-area", help="检查指定Area")
    parser.add_argument("--update-atlas", action="store_true", help="更新Atlas MOC")
    parser.add_argument("--dry-run", action="store_true", help="预览模式")
    parser.add_argument("--vault-dir", type=Path, default=VAULT_DIR, help="Vault根目录")
    args = parser.parse_args()

    # 初始化
    logger = PipelineLogger(LOG_FILE)
    updater = MOCUpdater(args.vault_dir, logger)

    # 执行更新
    if args.scan:
        print("Scanning all areas...")
        results = updater.scan_all_areas(dry_run=args.dry_run)

        if args.update_atlas:
            print("\nUpdating Atlas...")
            atlas_result = updater.update_atlas_moc(dry_run=args.dry_run)
            results.append({"area": "Atlas", **atlas_result})

    elif args.check_area:
        print(f"Checking area: {args.check_area}")
        result = updater.update_area_moc(args.check_area, dry_run=args.dry_run)
        results = [result]

    elif args.update_atlas:
        print("Updating Atlas...")
        result = updater.update_atlas_moc(dry_run=args.dry_run)
        results = [{"area": "Atlas", **result}]

    else:
        parser.print_help()
        sys.exit(1)

    # 汇总
    total_scanned = sum(r.get("files_scanned", 0) for r in results)
    total_added = sum(r.get("files_added", 0) for r in results)

    print(f"\n{'='*60}")
    print(f"MOC UPDATE COMPLETE")
    print(f"{'='*60}")
    print(f"Areas checked: {len(results)}")
    print(f"Files scanned: {total_scanned}")
    print(f"Files added: {total_added}")

    logger.log("moc_update_complete", {
        "areas": len(results),
        "scanned": total_scanned,
        "added": total_added
    })

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
