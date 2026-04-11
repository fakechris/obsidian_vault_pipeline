from __future__ import annotations


def test_create_evergreen_writes_source_traceability_to_frontmatter(tmp_path):
    from openclaw_pipeline.query_to_wiki import create_evergreen

    vault = tmp_path / "vault"
    (vault / "10-Knowledge" / "Evergreen").mkdir(parents=True, exist_ok=True)

    path, slug = create_evergreen(
        vault,
        title="Architecture View",
        definition="A saved answer",
        content="Body",
        sources=["50-Inbox/01-Raw/example.md"],
    )

    content = path.read_text(encoding="utf-8")

    assert slug == "architecture-view"
    assert "sources:" in content
    assert "- \"50-Inbox/01-Raw/example.md\"" in content

