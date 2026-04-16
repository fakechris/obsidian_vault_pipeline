from __future__ import annotations

import pytest


def test_build_views_help_mentions_primary_pack(capsys):
    from openclaw_pipeline.commands.build_views import main

    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0

    output = " ".join(capsys.readouterr().out.split())
    assert "compatibility pack" in output
    assert "research-tech" in output


def test_build_views_command_requires_object_id_for_object_page(temp_vault):
    from openclaw_pipeline.commands.build_views import main

    try:
        main(
            [
                "--vault-dir",
                str(temp_vault),
                "--pack",
                "default-knowledge",
                "--view",
                "object/page",
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected object/page to require --object-id")


def test_build_views_command_requires_cluster_id_for_cluster_crystal(temp_vault):
    from openclaw_pipeline.commands.build_views import main

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--vault-dir",
                str(temp_vault),
                "--pack",
                "default-knowledge",
                "--view",
                "cluster/crystal",
            ]
        )

    assert exc_info.value.code == 2


def test_build_views_command_writes_compiled_markdown(temp_vault):
    from openclaw_pipeline.commands.build_views import main
    from openclaw_pipeline.runtime import VaultLayout

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Agent-Harness.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        """---
note_id: agent-harness
title: Agent Harness
type: evergreen
date: 2026-04-10
---

# Agent Harness
""",
        encoding="utf-8",
    )

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--view",
            "overview/domain",
        ]
    )

    layout = VaultLayout.from_vault(temp_vault)
    artifacts = sorted(layout.compiled_views_dir.rglob("*.md"))

    assert result == 0
    assert artifacts
    content = artifacts[0].read_text(encoding="utf-8")
    assert "# overview/domain" in content
    assert "Agent Harness" in content


def test_build_views_command_respects_input_source_kinds(temp_vault):
    from openclaw_pipeline.commands.build_views import main
    from openclaw_pipeline.runtime import VaultLayout

    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "Evergreen-Only.md"
    evergreen.parent.mkdir(parents=True, exist_ok=True)
    evergreen.write_text(
        """---
title: Evergreen Only
type: evergreen
---
""",
        encoding="utf-8",
    )

    query = temp_vault / "20-Areas" / "Queries" / "Saved-Answer.md"
    query.parent.mkdir(parents=True, exist_ok=True)
    query.write_text(
        """---
title: Saved Answer
type: query
---
""",
        encoding="utf-8",
    )

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--view",
            "saved_answer/query",
        ]
    )

    layout = VaultLayout.from_vault(temp_vault)
    artifacts = sorted(layout.compiled_views_dir.rglob("*.md"))

    assert result == 0
    assert artifacts
    content = artifacts[-1].read_text(encoding="utf-8")
    assert "Saved Answer" in content
    assert "Evergreen Only" not in content


def test_build_views_command_can_render_extraction_overview(temp_vault):
    import json
    from pathlib import Path

    from openclaw_pipeline.commands.build_views import main
    from openclaw_pipeline.derived.paths import extraction_run_path
    from openclaw_pipeline.extraction.results import ExtractionRecord, ExtractionRunResult
    from openclaw_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    artifact = extraction_run_path(
        layout,
        pack_name="default-knowledge",
        profile_name="tech/doc_structure",
        source_path=Path("50-Inbox/01-Raw/example.md"),
    )
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            ExtractionRunResult(
                pack_name="default-knowledge",
                profile_name="tech/doc_structure",
                source_path="50-Inbox/01-Raw/example.md",
                records=[ExtractionRecord(values={"section_title": "Architecture"}, spans=[])],
            ).to_dict(),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--view",
            "overview/extraction",
        ]
    )

    artifacts = sorted(layout.compiled_views_dir.rglob("*.md"))

    assert result == 0
    assert artifacts
    content = artifacts[-1].read_text(encoding="utf-8")
    assert "# overview/extraction" in content
    assert "tech/doc_structure" in content
    assert "50-Inbox/01-Raw/example.md" in content


def test_build_views_command_can_render_extraction_overview_for_research_tech(temp_vault):
    import json
    from pathlib import Path

    from openclaw_pipeline.commands.build_views import main
    from openclaw_pipeline.derived.paths import extraction_run_path
    from openclaw_pipeline.extraction.results import ExtractionRecord, ExtractionRunResult
    from openclaw_pipeline.runtime import VaultLayout

    layout = VaultLayout.from_vault(temp_vault)
    artifact = extraction_run_path(
        layout,
        pack_name="research-tech",
        profile_name="tech/doc_structure",
        source_path=Path("50-Inbox/01-Raw/research.md"),
    )
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            ExtractionRunResult(
                pack_name="research-tech",
                profile_name="tech/doc_structure",
                source_path="50-Inbox/01-Raw/research.md",
                records=[ExtractionRecord(values={"section_title": "Architecture"}, spans=[])],
            ).to_dict(),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "research-tech",
            "--view",
            "overview/extraction",
        ]
    )

    output_path = layout.compiled_views_dir / "research-tech" / "overview__extraction.md"

    assert result == 0
    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert "# overview/extraction" in content
    assert "research.md" in content


def test_build_views_command_can_materialize_object_page_from_truth_store(temp_vault):
    from openclaw_pipeline.commands.build_views import main
    from openclaw_pipeline.knowledge_index import list_contradictions, rebuild_knowledge_index, resolve_contradictions
    from openclaw_pipeline.runtime import VaultLayout
    from openclaw_pipeline.truth_api import record_review_action

    source = temp_vault / "10-Knowledge" / "Evergreen" / "Source.md"
    target = temp_vault / "10-Knowledge" / "Evergreen" / "Target.md"
    conflict = temp_vault / "10-Knowledge" / "Evergreen" / "Conflict.md"

    source.write_text(
        """---
note_id: source-note
title: Source Note
type: evergreen
date: 2026-04-10
---

# Source Note

Source note supports the runtime architecture.

Links to [[target-note]].
""",
        encoding="utf-8",
    )
    target.write_text(
        """---
note_id: target-note
title: Target Note
type: evergreen
date: 2026-04-10
---

# Target Note

Target note captures downstream effects.
""",
        encoding="utf-8",
    )
    conflict.write_text(
        """---
note_id: conflict-note
title: Conflict Note
type: evergreen
date: 2026-04-10
---

# Conflict Note

Source note does not support the runtime architecture.
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)
    contradiction_id = list_contradictions(temp_vault, limit=5)[0]["contradiction_id"]
    resolve_contradictions(
        temp_vault,
        [contradiction_id],
        status="needs_human",
        note="Needs editorial review.",
    )
    record_review_action(
        temp_vault,
        event_type="ui_contradictions_resolved",
        slug="source-note",
        payload={
            "object_ids": ["source-note", "conflict-note"],
            "contradiction_ids": [contradiction_id],
            "status": "needs_human",
            "note": "Needs editorial review.",
            "rebuilt_object_ids": [],
        },
    )

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--view",
            "object/page",
            "--object-id",
            "source-note",
        ]
    )

    layout = VaultLayout.from_vault(temp_vault)
    content = (layout.compiled_views_dir / "default-knowledge" / "objects" / "source-note.md").read_text(encoding="utf-8")

    assert result == 0
    assert "# Source Note" in content
    assert "## Compiled Summary" in content
    assert "Source note supports the runtime architecture." in content
    assert "## Claims" in content
    assert "## Related Objects" in content
    assert "[[target-note]]" in content
    assert "## Contradictions" in content
    assert "source note" in content
    assert "needs_human" in content
    assert "Needs editorial review." in content


def test_build_views_command_escapes_like_wildcards_in_object_id_lookup(temp_vault):
    from openclaw_pipeline.commands.build_views import main
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout
    import sqlite3

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Baseline.md"
    note.write_text(
        """---
note_id: baseline-note
title: Baseline Note
type: evergreen
date: 2026-04-10
---

# Baseline Note

Baseline note.
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)
    layout = VaultLayout.from_vault(temp_vault)
    with sqlite3.connect(layout.knowledge_db) as conn:
        conn.execute(
            """
            INSERT INTO objects (object_id, object_kind, title, canonical_path, source_slug)
            VALUES ('agent_note', 'evergreen', 'Agent Note', '10-Knowledge/Evergreen/Agent Note.md', 'agent_note')
            """
        )
        conn.execute(
            """
            INSERT INTO compiled_summaries (object_id, summary_text, source_slug)
            VALUES ('agent_note', 'Agent note documents an unrelated system.', 'agent_note')
            """
        )
        conn.execute(
            """
            INSERT INTO contradictions (
              contradiction_id, subject_key, positive_claim_ids_json, negative_claim_ids_json, status, resolution_note, resolved_at
            ) VALUES (
              'contradiction::underscore',
              'agent platform',
              '[\"agentXnote::positive\"]',
              '[\"agentXnote::negative\"]',
              'open',
              '',
              ''
            )
            """
        )
        conn.commit()

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--view",
            "object/page",
            "--object-id",
            "agent_note",
        ]
    )

    content = (layout.compiled_views_dir / "default-knowledge" / "objects" / "agent_note.md").read_text(encoding="utf-8")

    assert result == 0
    assert "## Contradictions" in content
    assert "- (none)" in content


def test_build_views_command_can_materialize_topic_view_from_truth_store(temp_vault):
    from openclaw_pipeline.commands.build_views import main
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

    source = temp_vault / "10-Knowledge" / "Evergreen" / "Source.md"
    target = temp_vault / "10-Knowledge" / "Evergreen" / "Target.md"

    source.write_text(
        """---
note_id: source-note
title: Source Note
type: evergreen
date: 2026-04-10
---

# Source Note

Source note explains the runtime architecture.

Links to [[target-note]].
""",
        encoding="utf-8",
    )
    target.write_text(
        """---
note_id: target-note
title: Target Note
type: evergreen
date: 2026-04-10
---

# Target Note

Target note captures downstream effects.
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--view",
            "overview/topic",
        ]
    )

    layout = VaultLayout.from_vault(temp_vault)
    content = (layout.compiled_views_dir / "default-knowledge" / "overview__topic.md").read_text(encoding="utf-8")

    assert result == 0
    assert "# overview/topic" in content
    assert "## Object Summaries" in content
    assert "Source Note" in content
    assert "Target Note" in content
    assert "Source note explains the runtime architecture." in content
    assert "[[target-note]]" in content


def test_build_views_command_can_materialize_event_dossier_from_truth_store(temp_vault):
    from openclaw_pipeline.commands.build_views import main
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

    source = temp_vault / "10-Knowledge" / "Evergreen" / "Launch.md"
    source.write_text(
        """---
note_id: launch-note
title: Launch Note
type: evergreen
date: 2026-04-10
---

# Launch Note

Launch note explains the system launch details.

## 2026-04-09
The system launched publicly for operators.
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--view",
            "event/dossier",
        ]
    )

    layout = VaultLayout.from_vault(temp_vault)
    content = (layout.compiled_views_dir / "default-knowledge" / "event__dossier.md").read_text(encoding="utf-8")

    assert result == 0
    assert "# event/dossier" in content
    assert "## Timeline" in content
    assert "2026-04-09" in content
    assert "Launch Note" in content
    assert "Launch note explains the system launch details." in content


def test_build_views_command_can_materialize_cluster_overview(temp_vault):
    from openclaw_pipeline.commands.build_views import main
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

    source = temp_vault / "10-Knowledge" / "Evergreen" / "Source.md"
    target = temp_vault / "10-Knowledge" / "Evergreen" / "Target.md"

    source.write_text(
        """---
note_id: source-note
title: Source Note
type: evergreen
date: 2026-04-10
---

# Source Note

Source note explains the runtime architecture.

Links to [[target-note]].
""",
        encoding="utf-8",
    )
    target.write_text(
        """---
note_id: target-note
title: Target Note
type: evergreen
date: 2026-04-10
---

# Target Note

Target note captures downstream effects.
""",
        encoding="utf-8",
    )
    source = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Source Deep Dive_深度解读.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        """---
note_id: source-deep-dive
title: Source Deep Dive
type: deep_dive
date: 2026-04-10
---

# Source Deep Dive

Mentions [[source-note]] and [[target-note]].
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Atlas-Index.md"
    atlas.write_text(
        """---
note_id: atlas-index
title: Atlas Index
type: moc
date: 2026-04-10
---

# Atlas Index

- [[source-note]]
- [[target-note]]
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--view",
            "overview/clusters",
        ]
    )

    layout = VaultLayout.from_vault(temp_vault)
    content = (layout.compiled_views_dir / "default-knowledge" / "overview__clusters.md").read_text(encoding="utf-8")

    assert result == 0
    assert "# overview/clusters" in content
    assert "## Graph Clusters" in content
    assert "Source Note" in content
    assert "Target Note" in content
    assert "#### Cluster Synthesis" in content
    assert "#### Structural Label" in content
    assert "#### Relation Patterns" in content
    assert "#### Related Clusters" in content
    assert "#### Next Reading Route" in content
    assert "- priority_band:" in content
    assert "- neighborhood_score:" in content
    assert "#### Coverage" in content
    assert "Source Deep Dive" in content
    assert "Atlas Index" in content


def test_build_views_command_can_materialize_cluster_crystal(temp_vault):
    from openclaw_pipeline.commands.build_views import main
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout
    from openclaw_pipeline.truth_api import list_graph_clusters

    source = temp_vault / "10-Knowledge" / "Evergreen" / "Source.md"
    target = temp_vault / "10-Knowledge" / "Evergreen" / "Target.md"
    source.write_text(
        """---
note_id: source-note
title: Source Note
type: evergreen
date: 2026-04-10
---

# Source Note

Source note explains the runtime architecture.

Links to [[target-note]].
""",
        encoding="utf-8",
    )
    target.write_text(
        """---
note_id: target-note
title: Target Note
type: evergreen
date: 2026-04-10
---

# Target Note

Target note captures downstream effects.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)
    cluster = list_graph_clusters(temp_vault, pack_name="default-knowledge")[0]

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--view",
            "cluster/crystal",
            "--cluster-id",
            cluster["cluster_id"],
        ]
    )

    layout = VaultLayout.from_vault(temp_vault)
    content = (
        layout.compiled_views_dir / "default-knowledge" / "clusters" / f"{cluster['cluster_id']}.md"
    ).read_text(encoding="utf-8")

    assert result == 0
    assert f"# cluster/{cluster['cluster_id']}" in content
    assert "## Cluster Synthesis" in content
    assert "## Structural Label" in content
    assert "## Edge Summary" in content
    assert "## Relation Patterns" in content
    assert "## Review Pressure" in content
    assert "## Next Reading Route" in content
    assert "## Members" in content
    assert "## Internal Edges" in content
    assert "- display_title: " in content
    assert "cluster around" in content
    assert "Source Note" in content
    assert "Target Note" in content


def test_build_views_command_cluster_crystal_includes_related_clusters(temp_vault):
    from openclaw_pipeline.commands.build_views import main
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout
    from openclaw_pipeline.truth_api import list_graph_clusters

    alpha = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    beta = temp_vault / "10-Knowledge" / "Evergreen" / "Beta.md"
    gamma = temp_vault / "10-Knowledge" / "Evergreen" / "Gamma.md"
    delta = temp_vault / "10-Knowledge" / "Evergreen" / "Delta.md"
    alpha.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-10
---

# Alpha

Alpha links to [[beta]].
""",
        encoding="utf-8",
    )
    beta.write_text(
        """---
note_id: beta
title: Beta
type: evergreen
date: 2026-04-10
---

# Beta

Alpha does not support local-first execution.
""",
        encoding="utf-8",
    )
    gamma.write_text(
        """---
note_id: gamma
title: Gamma
type: evergreen
date: 2026-04-10
---

# Gamma

Gamma links to [[delta]].
""",
        encoding="utf-8",
    )
    delta.write_text(
        """---
note_id: delta
title: Delta
type: evergreen
date: 2026-04-10
---

# Delta
""",
        encoding="utf-8",
    )
    shared_source = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Shared Deep Dive_深度解读.md"
    shared_source.parent.mkdir(parents=True, exist_ok=True)
    shared_source.write_text(
        """---
note_id: shared-deep-dive
title: Shared Deep Dive
type: deep_dive
date: 2026-04-10
---

# Shared Deep Dive

Mentions [[alpha]], [[beta]], [[gamma]], and [[delta]].
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Shared-Atlas.md"
    atlas.write_text(
        """---
note_id: shared-atlas
title: Shared Atlas
type: moc
date: 2026-04-10
---

# Shared Atlas

- [[alpha]]
- [[beta]]
- [[gamma]]
- [[delta]]
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)
    cluster = next(
        item
        for item in list_graph_clusters(temp_vault, pack_name="default-knowledge")
        if "alpha" in {member["object_id"] for member in item["members"]}
    )

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--view",
            "cluster/crystal",
            "--cluster-id",
            cluster["cluster_id"],
        ]
    )

    layout = VaultLayout.from_vault(temp_vault)
    content = (
        layout.compiled_views_dir / "default-knowledge" / "clusters" / f"{cluster['cluster_id']}.md"
    ).read_text(encoding="utf-8")

    assert result == 0
    assert "## Related Clusters" in content
    assert "## Neighborhood Groups" in content
    assert "source_and_atlas_overlap" in content
    assert "Shared Atlas" in content or "Shared Deep Dive" in content


def test_build_views_command_can_materialize_contradictions_overview(temp_vault):
    from openclaw_pipeline.commands.build_views import main
    from openclaw_pipeline.knowledge_index import list_contradictions, rebuild_knowledge_index, resolve_contradictions
    from openclaw_pipeline.runtime import VaultLayout
    from openclaw_pipeline.truth_api import record_review_action

    one = temp_vault / "10-Knowledge" / "Evergreen" / "One.md"
    two = temp_vault / "10-Knowledge" / "Evergreen" / "Two.md"
    one.write_text(
        """---
note_id: harness-positive
title: Harness Positive
type: evergreen
date: 2026-04-10
---

# Harness Positive

Agent harness supports local-first execution for operators.
""",
        encoding="utf-8",
    )
    two.write_text(
        """---
note_id: harness-negative
title: Harness Negative
type: evergreen
date: 2026-04-10
---

# Harness Negative

Agent harness does not support local-first execution for operators.
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)
    contradiction_id = list_contradictions(temp_vault, limit=5)[0]["contradiction_id"]
    resolve_contradictions(
        temp_vault,
        [contradiction_id],
        status="dismissed",
        note="False conflict after claim review.",
    )
    record_review_action(
        temp_vault,
        event_type="ui_contradictions_resolved",
        slug="harness-positive",
        payload={
            "object_ids": ["harness-positive", "harness-negative"],
            "contradiction_ids": [contradiction_id],
            "status": "dismissed",
            "note": "False conflict after claim review.",
            "rebuilt_object_ids": [],
        },
    )

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--pack",
            "default-knowledge",
            "--view",
            "truth/contradictions",
        ]
    )

    layout = VaultLayout.from_vault(temp_vault)
    content = (layout.compiled_views_dir / "default-knowledge" / "truth__contradictions.md").read_text(encoding="utf-8")

    assert result == 0
    assert "# truth/contradictions" in content
    assert "## Contradiction Records" in content
    assert "agent harness" in content
    assert "dismissed" in content
    assert "False conflict after claim review." in content
