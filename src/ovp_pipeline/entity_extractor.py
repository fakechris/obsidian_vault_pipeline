"""
Entity Extractor — LLM-based NER + alias matching for typed entity extraction.

Mirrors the LLM call pattern from auto_evergreen_extractor.py but focuses on
extracting *named entities* (person, company, tool, project, paper, event)
rather than abstract concepts.

Flow:
  1. Send markdown content to LLM with NER prompt
  2. Parse JSON response → [{text, kind, confidence, snippet}]
  3. Alias lookup against EntityRegistry
     a. Hit → update mentioned_in_count
     b. Miss + confidence >= 0.8 → upsert_candidate
     c. Miss + confidence < 0.8 → skip (log as pending)

Design: uses the same litellm infrastructure as EvergreenExtractor.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .entity_registry import (
    ENTITY_LAYER_KINDS,
    EntityEntry,
    EntityRegistry,
    is_entity_kind,
)
from .identity import canonicalize_note_id
from .object_kinds import normalize_kind

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EntityMention:
    """A single entity mention extracted by the LLM."""

    text: str
    kind: str
    confidence: float
    snippet: str = ""
    resolved_slug: str | None = None
    resolution: str = "unresolved"  # "alias_hit" | "new_candidate" | "skipped" | "unresolved"

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "kind": self.kind,
            "confidence": self.confidence,
            "snippet": self.snippet,
            "resolved_slug": self.resolved_slug,
            "resolution": self.resolution,
        }


@dataclass
class ExtractionResult:
    """Result of entity extraction for a single document."""

    source_file: str
    mentions: list[EntityMention] = field(default_factory=list)
    candidates_created: int = 0
    existing_matched: int = 0
    skipped_low_confidence: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "mentions": [m.to_dict() for m in self.mentions],
            "candidates_created": self.candidates_created,
            "existing_matched": self.existing_matched,
            "skipped_low_confidence": self.skipped_low_confidence,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# NER Prompt
# ---------------------------------------------------------------------------

ENTITY_NER_SYSTEM_PROMPT = """\
你是命名实体识别专家。从给定文本中提取命名实体（人物、公司、工具/产品、项目、论文、事件）。

**不要提取抽象概念**（如"注意力机制"、"强化学习"）——那些属于 Evergreen 概念层。
只提取具有专有名称的实体。

输出格式（严格 JSON 数组）：
[
  {
    "text": "实体的规范名称（英文优先）",
    "kind": "person|company|tool|project|paper|event",
    "confidence": 0.0-1.0,
    "snippet": "包含该实体的原文片段（50字以内）"
  }
]

kind 说明：
- person: 具名人物（如 Andrej Karpathy, Yann LeCun）
- company: 公司/组织（如 OpenAI, Google DeepMind）
- tool: 工具/产品/框架（如 PyTorch, Claude Code, VS Code）
- project: 开源项目/具名项目（如 Linux, Kubernetes）
- paper: 论文（如 "Attention Is All You Need"）
- event: 事件/会议（如 NeurIPS 2024, GPT-4 发布）

要求：
- 每个实体 confidence 按你的确定程度打分
- text 使用实体最常见的规范名称
- 同一实体只提取一次（选 confidence 最高的）
- 最多提取 15 个实体
- 如果文本中没有命名实体，返回空数组 []
"""

CONFIDENCE_THRESHOLD = 0.8


# ---------------------------------------------------------------------------
# EntityExtractor
# ---------------------------------------------------------------------------

class EntityExtractor:
    """Extract named entities from markdown content using LLM NER.

    Parameters
    ----------
    registry : EntityRegistry
        The entity registry for alias resolution and candidate upsertion.
    llm_call : callable, optional
        A callable ``(system_prompt, user_prompt, max_tokens) -> str`` that
        invokes the LLM. When ``None``, extraction is skipped (useful for
        testing alias resolution only).
    confidence_threshold : float
        Minimum confidence to create a new candidate. Default 0.8.
    """

    def __init__(
        self,
        registry: EntityRegistry,
        llm_call: Any | None = None,
        *,
        confidence_threshold: float = CONFIDENCE_THRESHOLD,
    ) -> None:
        self.registry = registry
        self.llm_call = llm_call
        self.confidence_threshold = confidence_threshold

    # ----- LLM call -----

    def _call_llm_ner(self, content: str, source_name: str) -> list[dict[str, Any]]:
        """Call LLM to extract entity mentions from *content*."""
        if self.llm_call is None:
            return []

        user_prompt = f"""请从以下文本中提取命名实体：

文件: {source_name}

内容（前 5000 字符）：
```
{content[:5000]}
```

请按 JSON 格式输出实体列表。"""

        try:
            result_text = self.llm_call(
                ENTITY_NER_SYSTEM_PROMPT,
                user_prompt,
                3000,
            )
        except Exception as exc:
            return [{"_error": str(exc)}]

        return self._parse_llm_response(result_text)

    @staticmethod
    def _parse_llm_response(text: str) -> list[dict[str, Any]]:
        """Parse LLM output, tolerating markdown fences and trailing text."""
        try:
            json_match = re.search(r"\[.*\]", text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
        return []

    # ----- Resolution -----

    def _resolve_and_upsert(
        self,
        mention: EntityMention,
        source_file: str,
    ) -> None:
        """Resolve a mention against the registry; upsert if needed."""
        kind = normalize_kind(mention.kind)
        if kind not in ENTITY_LAYER_KINDS:
            mention.resolution = "skipped"
            return

        existing = self.registry.resolve_mention(mention.text)
        if existing is None:
            slug = canonicalize_note_id(mention.text)
            existing = self.registry.find_by_slug(slug)

        if existing is not None:
            mention.resolved_slug = existing.slug
            mention.resolution = "alias_hit"
            self.registry.update_mentioned_count(existing.slug)
            return

        if mention.confidence >= self.confidence_threshold:
            slug = canonicalize_note_id(mention.text)
            self.registry.upsert_candidate(
                slug=slug,
                title=mention.text,
                entity_type=kind,
                confidence=mention.confidence,
                source_evergreen=source_file,
            )
            mention.resolved_slug = slug
            mention.resolution = "new_candidate"
        else:
            mention.resolution = "skipped"

    # ----- Public API -----

    def extract_entities(
        self,
        content: str,
        source_file: str,
    ) -> ExtractionResult:
        """Extract entities from *content* and resolve against the registry.

        Parameters
        ----------
        content : str
            Markdown content to extract from.
        source_file : str
            Identifier for the source (e.g. filename), used for logging.

        Returns
        -------
        ExtractionResult
        """
        result = ExtractionResult(source_file=source_file)

        raw_mentions = self._call_llm_ner(content, source_file)

        for item in raw_mentions:
            if "_error" in item:
                result.errors.append(item["_error"])
                continue

            text = item.get("text", "").strip()
            kind = item.get("kind", "").strip()
            confidence = float(item.get("confidence", 0.0))
            snippet = item.get("snippet", "")

            if not text or not kind:
                continue

            mention = EntityMention(
                text=text,
                kind=kind,
                confidence=confidence,
                snippet=snippet,
            )

            self._resolve_and_upsert(mention, source_file)
            result.mentions.append(mention)

            if mention.resolution == "alias_hit":
                result.existing_matched += 1
            elif mention.resolution == "new_candidate":
                result.candidates_created += 1
            elif mention.resolution == "skipped":
                result.skipped_low_confidence += 1

        return result

    def extract_entities_from_file(
        self,
        file_path: Path,
    ) -> ExtractionResult:
        """Convenience: read file and extract entities."""
        content = file_path.read_text(encoding="utf-8")
        return self.extract_entities(content, str(file_path.name))


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def make_extractor(
    vault_dir: Path,
    llm_call: Any | None = None,
    *,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> EntityExtractor:
    """Create an EntityExtractor with a freshly loaded registry."""
    registry = EntityRegistry(vault_dir).load()
    return EntityExtractor(
        registry,
        llm_call=llm_call,
        confidence_threshold=confidence_threshold,
    )
