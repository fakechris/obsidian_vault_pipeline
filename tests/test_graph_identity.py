from __future__ import annotations

from openclaw_pipeline.concept_registry import ConceptEntry, ConceptRegistry
from openclaw_pipeline.graph.frontmatter import FrontmatterParser
from openclaw_pipeline.graph.graph_builder import GraphBuilder
from openclaw_pipeline.graph.link_parser import LinkParser


def test_frontmatter_and_link_parser_respect_explicit_note_id(temp_vault):
    note_path = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04-01_Custom_Name.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(
        """---
note_id: canonical-note
title: Custom Note
type: deep_dive
date: 2026-04-01
---

# Custom Note

Links to [[Another Concept]].
""",
        encoding="utf-8",
    )

    meta = FrontmatterParser(temp_vault).parse_file(note_path)
    links = LinkParser(temp_vault).parse_file(note_path)

    assert meta.note_id == "canonical-note"
    assert links[0].source == "canonical-note"
    assert links[0].target == "another-concept"
    assert links[0].target_raw == "Another Concept"


def test_graph_builder_resolves_registry_alias_without_unknown_placeholder(temp_vault):
    registry = ConceptRegistry(temp_vault)
    registry.add_entry(
        ConceptEntry(
            slug="ai-agent",
            title="AI Agent",
            aliases=["AI代理"],
            definition="Autonomous AI system.",
            area="AI",
        )
    )
    registry.save()

    evergreen_path = temp_vault / "10-Knowledge" / "Evergreen" / "AI-Agent.md"
    evergreen_path.write_text(
        """---
note_id: ai-agent
title: AI Agent
type: evergreen
aliases: [AI代理]
date: 2026-01-01
---

# AI Agent
""",
        encoding="utf-8",
    )

    article_path = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04-01_Article.md"
    article_path.parent.mkdir(parents=True, exist_ok=True)
    article_path.write_text(
        """---
note_id: article-note
title: Article Note
type: deep_dive
date: 2026-04-01
---

# Article Note

Mentions [[AI代理]].
""",
        encoding="utf-8",
    )

    nodes, edges = GraphBuilder(temp_vault).build_from_directory(temp_vault, recursive=True)

    node_ids = {node["note_id"] for node in nodes}
    edge_targets = {edge["target"] for edge in edges if edge["source"] == "article-note"}
    unknown_nodes = [node for node in nodes if node["note_type"] == "unknown"]

    assert "ai-agent" in node_ids
    assert edge_targets == {"ai-agent"}
    assert unknown_nodes == []
