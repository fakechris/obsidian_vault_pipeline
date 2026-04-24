#!/usr/bin/env python3
"""
Auto Evergreen Extractor - 自动Evergreen笔记提取器
从深度解读中自动提取核心概念并创建原子笔记

Usage:
    python3 auto_evergreen_extractor.py --dir 20-Areas/AI-Research/Topics/2026-03/
    python3 auto_evergreen_extractor.py --file article.md
    python3 auto_evergreen_extractor.py --recent 7  # 最近7天的解读

Features:
    - 自动识别核心概念
    - 创建原子化Evergreen笔记
    - 自动双向链接
    - 幂等处理（跳过已存在）
    - 统一日志记录
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from .runtime import VaultLayout, resolve_vault_dir
except ImportError:
    from runtime import VaultLayout, resolve_vault_dir  # type: ignore

try:
    from .identity import canonicalize_note_id
except ImportError:
    from identity import canonicalize_note_id  # type: ignore

try:
    from .promotion_backlinks import upsert_promotions_in_file
except ImportError:
    from promotion_backlinks import upsert_promotions_in_file  # type: ignore

try:
    from .llm_defaults import (
        DEFAULT_LITELLM_TIMEOUT_SECONDS,
        DEFAULT_MINIMAX_MODEL,
        normalize_model_for_api_base,
        resolve_api_base,
        resolve_api_key,
    )
except ImportError:
    from llm_defaults import (  # type: ignore
        DEFAULT_LITELLM_TIMEOUT_SECONDS,
        DEFAULT_MINIMAX_MODEL,
        normalize_model_for_api_base,
        resolve_api_base,
        resolve_api_key,
    )

# Try to import concept registry
try:
    from .concept_registry import ConceptRegistry, STATUS_ACTIVE, STATUS_CANDIDATE
    HAS_REGISTRY = True
except ImportError:
    HAS_REGISTRY = False

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

sys.path.insert(0, str(Path(__file__).parent / "auto_vault"))
try:
    import litellm
    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False

# ========== 配置 ==========
EVERGREEN_DIR = DEFAULT_LAYOUT.evergreen_dir
ATLAS_DIR = DEFAULT_LAYOUT.atlas_dir
LOG_FILE = DEFAULT_LAYOUT.pipeline_log


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
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class LiteLLMClient:
    """LiteLLM客户端"""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MINIMAX_MODEL,
        api_type: str = "anthropic",
        api_key: str | None = None,
        api_base: str | None = None,
        temperature: float = 0.3,
    ):
        self.api_type = api_type
        self._api_key = resolve_api_key(api_key)
        self.api_base = resolve_api_base(api_base)
        self.model = normalize_model_for_api_base(
            model,
            api_type=api_type,
            api_base=self.api_base,
            default_model=DEFAULT_MINIMAX_MODEL,
        )
        self.temperature = temperature

    def generate(self, system_prompt: str, user_prompt: str, max_tokens: int = 4000) -> str:
        if not LITELLM_AVAILABLE:
            raise RuntimeError("litellm not available")

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "timeout": DEFAULT_LITELLM_TIMEOUT_SECONDS,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base

        response = litellm.completion(**kwargs)
        return response.choices[0].message.content or ""


class EvergreenExtractor:
    """Evergreen提取器"""

    SYSTEM_PROMPT = """你是知识提取专家。请从文章中提取3-5个核心概念，每个概念应适合创建为原子化的Evergreen笔记。

Evergreen笔记标准：
1. 原子性：一个概念一个笔记
2. 永久性：时间无关的知识
3. 可链接：能与其他概念连接
4. 命名：用陈述句命名（如"AI agents require persistent memory"）
5. 非元数据：不要把文件名、仓库文件、README、AGENTS.md、package.json、GitHub Action、目录结构、具体 URL 本身当成概念

输出格式（严格JSON数组）：
[
  {
    "concept_name": "Concept-Name-Kebab-Case",
    "title": "AI agents require persistent memory",
    "one_sentence_def": "一句话定义（中文，但保留技术术语英文）",
    "explanation": "详细解释（中文，技术术语不翻译）",
    "importance": "为什么重要",
    "related_concepts": ["Related-Concept-1", "Related-Concept-2"]
  }
]

要求：
- 每个概念必须是一个可独立理解的知识单元
- 技术术语保持英文（如MCP Protocol, function calling）
- 解释部分使用中文
- 最多5个概念，选择最有价值的
- `one_sentence_def` 不能为空，必须是完整定义句
- `concept_name` 必须是稳定的 kebab-case slug，不能包含文件扩展名或 URL 片段
- `title` 应该是紧凑、可复用的知识标题，不要直接复述文件名/README 标题
- `related_concepts` 至少给出 1-3 个真正相关的概念；如果没有合适项，返回空数组
- 如果全文主要是项目包装、目录说明、营销文案、或信息不足以形成稳定知识，请返回空数组
"""

    def __init__(self, llm_client: LiteLLMClient, logger: PipelineLogger):
        self.llm = llm_client
        self.logger = logger

    def extract_concepts(self, file_path: Path, content: str) -> list[dict]:
        """从内容中提取概念"""
        user_prompt = f"""请从以下深度解读中提取3-5个核心概念：

文件: {file_path}

内容（前6000字符）：
```
{content[:6000]}
```

请按JSON格式输出概念列表。"""

        result_text = self.llm.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=4000
        )

        # 尝试解析JSON
        try:
            json_match = re.search(r'\[.*\]', result_text, re.DOTALL)
            if json_match:
                concepts = json.loads(json_match.group())
            else:
                concepts = []
        except json.JSONDecodeError:
            concepts = []

        return concepts

    def create_evergreen_note(self, concept: dict, source_file: Path) -> str:
        """创建Evergreen笔记内容"""
        concept_name = concept.get("concept_name", "Untitled")
        note_id = canonicalize_note_id(concept_name)
        title = concept.get("title", concept_name.replace("-", " "))
        definition = concept.get("one_sentence_def", "")
        explanation = concept.get("explanation", "")
        importance = concept.get("importance", "")
        related = concept.get("related_concepts", [])

        # 构建相关链接
        related_links = "\n".join([f"- [[{c}]]" for c in related if c])

        note = f"""---
note_id: {note_id}
title: "{title}"
type: evergreen
date: {datetime.now().strftime('%Y-%m-%d')}
tags: [evergreen]
aliases: ["{concept_name}"]
---

# {title}

> **一句话定义**: {definition}

## 📝 详细解释

### 是什么？
{explanation}

### 为什么重要？
{importance}

## 🔗 关联概念
{related_links}

## 📚 来源与扩展阅读
- [[{source_file.stem}]]
"""

        return note


class AutoEvergreenExtractor:
    """自动Evergreen提取器"""

    # 默认 promote 阈值：同一概念出现在 3 篇以上深度解读时自动创建
    DEFAULT_PROMOTE_THRESHOLD = 3

    def __init__(self, vault_dir: Path, logger: PipelineLogger):
        self.layout = VaultLayout.from_vault(vault_dir)
        self.vault_dir = self.layout.vault_dir
        self.evergreen_dir = self.layout.evergreen_dir
        self.logger = logger
        self.extractor = None
        self._registry = None  # 延迟加载，per-instance 单次加载

    def _get_registry(self):
        """获取已加载的 registry（每实例只 load 一次）"""
        if self._registry is None and HAS_REGISTRY:
            try:
                self._registry = ConceptRegistry(self.vault_dir).load()
            except Exception:
                pass
        return self._registry

    def init_llm(self, api_key: str | None = None, api_base: str | None = None):
        """初始化LLM"""
        llm_client = LiteLLMClient(
            api_key=api_key,
            api_base=api_base,
            model=DEFAULT_MINIMAX_MODEL,
            api_type="anthropic"
        )
        self.extractor = EvergreenExtractor(llm_client, self.logger)

    def evergreen_exists(self, concept_name: str, registry=None) -> bool:
        """检查Evergreen笔记是否已存在（registry优先，文件系统备选）"""
        # Check registry first (use provided registry or instance cache)
        reg = registry or self._get_registry()
        if reg:
            try:
                if reg.has_active_slug(concept_name):
                    return True
                # Also check by alias
                if reg.find_by_alias(concept_name):
                    return True
            except Exception:
                pass  # Fall back to filesystem

        # Fall back to filesystem check
        possible_paths = [
            self.evergreen_dir / f"{concept_name}.md",
            self.evergreen_dir / f"{concept_name.replace('-', '_')}.md",
        ]
        return any(p.exists() for p in possible_paths)

    def process_file(
        self,
        file_path: Path,
        dry_run: bool = False,
        auto_promote: bool = False,
        promote_threshold: int = DEFAULT_PROMOTE_THRESHOLD,
    ) -> dict:
        """处理单个文件 - 将提取的概念添加到candidate队列或自动创建

        Args:
            file_path: 要处理的文件路径
            dry_run: 预览模式，不写入任何更改
            auto_promote: 是否自动 promote 高 source_count 的候选
            promote_threshold: 自动 promote 的 source_count 阈值
        """
        result = {
            "file": str(file_path),
            "concepts_extracted": 0,
            "concepts_created": 0,
            "concepts_skipped": 0,
            "candidates_added": 0,
            "concepts_promoted": 0,
            "concepts": []
        }
        registry_needs_save = False

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 提取概念
            concepts = self.extractor.extract_concepts(file_path, content)
            result["concepts_extracted"] = len(concepts)

            # 获取已加载的 registry（每文件一次，不是每概念一次）
            registry = self._get_registry()

            for concept in concepts:
                concept_name = concept.get("concept_name")
                if not concept_name:
                    continue

                concept_info = {
                    "name": concept_name,
                    "status": "pending"
                }

                # 检查是否已存在（registry或文件系统）
                if self.evergreen_exists(concept_name, registry=registry):
                    concept_info["status"] = "exists"
                    result["concepts_skipped"] += 1
                    result["concepts"].append(concept_info)
                    continue

                if dry_run:
                    concept_info["status"] = "dry_run"
                    result["candidates_added"] += 1
                    result["concepts"].append(concept_info)
                    continue

                # 添加到candidate队列（而不是直接创建active Evergreen）
                if registry:
                    try:
                        entry = registry.upsert_candidate(
                            slug=concept_name,
                            title=concept.get("title", concept_name.replace("-", " ")),
                            definition=concept.get("one_sentence_def", ""),
                            area="general",
                            aliases=[concept_name],
                        )

                        # Auto-promote: source_count 达到阈值时自动创建文件
                        if auto_promote and entry.source_count >= promote_threshold:
                            registry.save()
                            from .promote_candidates import promote_candidate, write_candidate_file

                            write_candidate_file(
                                self.vault_dir,
                                entry,
                                dry_run=False,
                                concept_data=concept,
                                source_file=file_path,
                            )

                            mutation = promote_candidate(self.vault_dir, concept_name, dry_run=False)
                            output_path = self.evergreen_dir / f"{concept_name}.md"
                            self._registry = ConceptRegistry(self.vault_dir).load()
                            registry = self._registry

                            concept_info["status"] = "promoted_created"
                            concept_info["path"] = str(output_path)
                            concept_info["mutation"] = mutation.to_dict()
                            result["concepts_promoted"] += 1
                            result["concepts_created"] += 1

                            self.logger.log("evergreen_auto_promoted", {
                                "concept": concept_name,
                                "source": str(file_path.name),
                                "source_count": entry.source_count,
                                "path": str(output_path),
                                "mutation": mutation.to_dict(),
                            })
                            # Phase 38.C: write the promotion as a real
                            # wikilink back into the source so the graph
                            # picks it up via the standard scan.
                            try:
                                upsert_promotions_in_file(file_path, [concept_name])
                            except Exception:
                                pass
                        else:
                            from .promote_candidates import write_candidate_file

                            write_candidate_file(
                                self.vault_dir,
                                entry,
                                dry_run=False,
                                concept_data=concept,
                                source_file=file_path,
                            )
                            concept_info["status"] = "candidate_added"
                            result["candidates_added"] += 1
                            registry_needs_save = True
                    except ValueError:
                        # Already exists
                        concept_info["status"] = "exists"
                        result["concepts_skipped"] += 1
                    except Exception as e:
                        concept_info["status"] = "error"
                        concept_info["error"] = str(e)
                else:
                    # Fallback: create directly (legacy behavior)
                    note_content = self.extractor.create_evergreen_note(concept, file_path)
                    output_path = self.evergreen_dir / f"{concept_name}.md"
                    self.evergreen_dir.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "w", encoding="utf-8") as f:
                        f.write(note_content)
                    concept_info["status"] = "created"
                    concept_info["path"] = str(output_path)
                    result["concepts_created"] += 1

                    self.logger.log("evergreen_created", {
                        "concept": concept_name,
                        "source": str(file_path.name),
                        "path": str(output_path)
                    })
                    # Phase 38.C: write backlink into source MD.
                    try:
                        upsert_promotions_in_file(file_path, [concept_name])
                    except Exception:
                        pass

                result["concepts"].append(concept_info)

            # 文件内所有概念处理完毕后，一次性保存 registry（而非每概念保存一次）
            if registry and registry_needs_save and not dry_run:
                try:
                    registry.save()
                except Exception:
                    pass

        except Exception as e:
            result["error"] = str(e)
            self.logger.log("evergreen_error", {"file": str(file_path), "error": str(e)})

        return result

    def process_directory(
        self,
        directory: Path,
        dry_run: bool = False,
        auto_promote: bool = False,
        promote_threshold: int = DEFAULT_PROMOTE_THRESHOLD,
    ) -> list[dict]:
        """处理整个目录"""
        if not directory.exists():
            return []

        # 只处理深度解读文件
        files = list(directory.glob("*_深度解读.md"))

        results = []
        for file_path in files:
            print(f"  Processing: {file_path.name}")
            result = self.process_file(
                file_path,
                dry_run=dry_run,
                auto_promote=auto_promote,
                promote_threshold=promote_threshold,
            )
            results.append(result)
            print(f"    Extracted: {result['concepts_extracted']}, "
                  f"Candidates: {result['candidates_added']}, "
                  f"Promoted: {result.get('concepts_promoted', 0)}, "
                  f"Skipped: {result['concepts_skipped']}")

        return results


def build_extraction_summary(
    results: list[dict[str, Any]],
    *,
    dry_run: bool,
    auto_promote: bool,
    promote_threshold: int,
    source_scope: dict[str, Any],
) -> dict[str, Any]:
    """Build a structured absorb summary payload from per-file extraction results."""
    return {
        "mode": "absorb",
        "dry_run": dry_run,
        "auto_promote": auto_promote,
        "promote_threshold": promote_threshold,
        "source_scope": source_scope,
        "summary": {
            "files_processed": len(results),
            "concepts_extracted": sum(r.get("concepts_extracted", 0) for r in results),
            "candidates_added": sum(r.get("candidates_added", 0) for r in results),
            "concepts_promoted": sum(r.get("concepts_promoted", 0) for r in results),
            "concepts_created": sum(r.get("concepts_created", 0) for r in results),
            "concepts_skipped": sum(r.get("concepts_skipped", 0) for r in results),
            "errors": sum(1 for r in results if r.get("error")),
        },
        "results": results,
    }


def _collect_absorb_targets(
    layout: VaultLayout,
    *,
    file_path: Path | None = None,
    directory: Path | None = None,
    recent: int | None = None,
) -> list[Path]:
    if file_path:
        return [file_path]
    if directory:
        if not directory.exists():
            return []
        return sorted(directory.glob("*_深度解读.md"))
    if recent:
        areas_root = layout.vault_dir / "20-Areas"
        area_dirs = (
            sorted(path for path in areas_root.iterdir() if path.is_dir() and (path / "Topics").exists())
            if areas_root.exists()
            else []
        )
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=recent)
        month_names = {
            (now - timedelta(days=days_ago)).strftime("%Y-%m")
            for days_ago in range(recent)
        }
        ordered: list[Path] = []
        seen: set[str] = set()
        for area_dir in area_dirs:
            for month_name in sorted(month_names):
                month_dir = area_dir / "Topics" / month_name
                if not month_dir.exists():
                    continue
                for candidate in sorted(month_dir.glob("*_深度解读.md")):
                    try:
                        modified_at = datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc)
                    except OSError:
                        continue
                    if modified_at < cutoff:
                        continue
                    key = str(candidate.resolve())
                    if key in seen:
                        continue
                    seen.add(key)
                    ordered.append(candidate)
        return ordered
    raise ValueError("one of file_path, directory, or recent must be provided")


def run_absorb_workflow(
    vault_dir: Path,
    *,
    file_path: Path | None = None,
    directory: Path | None = None,
    recent: int | None = None,
    dry_run: bool = False,
    auto_promote: bool = False,
    promote_threshold: int = AutoEvergreenExtractor.DEFAULT_PROMOTE_THRESHOLD,
    api_key: str | None = None,
    api_base: str | None = None,
    verbose: bool = False,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    layout = VaultLayout.from_vault(vault_dir)
    load_env_file(layout.vault_dir)

    logger = PipelineLogger(layout.pipeline_log)
    extractor = AutoEvergreenExtractor(layout.vault_dir, logger)
    extractor.init_llm(api_key=api_key, api_base=api_base)
    if verbose:
        print("✓ LLM Client initialized")

    if directory and progress_callback is None and hasattr(extractor, "process_directory"):
        results = extractor.process_directory(
            directory,
            dry_run=dry_run,
            auto_promote=auto_promote,
            promote_threshold=promote_threshold,
        )
        payload = build_extraction_summary(
            results,
            dry_run=dry_run,
            auto_promote=auto_promote,
            promote_threshold=promote_threshold,
            source_scope={
                "file": str(file_path) if file_path else None,
                "dir": str(directory) if directory else None,
                "recent": recent,
            },
        )
        logger.log(
            "evergreen_extraction_complete",
            {
                **payload["summary"],
                "auto_promote": auto_promote,
                "dry_run": dry_run,
                **payload["source_scope"],
            },
        )
        return payload

    targets = _collect_absorb_targets(
        layout,
        file_path=file_path,
        directory=directory,
        recent=recent,
    )

    if verbose and directory:
        print(f"\nProcessing directory: {directory}")
    elif verbose and file_path:
        print(f"\nProcessing file: {file_path}")
    elif verbose and recent:
        print(f"\nProcessing recent deep dives: {recent} day window")

    results: list[dict[str, Any]] = []
    files_failed = 0
    for index, target in enumerate(targets, start=1):
        if verbose:
            print(f"  Processing: {target.name}")
        result = extractor.process_file(
            target,
            dry_run=dry_run,
            auto_promote=auto_promote,
            promote_threshold=promote_threshold,
        )
        results.append(result)
        if result.get("error"):
            files_failed += 1
        if verbose:
            print(
                f"    Extracted: {result['concepts_extracted']}, "
                f"Candidates: {result['candidates_added']}, "
                f"Promoted: {result.get('concepts_promoted', 0)}, "
                f"Skipped: {result['concepts_skipped']}"
            )
        if progress_callback is not None:
            progress_callback(
                {
                    "event_type": "absorb_file_processed",
                    "file": target.name,
                    "current_item": target.name,
                    "files_total": len(targets),
                    "files_done": index,
                    "files_failed": files_failed,
                    "result": result,
                }
            )

    payload = build_extraction_summary(
        results,
        dry_run=dry_run,
        auto_promote=auto_promote,
        promote_threshold=promote_threshold,
        source_scope={
            "file": str(file_path) if file_path else None,
            "dir": str(directory) if directory else None,
            "recent": recent,
        },
    )

    logger.log(
        "evergreen_extraction_complete",
        {
            **payload["summary"],
            "auto_promote": auto_promote,
            "dry_run": dry_run,
            **payload["source_scope"],
        },
    )
    return payload


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="自动Evergreen笔记提取器")
    parser.add_argument("--dir", type=Path, help="处理目录")
    parser.add_argument("--file", type=Path, help="处理单个文件")
    parser.add_argument("--recent", type=int, help="处理最近N天的深度解读")
    parser.add_argument("--dry-run", action="store_true", help="预览模式")
    parser.add_argument("--auto-promote", action="store_true",
                        help="自动 promote source_count >= threshold 的候选概念")
    parser.add_argument("--promote-threshold", type=int,
                        default=AutoEvergreenExtractor.DEFAULT_PROMOTE_THRESHOLD,
                        help=f"自动 promote 的 source_count 阈值 (默认: {AutoEvergreenExtractor.DEFAULT_PROMOTE_THRESHOLD})")
    parser.add_argument("--api-key", help="API Key")
    parser.add_argument("--api-base", help="API Base URL")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault根目录")
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON 汇总")
    args = parser.parse_args(argv)

    layout = VaultLayout.from_vault(args.vault_dir or VAULT_DIR)

    try:
        payload = run_absorb_workflow(
            layout.vault_dir,
            file_path=args.file,
            directory=args.dir,
            recent=args.recent,
            dry_run=args.dry_run,
            auto_promote=args.auto_promote,
            promote_threshold=args.promote_threshold,
            api_key=args.api_key,
            api_base=args.api_base,
            verbose=not args.json,
        )
    except Exception as e:
        if args.json:
            print(json.dumps({"mode": "absorb", "error": str(e)}, ensure_ascii=False, indent=2))
        else:
            print(f"✗ {e}")
        sys.exit(1)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"\n{'='*60}")
    print(f"EVERGREEN EXTRACTION COMPLETE")
    print(f"{'='*60}")
    print(f"Files processed: {payload['summary']['files_processed']}")
    print(f"Concepts extracted: {payload['summary']['concepts_extracted']}")
    print(f"Candidates added: {payload['summary']['candidates_added']}")
    if args.auto_promote:
        print(f"Concepts auto-promoted: {payload['summary']['concepts_promoted']}")
        print(f"Files created: {payload['summary']['concepts_created']}")
    print(f"Concepts skipped (exists): {payload['summary']['concepts_skipped']}")
    print()
    if args.auto_promote:
        print("Note: High-confidence concepts have been auto-promoted and files created.")
    else:
        print("Note: Extracted concepts are added to candidate queue.")
        print("Use --auto-promote to automatically create files for high source_count concepts.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
