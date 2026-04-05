#!/usr/bin/env python3
"""
Concept Resolver - Mention extraction, candidate retrieval, and link resolution.

The resolver takes article content and determines:
1. Which surface forms are mentioned (mention extraction)
2. Which registry candidates match each mention (retrieval)
3. What action to take: link_existing, create_candidate, or no_link (resolution)
4. How to render the final deterministic wikilink (rendering)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Optional litellm import
try:
    import litellm
    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False


# ========== Prompt Templates (from 05-prompts.md) ==========

MENTION_EXTRACTOR_PROMPT = """你是一个知识库术语抽取器。你的任务是从文章中抽取"可能值得链接到 Evergreen 的 mention"。

判定标准：
1. 优先抽取稳定概念、方法、框架、理论、组织、产品、技术术语。
2. 不要抽取纯修辞表达、一次性事件标题、情绪化短语。
3. 相同概念的重复出现只保留一次。
4. 每个 mention 输出一个最自然的 surface form。
5. 只输出 JSON。

输出格式：
{{"mentions": [{{"surface": "...", "type": "concept", "reason": "...", "local_context": "..."}}]}}
"""

CONCEPT_RESOLVER_PROMPT = """你是一个 concept resolver。你不能自由创造链接目标，你只能在给定候选中做决策。

任务：
对每个 mention，在 candidate_concepts 中判断：
- link_existing
- create_candidate
- no_link

决策原则：
1. 只是译名/缩写/旧称，优先 link_existing。
2. 与已有概念语义高度重合，优先 link_existing，不要重复造概念。
3. 只有当 mention 具有长期复用价值、能被一句话定义、且无法并入现有概念时，才 create_candidate。
4. 如果 mention 只在本文有效、太具体、太像段落话题或热点表述，选择 no_link。
5. 只输出 JSON。

输入：
{{"article_area": "{{area}}", "mentions": [...], "candidate_concepts": [...]}}

输出：
{{"resolutions": [{{"surface": "...", "action": "link_existing"|"create_candidate"|"no_link", "slug": "...", "display": "...", "confidence": 0.0, "proposed_slug": "...", "title": "...", "definition": "..."}}]}}
"""


# ========== Data Classes ==========

@dataclass
class Mention:
    """A mention extracted from article content."""
    surface: str
    mention_type: str = "concept"
    reason: str = ""
    local_context: str = ""


@dataclass
class ResolutionDecision:
    """A resolution decision for a single surface form."""
    surface: str
    action: str  # "link_existing", "create_candidate", "no_link"
    slug: str = ""
    display: str = ""
    confidence: float = 0.0
    proposed_slug: str = ""
    title: str = ""
    definition: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = {
            "surface": self.surface,
            "action": self.action,
        }
        if self.slug:
            result["slug"] = self.slug
        if self.display:
            result["display"] = self.display
        if self.confidence:
            result["confidence"] = self.confidence
        if self.proposed_slug:
            result["proposed_slug"] = self.proposed_slug
        if self.title:
            result["title"] = self.title
        if self.definition:
            result["definition"] = self.definition
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResolutionDecision:
        return cls(
            surface=data["surface"],
            action=data["action"],
            slug=data.get("slug", ""),
            display=data.get("display", ""),
            confidence=data.get("confidence", 0.0),
            proposed_slug=data.get("proposed_slug", ""),
            title=data.get("title", ""),
            definition=data.get("definition", ""),
        )


@dataclass
class LinkResolutionSidecar:
    """Sidecar file for link resolution decisions."""
    article: str
    resolver_version: str = "v2"
    area: str = ""
    decisions: list[ResolutionDecision] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "article": self.article,
            "resolver_version": self.resolver_version,
            "area": self.area,
            "decisions": [d.to_dict() for d in self.decisions],
        }

    def write(self, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)


# ========== Mention Extractor ==========

class MentionExtractor:
    """Extract potential concept mentions from article markdown."""

    def extract_from_wikilinks(self, content: str) -> list[Mention]:
        """Extract explicit [[wikilinks]] from content."""
        mentions = []
        # Match [[target]] or [[target|display]]
        pattern = r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]'
        seen = set()

        for match in re.finditer(pattern, content):
            surface = match.group(1).strip()
            if surface and surface not in seen:
                seen.add(surface)
                # Get local context (surrounding text)
                start = max(0, match.start() - 50)
                end = min(len(content), match.end() + 50)
                context = content[start:end].replace('\n', ' ').strip()

                mentions.append(Mention(
                    surface=surface,
                    mention_type="explicit_link",
                    reason="Explicit wikilink found in article",
                    local_context=context,
                ))

        return mentions

    def extract_from_llm(self, content: str, area: str, llm_client: Any = None) -> list[Mention]:
        """
        Use LLM to extract high-value concept mentions from article body.

        Falls back to keyword extraction if no LLM available.
        """
        if not LITELLM_AVAILABLE or llm_client is None:
            return self._fallback_keyword_extraction(content)

        # Prepare article excerpt (first 4000 chars for context)
        excerpt = content[:4000]

        prompt_data = {
            "article_area": area,
            "content": excerpt,
        }

        try:
            response, _ = llm_client.generate(
                system_prompt=MENTION_EXTRACTOR_PROMPT,
                user_prompt=json.dumps(prompt_data, ensure_ascii=False),
                max_tokens=2000,
            )

            # Parse JSON response
            data = json.loads(response)
            mentions = []
            for item in data.get("mentions", []):
                mentions.append(Mention(
                    surface=item["surface"],
                    mention_type=item.get("type", "concept"),
                    reason=item.get("reason", ""),
                    local_context=item.get("local_context", ""),
                ))
            return mentions

        except Exception as e:
            print(f"Warning: LLM mention extraction failed: {e}")
            return self._fallback_keyword_extraction(content)

    def _fallback_keyword_extraction(self, content: str) -> list[Mention]:
        """Fallback: extract mentions from content using simple heuristics."""
        mentions = []
        seen = set()

        # Extract H2/H3 headings as potential concepts
        heading_pattern = r'^#{2,3}\s+(.+)$'
        for line in content.split('\n'):
            match = re.match(heading_pattern, line.strip())
            if match:
                surface = match.group(1).strip()
                if surface and surface not in seen and len(surface) > 2:
                    seen.add(surface)
                    mentions.append(Mention(
                        surface=surface,
                        mention_type="heading",
                        reason="Article heading",
                        local_context=line.strip(),
                    ))

        return mentions

    def extract_all(self, content: str, area: str, llm_client: Any = None) -> list[Mention]:
        """Extract all mentions using both explicit links and LLM."""
        all_mentions = []

        # Add explicit wikilinks
        explicit = self.extract_from_wikilinks(content)
        all_mentions.extend(explicit)

        # Dedupe by surface
        seen = {m.surface for m in all_mentions}

        # Add LLM-extracted mentions
        llm_mentions = self.extract_from_llm(content, area, llm_client)
        for m in llm_mentions:
            if m.surface not in seen:
                all_mentions.append(m)
                seen.add(m.surface)

        return all_mentions


# ========== Candidate Retriever ==========

class CandidateRetriever:
    """Retrieve candidate concepts from registry for a given surface form."""

    def __init__(self, registry: Any):
        self.registry = registry

    def retrieve(self, surface: str, contexts: list[str], area: str | None = None,
                 topk: int = 20) -> list[tuple[Any, float]]:
        """
        Retrieve top matching concepts for a surface form.

        Uses 3-stage retrieval:
        1. Exact match (slug, title, alias) - highest score
        2. Lexical match (BM25-style token overlap)
        3. Semantic would go here (embeddings) - placeholder

        Returns: list of (ConceptEntry, score) sorted by descending score.
        """
        candidates: list[tuple[Any, float]] = []

        # Stage 1: Exact match
        exact = self._exact_match(surface)
        for entry, score in exact:
            candidates.append((entry, score))

        # Stage 2: Search-based retrieval
        search_results = self.registry.search(surface, area=area, topk=topk)
        for entry, score in search_results:
            # Avoid duplicates from exact match
            if not any(c[0].slug == entry.slug for c in candidates):
                candidates.append((entry, score * 0.8))  # Discount search scores

        # Sort and dedupe
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:topk]

    def _exact_match(self, surface: str) -> list[tuple[Any, float]]:
        """Exact match on slug, title, or alias."""
        results = []

        # Try slug
        entry = self.registry.find_by_slug(surface)
        if entry:
            results.append((entry, 1.0))

        # Try alias
        entry = self.registry.find_by_alias(surface)
        if entry and not any(e.slug == entry.slug for e, _ in results):
            results.append((entry, 0.95))

        # Try surface-as-title
        entry = self.registry.find_by_surface(surface)
        if entry and not any(e.slug == entry.slug for e, _ in results):
            results.append((entry, 0.9))

        return results


# ========== Concept Resolver ==========

class ConceptResolver:
    """Resolve mentions to concepts using registry and LLM."""

    def __init__(self, registry: Any, llm_client: Any = None):
        self.registry = registry
        self.llm = llm_client

    def resolve_mentions(self, mentions: list[Mention], area: str) -> list[ResolutionDecision]:
        """
        Resolve a list of mentions to resolution decisions.

        For each mention:
        1. Retrieve candidates from registry
        2. Use LLM (or rules) to make resolution decision
        3. Return decision
        """
        decisions = []

        for mention in mentions:
            decision = self._resolve_single(mention, area)
            decisions.append(decision)

        return decisions

    def _resolve_single(self, mention: Mention, area: str) -> ResolutionDecision:
        """Resolve a single mention."""
        # Retrieve candidates
        retriever = CandidateRetriever(self.registry)
        candidates = retriever.retrieve(
            surface=mention.surface,
            contexts=[mention.local_context],
            area=area,
        )

        if not candidates:
            # No candidates: create candidate or no_link
            return self._decide_no_candidates(mention)

        # Use LLM if available
        if LITELLM_AVAILABLE and self.llm is not None:
            return self._resolve_via_llm(mention, candidates, area)
        else:
            # Fallback to rules
            return self._resolve_via_rules(mention, candidates)

    def _decide_no_candidates(self, mention: Mention) -> ResolutionDecision:
        """Decide when no candidates are found."""
        # If mention looks like a real concept (long enough, proper nouns), create candidate
        if len(mention.surface) >= 3 and mention.mention_type in ("concept", "explicit_link"):
            return ResolutionDecision(
                surface=mention.surface,
                action="create_candidate",
                proposed_slug=self._surface_to_slug(mention.surface),
                title=mention.surface,
                confidence=0.5,
            )
        else:
            return ResolutionDecision(
                surface=mention.surface,
                action="no_link",
                confidence=1.0,
            )

    def _resolve_via_llm(self, mention: Mention, candidates: list, area: str) -> ResolutionDecision:
        """Use LLM to resolve a mention."""
        # Build candidate context for prompt
        candidate_context = []
        for entry, score in candidates[:10]:
            candidate_context.append({
                "slug": entry.slug,
                "title": entry.title,
                "aliases": entry.aliases,
                "definition": entry.definition[:200],
                "score": round(score, 2),
            })

        prompt_input = {
            "article_area": area,
            "mentions": [{
                "surface": mention.surface,
                "type": mention.mention_type,
                "reason": mention.reason,
                "local_context": mention.local_context,
            }],
            "candidate_concepts": candidate_context,
        }

        try:
            response, _ = self.llm.generate(
                system_prompt=CONCEPT_RESOLVER_PROMPT,
                user_prompt=json.dumps(prompt_input, ensure_ascii=False),
                max_tokens=2000,
            )

            data = json.loads(response)
            for item in data.get("resolutions", []):
                if item["surface"] == mention.surface:
                    return ResolutionDecision.from_dict(item)

        except Exception as e:
            print(f"Warning: LLM resolution failed: {e}")

        # Fallback to rules on error
        return self._resolve_via_rules(mention, candidates)

    def _resolve_via_rules(self, mention: Mention, candidates: list) -> ResolutionDecision:
        """Rule-based resolution fallback."""
        best_entry, best_score = candidates[0]

        # High confidence exact match
        if best_score >= 0.95:
            return ResolutionDecision(
                surface=mention.surface,
                action="link_existing",
                slug=best_entry.slug,
                display=mention.surface,
                confidence=best_score,
            )

        # Medium confidence - might be alias or related
        if best_score >= 0.5:
            return ResolutionDecision(
                surface=mention.surface,
                action="link_existing",
                slug=best_entry.slug,
                display=mention.surface,
                confidence=best_score * 0.8,
            )

        # Low confidence - create candidate
        return ResolutionDecision(
            surface=mention.surface,
            action="create_candidate",
            proposed_slug=self._surface_to_slug(mention.surface),
            title=mention.surface,
            confidence=best_score,
        )

    def _surface_to_slug(self, surface: str) -> str:
        """Convert surface form to kebab-case slug."""
        # Remove common prefixes/suffixes
        slug = surface.strip()
        # Replace spaces with hyphens
        slug = re.sub(r'\s+', '-', slug)
        # Remove non-alphanumeric (keep hyphens)
        slug = re.sub(r'[^\w\-]', '', slug)
        # Collapse multiple hyphens
        slug = re.sub(r'-+', '-', slug)
        # Lowercase
        slug = slug.lower()
        return slug


# ========== Deterministic Renderer ==========

class LinkRenderer:
    """Render resolved mentions to deterministic wikilinks."""

    def __init__(self, registry: Any):
        self.registry = registry

    def render_wikilink(self, decision: ResolutionDecision) -> str:
        """
        Render a resolution decision to a wikilink string.

        Rules:
        - link_existing: [[slug|surface]]
        - create_candidate: (no link, pure text)
        - no_link: (no link, pure text)
        """
        if decision.action == "link_existing":
            slug = decision.slug or decision.surface
            display = decision.display or decision.surface
            return f"[[{slug}|{display}]]"
        else:
            # No link - return surface as plain text
            return decision.surface

    def render_all(self, content: str, decisions: list[ResolutionDecision]) -> str:
        """
        Replace all surface mentions in content with rendered wikilinks.

        Handles both [[surface]] and [[slug|surface]] formats.
        """
        # Build replacement map
        replacements: dict[str, str] = {}
        for d in decisions:
            rendered = self.render_wikilink(d)
            replacements[d.surface] = rendered

        # Replace [[surface]] patterns
        result = content
        for surface, rendered in replacements.items():
            # Match [[surface]] or [[anything|surface]]
            # First handle [[surface]]
            pattern = rf'\[\[{re.escape(surface)}\]\]'
            result = re.sub(pattern, rendered, result)

            # Also handle [[something|surface]]
            pattern = rf'\[\[[^\]]+\|{re.escape(surface)}\]\]'
            result = re.sub(pattern, rendered, result)

        return result


# ========== High-level API ==========

def resolve_article_links(
    content: str,
    article_stem: str,
    area: str,
    registry: Any,
    llm_client: Any = None,
) -> tuple[str, LinkResolutionSidecar]:
    """
    Full link resolution pipeline for an article.

    1. Extract mentions from content
    2. Resolve each mention to a decision
    3. Render deterministic wikilinks
    4. Return modified content + sidecar

    Returns: (modified_content, sidecar)
    """
    # Step 1: Extract mentions
    extractor = MentionExtractor()
    mentions = extractor.extract_all(content, area, llm_client)

    # Step 2: Resolve mentions
    resolver = ConceptResolver(registry, llm_client)
    decisions = resolver.resolve_mentions(mentions, area)

    # Step 3: Render links
    renderer = LinkRenderer(registry)
    modified_content = renderer.render_all(content, decisions)

    # Step 4: Build sidecar
    sidecar = LinkResolutionSidecar(
        article=article_stem,
        area=area,
        decisions=decisions,
    )

    return modified_content, sidecar
