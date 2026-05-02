#!/usr/bin/env python3
"""Seed 20-30 Entity candidates into the EntityRegistry + generate candidate .md files.

These are high-confidence named entities extracted from existing vault content.
Run once to bootstrap the Entity layer with known entities.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ovp_pipeline.entity_registry import EntityRegistry
from ovp_pipeline.promote_entities import write_candidate_file

VAULT_DIR = Path(__file__).resolve().parent.parent

SEED_ENTITIES = [
    {
        "title": "Anthropic",
        "entity_type": "company",
        "aliases": ["Anthropic AI"],
        "definition": "AI safety company, creator of Claude series models.",
    },
    {
        "title": "OpenAI",
        "entity_type": "company",
        "aliases": ["Open AI"],
        "definition": "AI research lab, creator of GPT series and ChatGPT.",
    },
    {
        "title": "Google DeepMind",
        "entity_type": "company",
        "aliases": ["DeepMind", "Google AI"],
        "definition": "Google's AI research division, developer of Gemini models.",
    },
    {
        "title": "Claude",
        "entity_type": "tool",
        "aliases": ["Claude AI", "Claude Code", "Claude Sonnet", "Claude Opus"],
        "definition": "Anthropic's family of AI assistant models.",
    },
    {
        "title": "GPT",
        "entity_type": "tool",
        "aliases": ["GPT-4", "GPT-4o", "GPT-5", "ChatGPT"],
        "definition": "OpenAI's family of large language models.",
    },
    {
        "title": "Gemini",
        "entity_type": "tool",
        "aliases": ["Gemini Pro", "Gemini Ultra"],
        "definition": "Google DeepMind's multimodal AI model family.",
    },
    {
        "title": "Model Context Protocol",
        "entity_type": "tool",
        "aliases": ["MCP"],
        "definition": "Anthropic's open protocol for standardized LLM tool integration.",
    },
    {
        "title": "LangChain",
        "entity_type": "tool",
        "aliases": [],
        "definition": "Framework for building applications with large language models.",
    },
    {
        "title": "Bruno",
        "entity_type": "tool",
        "aliases": ["Bruno API Client"],
        "definition": "Git-native open-source API client, alternative to Postman.",
    },
    {
        "title": "Stax",
        "entity_type": "tool",
        "aliases": [],
        "definition": "Complete toolkit for AI model evaluation and testing.",
    },
    {
        "title": "Tessl",
        "entity_type": "tool",
        "aliases": [],
        "definition": "Agent enablement platform for building AI agents.",
    },
    {
        "title": "Obsidian",
        "entity_type": "tool",
        "aliases": ["Obsidian.md"],
        "definition": "Markdown-based knowledge management and note-taking application.",
    },
    {
        "title": "React",
        "entity_type": "tool",
        "aliases": ["React.js", "ReactJS"],
        "definition": "Meta's JavaScript library for building user interfaces.",
    },
    {
        "title": "Python",
        "entity_type": "tool",
        "aliases": ["CPython"],
        "definition": "High-level programming language widely used in AI/ML.",
    },
    {
        "title": "Andrej Karpathy",
        "entity_type": "person",
        "aliases": ["Karpathy"],
        "definition": "AI researcher, former Tesla AI director, OpenAI founding member.",
    },
    {
        "title": "Dario Amodei",
        "entity_type": "person",
        "aliases": [],
        "definition": "CEO and co-founder of Anthropic.",
    },
    {
        "title": "Sam Altman",
        "entity_type": "person",
        "aliases": [],
        "definition": "CEO of OpenAI.",
    },
    {
        "title": "Sid Sijbrandij",
        "entity_type": "person",
        "aliases": ["Sid"],
        "definition": "Co-founder and CEO of GitLab.",
    },
    {
        "title": "Transformer",
        "entity_type": "paper",
        "aliases": ["Attention Is All You Need", "Transformer Architecture"],
        "definition": "Neural network architecture based on self-attention, introduced in 2017.",
    },
    {
        "title": "ReAct",
        "entity_type": "paper",
        "aliases": ["ReAct Framework", "Reasoning + Acting"],
        "definition": "Paradigm combining reasoning traces with action steps for LLM agents.",
    },
    {
        "title": "RAG",
        "entity_type": "tool",
        "aliases": ["Retrieval-Augmented Generation"],
        "definition": "Technique combining retrieval with generation for grounded LLM responses.",
    },
    {
        "title": "GitHub",
        "entity_type": "company",
        "aliases": ["GitHub Inc"],
        "definition": "Microsoft-owned platform for software development and version control.",
    },
    {
        "title": "Meta",
        "entity_type": "company",
        "aliases": ["Meta Platforms", "Facebook"],
        "definition": "Technology company, developer of Llama models and React framework.",
    },
    {
        "title": "Llama",
        "entity_type": "tool",
        "aliases": ["Llama 2", "Llama 3", "Meta Llama"],
        "definition": "Meta's family of open-source large language models.",
    },
    {
        "title": "Cursor",
        "entity_type": "tool",
        "aliases": ["Cursor IDE", "Cursor Editor"],
        "definition": "AI-first code editor built on VS Code with integrated AI assistance.",
    },
    {
        "title": "GBrain",
        "entity_type": "tool",
        "aliases": ["gbrain"],
        "definition": "Persistent memory layer for AI coding agents.",
    },
    {
        "title": "ClawManager",
        "entity_type": "tool",
        "aliases": ["Claw Manager"],
        "definition": "Multi-agent session management tool for coordinating AI agents.",
    },
    {
        "title": "wecom-cli",
        "entity_type": "tool",
        "aliases": ["WecomCLI"],
        "definition": "Command-line interface for WeCom (企业微信) automation.",
    },
]


def main():
    registry = EntityRegistry(VAULT_DIR).load()
    created = 0
    skipped = 0

    from ovp_pipeline.identity import canonicalize_note_id

    for seed in SEED_ENTITIES:
        title = seed["title"]
        slug = canonicalize_note_id(title)
        for alias in [title] + seed.get("aliases", []):
            match = registry.resolve_mention(alias)
            if match:
                print(f"  SKIP: '{title}' — already exists as '{match.slug}'")
                skipped += 1
                break
        else:
            entry = registry.upsert_candidate(
                slug=slug,
                title=title,
                entity_type=seed["entity_type"],
                aliases=seed.get("aliases", []),
                definition=seed.get("definition", ""),
                confidence=0.95,
            )
            write_candidate_file(VAULT_DIR, entry, dry_run=False)
            created += 1
            print(f"  CREATE: '{title}' [{seed['entity_type']}] → {entry.slug}")

    registry.save()
    print(f"\nDone: {created} created, {skipped} skipped, {len(registry)} total in registry.")


if __name__ == "__main__":
    main()
