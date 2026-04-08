#!/usr/bin/env python3
"""
Pinboard Bookmark Processor
从 Pinboard 获取书签，分类型处理，自动去重

用法:
    python3 pinboard-processor.py [days] [--dry-run=false]
    python3 pinboard-processor.py --incremental-forward   # 处理更新的书签
    python3 pinboard-processor.py --incremental-backward  # 处理更早的历史书签
    python3 pinboard-processor.py --incremental           # 处理自上次以来新增的书签

示例:
    python3 pinboard-processor.py 7           # 预览最近7天
    python3 pinboard-processor.py 30          # 预览最近30天
    python3 pinboard-processor.py 7 --dry-run=false  # 执行实际处理
    python3 pinboard-processor.py --incremental-backward  # 处理历史书签
"""
import argparse
import os
import sys
from pathlib import Path

# 自动加载 .env 文件
VAULT_DIR_FOR_ENV = Path(__file__).parent if Path(__file__).parent.exists() else Path.cwd()
ENV_FILE = VAULT_DIR_FOR_ENV / ".env"
if ENV_FILE.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=ENV_FILE, override=True)
    except ImportError:
        pass  # dotenv 未安装，跳过

import requests
import subprocess
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Optional, Tuple


# ========== 配置 ==========
# 从环境变量获取 Token，格式: username:token
# 设置方式: export PINBOARD_TOKEN="your_username:your_api_token"
TOKEN = os.environ.get("PINBOARD_TOKEN", "")
if not TOKEN:
    print("❌ 错误: 未设置 PINBOARD_TOKEN 环境变量")
    print("   设置方式: export PINBOARD_TOKEN='your_username:your_api_token'")
    sys.exit(1)

# 从环境变量获取代理配置（可选）
PROXY = os.environ.get("HTTP_PROXY", "http://127.0.0.1:7897")

# 自动检测或从环境变量获取 Vault 目录
if "WIGS_VAULT_DIR" in os.environ:
    VAULT_DIR = Path(os.environ["WIGS_VAULT_DIR"])
else:
    # 尝试从git根目录检测
    try:
        import subprocess
        git_root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=Path(__file__).parent,
            text=True
        ).strip()
        VAULT_DIR = Path(git_root)
    except subprocess.CalledProcessError:
        # 降级方案：使用脚本所在目录
        VAULT_DIR = Path(__file__).parent

DATE = datetime.now().strftime("%Y-%m-%d")
# 【FIXED】STATE_FILE 路径改为 50-Inbox/ 下的正确位置
STATE_FILE = VAULT_DIR / "50-Inbox" / ".pinboard_state.json"
# 旧路径（用于自动迁移）
OLD_STATE_FILE = VAULT_DIR / "Inbox" / ".pinboard_state.json"

# ========== 状态迁移 ==========
def migrate_old_state():
    """自动迁移旧位置的状态文件到新位置"""
    if OLD_STATE_FILE.exists() and not STATE_FILE.exists():
        print(f"🔄 检测到旧版状态文件，自动迁移...")
        print(f"   从: {OLD_STATE_FILE}")
        print(f"   到: {STATE_FILE}")
        try:
            import shutil
            # 确保目标目录存在
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(OLD_STATE_FILE, STATE_FILE)
            # 可选：重命名旧文件作为备份，而不是删除
            backup_path = OLD_STATE_FILE.parent / ".pinboard_state.json.backup"
            OLD_STATE_FILE.rename(backup_path)
            print(f"✅ 状态文件迁移完成（旧文件已备份到: {backup_path}）")
        except Exception as e:
            print(f"⚠️  迁移失败: {e}")

# 启动时执行迁移
try:
    migrate_old_state()
except Exception:
    pass  # 迁移失败不阻止主程序运行

# ========== 数据结构 ==========
@dataclass
class Bookmark:
    href: str
    description: str
    extended: str
    tags: str
    dt: str
    hash: str
    shared: str
    toread: str
    url_type: str = ""

    @property
    def date(self) -> str:
        return self.dt[:10] if self.dt else ""

    @property
    def title(self) -> str:
        return self.description or self.href

    def to_dict(self):
        return {
            "href": self.href,
            "title": self.title,
            "description": self.description,
            "extended": self.extended,
            "tags": self.tags,
            "date": self.date,
            "url_type": self.url_type,
        }

# ========== 分类逻辑 ==========
def classify_url(url: str) -> str:
    """分类 URL 类型: github / paper / article / website / social"""
    url_lower = url.lower()
    if "github.com" in url_lower:
        return "github"
    elif "arxiv.org" in url_lower:
        return "paper"
    elif any(ext in url_lower for ext in [
        '.md', '.txt', 'medium.com', 'blog.', 'dev.to',
        'substack.com', 'news.ycombinator.com', 'lobste.rs'
    ]):
        return "article"
    elif any(keyword in url_lower for keyword in [
        'twitter.com', 'x.com', 'youtube.com', 'bilibili.com',
        'instagram.com', 'tiktok.com'
    ]):
        return "social"
    else:
        return "website"

# ========== 状态管理 ==========
def load_state() -> dict:
    """加载上次处理状态"""
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "last_processed_date": None,  # 最新处理的日期（正向）
        "first_processed_date": None,  # 最早处理的日期（反向）
        "last_processed_hash": None,
        "processed_count": {"github": 0, "paper": 0, "website": 0, "article": 0, "social": 0}
    }

def save_state(state: dict):
    """保存处理状态"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def save_bookmarks_to_inbox(bookmarks: List[Bookmark], vault_dir: Path) -> dict:
    """保存书签到 50-Inbox/02-Pinboard/ 目录

    Returns: dict with counts of saved files
    """
    saved = {"github": 0, "paper": 0, "article": 0, "website": 0, "social": 0}

    inbox_dir = vault_dir / "50-Inbox" / "02-Pinboard"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    for bm in bookmarks:
        # 生成文件名
        if bm.url_type == "github":
            parsed = parse_github_url(bm.href)
            if parsed:
                owner, repo = parsed
                filename = f"{bm.date}_{owner}_{repo}.md"
            else:
                clean_title = re.sub(r'[^\w\-]', '_', bm.title[:30])
                filename = f"{bm.date}_{clean_title}.md"
        elif bm.url_type == "article":
            title = re.sub(r'[^\w\u4e00-\u9fff\-]', '_', bm.title)[:50]
            filename = f"{bm.date}_{title}.md"
        else:
            # website or social - use domain + date
            parsed_url = re.match(r'https?://([^/]+)', bm.href)
            domain = parsed_url.group(1) if parsed_url else "unknown"
            filename = f"{bm.date}_{domain}.md"

        filepath = inbox_dir / filename

        # 避免覆盖：如果文件已存在，跳过
        if filepath.exists():
            continue

        # 生成文件内容
        tags_list = bm.tags.split() if bm.tags else []
        tags_str = ", ".join(tags_list) if tags_list else "none"

        content = f"""---
title: "{bm.title}"
source: {bm.href}
date: {bm.date}
type: pinboard-{bm.url_type}
tags: [{tags_str}]
---

{bm.description}

## Notes

{bm.extended}

## Tags

#{bm.tags.replace(' ', ' #') if bm.tags else 'untagged'}
"""

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        saved[bm.url_type] += 1

    return saved

# ========== GitHub URL 解析 ==========
def parse_github_url(url: str) -> Optional[Tuple[str, str]]:
    """从 GitHub URL 提取 owner/repo"""
    match = re.match(r"github\.com[/:]([^/]+)/([^/\s#?]+)", url, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2).replace('.git', '')
    return None

# ========== 获取书签 ==========
def fetch_bookmarks(days: int = 7, start_date: datetime = None, end_date: datetime = None) -> List[Bookmark]:
    """从 Pinboard API 获取书签

    Args:
        days: 默认天数范围（当 start_date/end_date 未指定时使用）
        start_date: 指定起始日期（用于双向处理）
        end_date: 指定结束日期（用于双向处理）
    """
    if end_date is None:
        end_date = datetime.now()
    if start_date is None:
        start_date = end_date - timedelta(days=days)

    url = "https://api.pinboard.in/v1/posts/get"
    all_bookmarks = []

    # Pinboard API 需要每天单独查询，dt 参数是必需的
    current = start_date
    while current <= end_date:
        params = {
            "auth_token": TOKEN,
            "dt": current.strftime("%Y-%m-%d"),
            "results": 1000,
        }

        try:
            # 先尝试带代理
            proxies = {"http": PROXY, "https": PROXY} if PROXY else None
            response = requests.get(url, params=params, proxies=proxies, timeout=30)
            response.raise_for_status()
        except requests.exceptions.ProxyError:
            # 代理失败，尝试直连
            try:
                response = requests.get(url, params=params, timeout=30)
                response.raise_for_status()
            except requests.exceptions.RequestException as e:
                print(f"❌ 请求失败: {e}")
                return []
        except requests.exceptions.RequestException as e:
            print(f"❌ 请求失败: {e}")
            return []
            print("❌ 代理连接失败，请检查 PROXY 配置")
            return []
        except requests.exceptions.RequestException as e:
            print(f"❌ 请求失败: {e}")
            return []

        root = ET.fromstring(response.text)

        for post in root.findall("post"):
            # 跳过私有书签（shared="no" 表示私有）
            if post.get("shared") == "no":
                continue

            # Pinboard API 使用 "time" 属性，不是 "dt"
            bm = Bookmark(
                href=post.get("href", ""),
                description=post.get("description", ""),
                extended=post.get("extended", ""),
                tags=post.get("tag", ""),
                dt=post.get("time", ""),  # 使用 time 而非 dt
                hash=post.get("hash", ""),
                shared=post.get("shared", ""),
                toread=post.get("toread", ""),
            )
            bm.url_type = classify_url(bm.href)
            all_bookmarks.append(bm)

        current += timedelta(days=1)

    return all_bookmarks


def parse_cli_date(value: str) -> datetime:
    """Parse YYYY-MM-DD CLI arguments."""
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"无效日期格式: {value}，应为 YYYY-MM-DD") from exc


def validate_single_day_range(start_date: datetime, end_date: datetime) -> None:
    """Pinboard low-level queries must target exactly one day."""
    start_day = start_date.date()
    end_day = end_date.date()
    if start_day > end_day:
        raise ValueError(
            f"无效日期范围: start_date={start_day.isoformat()} 晚于 end_date={end_day.isoformat()}"
        )
    if start_day != end_day:
        raise ValueError(
            "Pinboard API 不支持跨天范围查询。"
            f"收到 {start_day.isoformat()} ~ {end_day.isoformat()}。"
            "请传入同一天的 --start-date/--end-date，或让上层 pipeline 按天拆分调用。"
        )

# ========== 去重检查 ==========
def check_duplicate_in_vault(bookmark: Bookmark, vault_dir: Path) -> Optional[str]:
    """检查书签是否已在 vault 中存在"""
    if not bookmark.href:
        return None

    # 1. 直接搜索 URL（处理带引号和不带引号的情况）
    # frontmatter 中可能是: github: "https://..." 或 source: https://...
    # 先提取 URL 核心部分（去除 query string 和 fragment）
    from urllib.parse import urlparse
    parsed = urlparse(bookmark.href)
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"

    search_patterns = [
        bookmark.href,                    # 原始 URL（带参数）
        f'"{bookmark.href}"',           # 带引号
        base_url,                        # 基础 URL（无参数）
        f'"{base_url}"',                # 带引号的基础 URL
    ]

    # 1b. arXiv 特殊处理：提取 arXiv ID 并搜索
    # 例如 https://arxiv.org/abs/2603.18000 -> 搜索 "2603.18000" 或 "arXiv:2603.18000"
    arxiv_match = re.search(r'arxiv\.org/(?:abs|pdf)/(\d+\.\d+)', bookmark.href)
    if arxiv_match:
        arxiv_id = arxiv_match.group(1)
        search_patterns.extend([
            arxiv_id,                  # 2603.18000
            f"arXiv:{arxiv_id}",      # arXiv:2603.18000
            f"arXiv {arxiv_id}",       # arXiv 2603.18000
        ])

    for pattern in search_patterns:
        # -i: case-insensitive (GitHub URL 大小写不敏感)
        result = subprocess.run(
            ["grep", "-ri", pattern, str(vault_dir), "--include=*.md", "-l"],
            capture_output=True, text=True, cwd=str(vault_dir)
        )
        if result.stdout.strip():
            return result.stdout.strip().split("\n")[0]

    # 2. 搜索 title (部分匹配)
    if bookmark.title and len(bookmark.title) > 10:
        # 转义特殊字符
        escaped_title = re.escape(bookmark.title[:50])
        result = subprocess.run(
            ["grep", "-rli", escaped_title, str(vault_dir), "--include=*.md"],
            capture_output=True, text=True, cwd=str(vault_dir)
        )
        if result.stdout.strip():
            return result.stdout.strip().split("\n")[0]

    # 3. GitHub 项目：搜索 Projects 目录的 owner/repo 字段（case-insensitive）
    if bookmark.url_type == "github":
        parsed = parse_github_url(bookmark.href)
        if parsed:
            owner, repo = parsed
            projects_dir = vault_dir / "Projects"
            if projects_dir.exists():
                # 搜索 owner 字段
                result = subprocess.run(
                    ["grep", "-rli", f"owner:", str(projects_dir), "--include=*.md"],
                    capture_output=True, text=True
                )
                if result.stdout.strip():
                    for f in result.stdout.strip().split("\n"):
                        if f:
                            check = subprocess.run(
                                ["grep", "-i", f"owner: {owner}", f],
                                capture_output=True, text=True
                            )
                            if check.stdout.strip():
                                return f
                            check2 = subprocess.run(
                                ["grep", "-i", f"repo: {repo}", f],
                                capture_output=True, text=True
                            )
                            if check2.stdout.strip():
                                return f

    return None

# ========== 生成处理建议 ==========
def generate_processing_instructions(new_items: List[Bookmark]) -> str:
    """为待处理书签生成处理指令"""
    lines = []
    lines.append("\n" + "="*70)
    lines.append("📋 处理指令")
    lines.append("="*70)

    for url_type in ["github", "paper", "article", "website", "social"]:
        type_items = [bm for bm in new_items if bm.url_type == url_type]
        if not type_items:
            continue

        lines.append(f"\n### {url_type.upper()} ({len(type_items)} 个)")

        for bm in type_items:
            lines.append(f"\n**{bm.title}**")
            lines.append(f"- URL: {bm.href}")
            if bm.tags:
                lines.append(f"- Tags: {bm.tags}")

            if url_type == "github":
                parsed = parse_github_url(bm.href)
                if parsed:
                    owner, repo = parsed
                    lines.append(f"→ 调用 `github-project-processor` skill")
                    lines.append(f"→ 目标路径: `Projects/{DATE}_{owner}_{repo}.md`")

            elif url_type == "paper":
                arxiv_match = re.search(r'arxiv\.org/(?:abs|pdf)/(\d+\.\d+)', bm.href)
                if arxiv_match:
                    lines.append(f"→ 调用 `paper-processor` skill")
                    lines.append(f"→ 目标路径: `AI-Research/Papers/{bm.date}_{arxiv_match.group(1)}.md`")

            elif url_type == "article":
                title = re.sub(r'[^\w\u4e00-\u9fff-]', '_', bm.title)[:50]
                lines.append(f"→ 调用 `article-processor` skill")
                lines.append(f"→ 原文路径: `Inbox/原始文章/{bm.date}_{title}.md`")

            elif url_type == "website":
                lines.append(f"→ 抓取页面元信息，保存到分类目录")

            elif url_type == "social":
                lines.append(f"→ 使用 browser tool 抓取内容")

    return "\n".join(lines)

# ========== 主函数 ==========
def main(days: int = 7, dry_run: bool = True, incremental: bool = False,
         incremental_forward: bool = False, incremental_backward: bool = False,
         start_date: datetime = None, end_date: datetime = None):
    mode = "normal"
    if incremental:
        mode = "incremental"
    elif incremental_forward:
        mode = "forward"
    elif incremental_backward:
        mode = "backward"

    # 增量模式：从状态文件读取上次处理日期
    if mode != "normal":
        state = load_state()

        if mode == "incremental":
            # 默认增量：处理更新的书签
            if state["last_processed_date"]:
                last_date = datetime.strptime(state["last_processed_date"], "%Y-%m-%d")
                days = (datetime.now() - last_date).days + 1
                print(f"📍 增量模式：自 {state['last_processed_date']} 以来约 {days} 天")
                start_date = last_date + timedelta(seconds=1)
                end_date = datetime.now()

        elif mode == "forward":
            # 正向增量：处理比 last_processed_date 更新的书签
            if state["last_processed_date"]:
                last_date = datetime.strptime(state["last_processed_date"], "%Y-%m-%d")
                days = (datetime.now() - last_date).days + 1
                print(f"📍 正向增量：处理 {state['last_processed_date']} 之后的新书签 (约 {days} 天)")
                start_date = last_date + timedelta(seconds=1)
                end_date = datetime.now()
            else:
                print(f"📍 正向增量：无上次处理记录，获取最近书签")
                days = 7
                start_date = None
                end_date = None

        elif mode == "backward":
            # 反向增量：处理比 first_processed_date 更早的书签
            first_date_str = state.get("first_processed_date")
            if first_date_str:
                first_date = datetime.strptime(first_date_str, "%Y-%m-%d")
                # 每次处理 30 天的历史
                days = 30
                start_date = first_date - timedelta(days=days)
                end_date = first_date - timedelta(seconds=1)
                print(f"📍 反向增量：处理 {state['first_processed_date']} 之前的历史书签 (约 {days} 天)")
            else:
                print(f"📍 反向增量：无最早处理记录，获取更早书签")
                # 获取更早的书签，设定一个较长的范围
                days = 100
                end_date = datetime.now() - timedelta(days=1)
                start_date = end_date - timedelta(days=days)

    print(f"\n{'='*70}")
    print(f"📌 Pinboard Bookmark Processor")
    print(f"{'='*70}")
    print(f"模式: {mode}")
    print(f"Token: {TOKEN.split(':')[0]}***")
    print(f"Vault: {VAULT_DIR}")
    print(f"{'='*70}\n")

    # 获取书签
    if start_date and end_date:
        print(f"📅 日期范围: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")
        bookmarks = fetch_bookmarks(start_date=start_date, end_date=end_date)
    else:
        print(f"📅 日期范围: 最近 {days} 天")
        bookmarks = fetch_bookmarks(days=days)

    if not bookmarks:
        print("❌ 未获取到任何书签")
        return

    # 分类统计
    stats = {"github": [], "paper": [], "article": [], "website": [], "social": []}
    for bm in bookmarks:
        if bm.url_type in stats:
            stats[bm.url_type].append(bm)

    print(f"📊 总计: {len(bookmarks)} 个书签")
    for url_type, items in stats.items():
        if items:
            icon = {"github": "🔬", "paper": "📄", "article": "📝", "website": "🌐", "social": "📱"}[url_type]
            print(f"   {icon} {url_type}: {len(items)}")

    # 去重检查
    print(f"\n{'='*70}")
    print("🔍 去重检查")
    print(f"{'='*70}")

    new_items = []
    duplicate_items = []

    for bm in bookmarks:
        duplicate = check_duplicate_in_vault(bm, VAULT_DIR)
        if duplicate:
            duplicate_items.append((bm, duplicate))
        else:
            new_items.append(bm)

    print(f"\n⏭️  已存在 ({len(duplicate_items)} 个) - 将跳过:")
    if duplicate_items:
        for bm, dup_file in duplicate_items[:10]:
            icon = {"github": "🔬", "paper": "📄", "article": "📝", "website": "🌐", "social": "📱"}.get(bm.url_type, "📌")
            print(f"   {icon} {bm.title[:50]}")
            print(f"      → {Path(dup_file).name}")
        if len(duplicate_items) > 10:
            print(f"   ... 还有 {len(duplicate_items) - 10} 个")
    else:
        print("   无")

    print(f"\n✅ 待处理 ({len(new_items)} 个):")

    if not new_items:
        print("   所有书签都已收录，无需处理")
    else:
        # 按类型分组展示
        for url_type in ["github", "article", "website", "social"]:
            type_items = [bm for bm in new_items if bm.url_type == url_type]
            if not type_items:
                continue

            icon = {"github": "🔬", "paper": "📄", "article": "📝", "website": "🌐", "social": "📱"}[url_type]
            type_name = {"github": "GitHub 项目", "paper": "学术论文", "article": "文章", "website": "网站", "social": "社交媒体"}[url_type]
            print(f"\n{icon} **{type_name}** ({len(type_items)}):")

            for bm in type_items:
                tags_str = f" #{bm.tags.replace(' ', ' #')}" if bm.tags else ""
                title_display = bm.title[:60] + '...' if len(bm.title) > 60 else bm.title
                print(f"   • {title_display}")
                print(f"     URL: {bm.href[:70]}{'...' if len(bm.href) > 70 else ''}{tags_str}")

                # 输出处理建议
                if url_type == "github":
                    parsed = parse_github_url(bm.href)
                    if parsed:
                        owner, repo = parsed
                        print(f"     → Projects/{DATE}_{owner}_{repo}.md")
                elif url_type == "article":
                    title = re.sub(r'[^\w\u4e00-\u9fff-]', '_', bm.title)[:50]
                    print(f"     → Inbox/原始文章/{bm.date}_{title}.md")

    # 生成处理指令
    if new_items and not dry_run:
        print(generate_processing_instructions(new_items))

    # 保存详细结果
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"/tmp/pinboard_bookmarks_{timestamp}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({
            "stats": {k: len(v) for k, v in stats.items()},
            "duplicates": [{"bookmark": bm.to_dict(), "existing_file": dup} for bm, dup in duplicate_items],
            "new_items": [bm.to_dict() for bm in new_items],
            "generated_at": timestamp,
        }, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*70}")
    print(f"💾 详细结果已保存到: {output_file}")
    print(f"{'='*70}")

    # 汇总信息
    print(f"\n📋 **汇总**:")
    print(f"   - 总书签: {len(bookmarks)}")
    print(f"   - 已收录: {len(duplicate_items)}")
    print(f"   - 待处理: {len(new_items)}")
    print(f"     🔬 GitHub: {len(stats['github'])}")
    print(f"     📄 论文: {len(stats['paper'])}")
    print(f"     📝 文章: {len(stats['article'])}")
    print(f"     🌐 网站: {len(stats['website'])}")
    print(f"     📱 社交: {len(stats['social'])}")

    if dry_run:
        print(f"\n💡 提示:")
        print(f"   预览模式: python3 pinboard-processor.py {days}")
        print(f"   实际处理: python3 pinboard-processor.py {days} --dry-run=false")
        print(f"   增量模式: python3 pinboard-processor.py --incremental")
    else:
        # 保存状态
        state = load_state()

        if mode == "forward":
            # 正向：更新 last_processed_date
            if bookmarks:
                max_date = max(bm.date for bm in bookmarks)
                if state["last_processed_date"] is None or max_date > state["last_processed_date"]:
                    state["last_processed_date"] = max_date

        elif mode == "backward":
            # 反向：更新 first_processed_date
            if bookmarks:
                min_date = min(bm.date for bm in bookmarks)
                if state["first_processed_date"] is None or min_date < state["first_processed_date"]:
                    state["first_processed_date"] = min_date

        else:
            # 常规/增量模式：更新 last_processed_date
            state["last_processed_date"] = DATE

        state["processed_count"]["github"] += len(stats["github"])
        state["processed_count"]["paper"] += len(stats["paper"])
        state["processed_count"]["website"] += len(stats["website"])
        state["processed_count"]["article"] += len(stats["article"])
        state["processed_count"]["social"] += len(stats["social"])

        # 【FIXED】实际保存书签文件到 50-Inbox/02-Pinboard/
        if new_items:
            saved = save_bookmarks_to_inbox(new_items, VAULT_DIR)
            print(f"\n💾 已保存书签到 inbox:")
            for url_type, count in saved.items():
                if count > 0:
                    icon = {"github": "🔬", "paper": "📄", "article": "📝", "website": "🌐", "social": "📱"}.get(url_type, "📌")
                    print(f"   {icon} {url_type}: {count} 个")

        save_state(state)
        print(f"\n✅ 状态已更新: {STATE_FILE}")

if __name__ == "__main__":
    argv = list(sys.argv[1:])
    if "--dry-run=false" in argv:
        argv = ["--execute" if arg == "--dry-run=false" else arg for arg in argv]

    parser = argparse.ArgumentParser(
        description="Pinboard Bookmark Processor（底层只接受单日查询，上层范围处理必须按天拆分）"
    )
    parser.add_argument("days", nargs="?", type=int, default=7, help="最近 N 天（默认 7）")
    parser.add_argument("--start-date", help="起始日期 YYYY-MM-DD。仅允许与 --end-date 为同一天")
    parser.add_argument("--end-date", help="结束日期 YYYY-MM-DD。仅允许与 --start-date 为同一天")
    parser.add_argument("--incremental", action="store_true", help="自上次处理以来的增量")
    parser.add_argument("--incremental-forward", action="store_true", help="向前增量处理新书签")
    parser.add_argument("--incremental-backward", action="store_true", help="向后增量处理历史书签")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=True, help="预览模式")
    parser.add_argument("--execute", dest="dry_run", action="store_false", help="执行实际写入（兼容 --dry-run=false）")
    args = parser.parse_args(argv)

    if (args.start_date is None) ^ (args.end_date is None):
        parser.error("--start-date 和 --end-date 必须同时提供，并且必须是同一天。")

    start_date = None
    end_date = None
    if args.start_date and args.end_date:
        try:
            start_date = parse_cli_date(args.start_date)
            end_date = parse_cli_date(args.end_date)
            validate_single_day_range(start_date, end_date)
        except ValueError as exc:
            parser.error(str(exc))

    main(
        days=args.days,
        dry_run=args.dry_run,
        incremental=args.incremental,
        incremental_forward=args.incremental_forward,
        incremental_backward=args.incremental_backward,
        start_date=start_date,
        end_date=end_date,
    )
