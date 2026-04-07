"""
图片下载工具 - 自动下载 Markdown 中的远程图片

基于 Karpathy LLM Wiki 模式：LLM 需要查看图片获取视觉信息
自动检测 MD 中的图片 URL，下载到本地，并更新链接。

Usage:
    # 作为模块调用
    from image_downloader import ImageDownloader
    downloader = ImageDownloader(vault_dir)
    new_content = downloader.process_markdown(content, base_path)

    # CLI 使用
    python -m openclaw_pipeline.image_downloader --file article.md
"""

import os
import re
import hashlib
import requests
import argparse
from pathlib import Path
from urllib.parse import urlparse
from typing import Tuple, Optional, List

try:
    from .runtime import resolve_vault_dir
except ImportError:
    from runtime import resolve_vault_dir  # type: ignore


class ImageDownloader:
    """Markdown 图片下载器"""

    def __init__(
        self,
        vault_dir: Path,
        attachments_dir: str = "50-Inbox/01-Raw/attachments",
        timeout: int = 30
    ):
        self.vault_dir = Path(vault_dir)
        self.attachments_dir = self.vault_dir / attachments_dir
        self.timeout = timeout

        # 创建附件目录
        self.attachments_dir.mkdir(parents=True, exist_ok=True)

        # 代理设置
        self.proxies = {}
        http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
        if http_proxy:
            self.proxies["http"] = http_proxy
            self.proxies["https"] = http_proxy

    def log(self, message: str):
        """打印日志"""
        print(f"[image] {message}")

    def download_image(self, url: str, referer: Optional[str] = None) -> Tuple[bool, str]:
        """
        下载单个图片
        返回: (成功/失败, 本地路径或错误信息)
        """
        try:
            # 生成文件名
            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            ext = self._get_extension(url)
            filename = f"img-{url_hash}{ext}"

            # 按日期组织
            from datetime import datetime
            date_dir = self.attachments_dir / datetime.now().strftime('%Y-%m')
            date_dir.mkdir(parents=True, exist_ok=True)

            target_path = date_dir / filename

            # 如果已存在，直接返回
            if target_path.exists():
                return True, str(target_path.relative_to(self.vault_dir))

            # 下载
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            if referer:
                headers["Referer"] = referer

            response = requests.get(
                url,
                headers=headers,
                proxies=self.proxies,
                timeout=self.timeout,
                stream=True
            )
            response.raise_for_status()

            # 保存
            with open(target_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            return True, str(target_path.relative_to(self.vault_dir))

        except Exception as e:
            return False, str(e)

    def _get_extension(self, url: str) -> str:
        """从 URL 获取文件扩展名"""
        parsed = urlparse(url)
        path = parsed.path.lower()

        # 常见图片格式
        for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp']:
            if path.endswith(ext):
                return ext

        # 默认 .png
        return '.png'

    def process_markdown(
        self,
        content: str,
        base_path: Optional[Path] = None,
        download: bool = True
    ) -> Tuple[str, List[str]]:
        """
        处理 Markdown 内容，下载图片并更新链接
        返回: (新内容, 下载的图片列表)
        """
        downloaded = []
        new_content = content

        # 匹配 Markdown 图片语法: ![alt](url) 或 ![alt](url "title")
        image_pattern = r'!\[([^\]]*)\]\(([^)"\s]+)(?:\s+"[^"]*")?\)'

        for match in re.finditer(image_pattern, content):
            alt_text = match.group(1)
            url = match.group(2)

            # 跳过已经是本地路径的图片
            if not url.startswith(('http://', 'https://')):
                continue

            # 跳过 data URI
            if url.startswith('data:'):
                continue

            if download:
                # 获取原始页面的 referer（如果有）
                referer = None
                if base_path and base_path.exists():
                    try:
                        # 尝试从 frontmatter 读取 source
                        fm = self._parse_frontmatter(content)
                        if 'source' in fm:
                            referer = fm['source']
                    except Exception:
                        pass

                success, result = self.download_image(url, referer)

                if success:
                    local_path = result
                    downloaded.append(local_path)

                    # 更新链接
                    old_pattern = re.escape(match.group(0))
                    new_img = f"![{alt_text}]({local_path})"
                    new_content = re.sub(old_pattern, new_img, new_content)

                    self.log(f"✓ 下载成功: {url[:50]}... → {local_path}")
                else:
                    self.log(f"✗ 下载失败: {url[:50]}... ({result})")

        return new_content, downloaded

    def process_file(self, file_path: Path, backup: bool = True) -> List[str]:
        """
        处理单个 Markdown 文件
        返回下载的图片列表
        """
        if not file_path.exists():
            self.log(f"错误: 文件不存在 {file_path}")
            return []

        try:
            content = file_path.read_text(encoding='utf-8')
        except Exception as e:
            self.log(f"错误: 无法读取文件 {file_path}: {e}")
            return []

        # 处理内容
        new_content, downloaded = self.process_markdown(content, file_path)

        if downloaded:
            # 备份原文件
            if backup:
                backup_path = file_path.with_suffix('.md.backup')
                backup_path.write_text(content, encoding='utf-8')
                self.log(f"已备份: {backup_path}")

            # 写回新内容
            file_path.write_text(new_content, encoding='utf-8')
            self.log(f"已更新: {file_path} (下载了 {len(downloaded)} 张图片)")

        return downloaded

    def _parse_frontmatter(self, content: str) -> dict:
        """解析 frontmatter"""
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                try:
                    import yaml
                    return yaml.safe_load(parts[1]) or {}
                except Exception:
                    pass
        return {}


def main():
    """CLI 入口"""
    parser = argparse.ArgumentParser(
        description="下载 Markdown 中的远程图片到本地"
    )
    parser.add_argument(
        "--file",
        type=Path,
        required=True,
        help="要处理的 Markdown 文件"
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=None,
        help="Vault 根目录 (默认: 当前目录)"
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="不创建备份文件"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只检查，不实际下载"
    )

    args = parser.parse_args()

    vault_dir = resolve_vault_dir(args.vault_dir)

    # 处理文件
    downloader = ImageDownloader(vault_dir)

    if args.dry_run:
        # 只检查
        content = args.file.read_text(encoding='utf-8')
        _, _ = downloader.process_markdown(content, download=False)
        print("\n干运行模式，未实际下载")
    else:
        downloaded = downloader.process_file(
            args.file,
            backup=not args.no_backup
        )

        if downloaded:
            print(f"\n✅ 成功下载 {len(downloaded)} 张图片:")
            for img in downloaded:
                print(f"  - {img}")
        else:
            print("\nℹ️ 没有发现需要下载的远程图片")


if __name__ == "__main__":
    main()
