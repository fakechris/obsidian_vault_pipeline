from __future__ import annotations

import argparse
import json
from pathlib import Path

from ovp_pipeline.graph_cli import cmd_build


def _write_note(directory: Path, slug: str, title: str, links: list[str] = ()) -> None:
    body = "\n".join(f"[[{target}]]" for target in links)
    (directory / f"{slug}.md").write_text(
        "---\n"
        f'title: "{title}"\n'
        "type: evergreen\n"
        "date: 2026-04-07\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{body}\n",
        encoding="utf-8",
    )


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    evergreen = vault / "10-Knowledge" / "Evergreen"
    evergreen.mkdir(parents=True)

    # Two seeds (titles match "agent memory")
    _write_note(evergreen, "Agent-Memory-Core", "Agent Memory Core",
                links=["Episodic Buffer"])
    _write_note(evergreen, "Agent-Memory-Eviction", "Agent Memory Eviction",
                links=["Cache Policy"])

    # 1-hop neighbours (no "agent memory" in title — only reachable by edge)
    _write_note(evergreen, "Episodic-Buffer", "Episodic Buffer",
                links=["LLM Context Window"])
    _write_note(evergreen, "Cache-Policy", "Cache Policy")

    # 2-hop (only included with --expand-hops 2+)
    _write_note(evergreen, "LLM-Context-Window", "LLM Context Window")

    # Unrelated (must never appear)
    _write_note(evergreen, "Investing-Notes", "Investing Notes")
    return vault


def _build_args(vault: Path, output: Path, *, seed_match=None, expand_hops=1,
                open=False, no_index=True):
    # 默认 no_index=True：现有用例走扫盘路径，避免依赖 ovp-knowledge-index 的 fixture
    return argparse.Namespace(
        vault_dir=vault,
        output=str(output),
        seed_match=seed_match,
        expand_hops=expand_hops,
        open=open,
        no_index=no_index,
    )


def test_build_seed_match_extracts_subgraph_with_one_hop(tmp_path):
    vault = _make_vault(tmp_path)
    output = tmp_path / "subgraph.json"

    rc = cmd_build(_build_args(vault, output, seed_match="agent memory", expand_hops=1))
    assert rc == 0

    data = json.loads(output.read_text(encoding="utf-8"))
    titles = {n["title"] for n in data["nodes"]}

    # Both seeds + both 1-hop neighbours
    assert "Agent Memory Core" in titles
    assert "Agent Memory Eviction" in titles
    assert "Episodic Buffer" in titles
    assert "Cache Policy" in titles

    # 2-hop and unrelated must be excluded at hops=1
    assert "LLM Context Window" not in titles
    assert "Investing Notes" not in titles

    # Subgraph metadata is preserved
    assert data["seed_pattern"] == "agent memory"
    assert data["expand_hops"] == 1
    assert data["stats"]["expanded_node_count"] == len(data["nodes"])

    # Seed roles are annotated
    by_title = {n["title"]: n for n in data["nodes"]}
    assert by_title["Agent Memory Core"]["seed_role"] == "seed"
    assert by_title["Agent Memory Core"]["distance_from_seed"] == 0
    assert by_title["Episodic Buffer"]["seed_role"] == "neighbor_1hop"
    assert by_title["Episodic Buffer"]["distance_from_seed"] == 1


def test_build_seed_match_expand_hops_two_includes_second_neighbour(tmp_path):
    vault = _make_vault(tmp_path)
    output = tmp_path / "subgraph.json"

    rc = cmd_build(_build_args(vault, output, seed_match="agent memory", expand_hops=2))
    assert rc == 0

    data = json.loads(output.read_text(encoding="utf-8"))
    titles = {n["title"] for n in data["nodes"]}

    assert "LLM Context Window" in titles
    assert "Investing Notes" not in titles

    by_title = {n["title"]: n for n in data["nodes"]}
    assert by_title["LLM Context Window"]["seed_role"] == "neighbor_2hop"
    assert by_title["LLM Context Window"]["distance_from_seed"] == 2


def test_build_seed_match_no_match_returns_error(tmp_path):
    vault = _make_vault(tmp_path)
    output = tmp_path / "subgraph.json"

    rc = cmd_build(_build_args(vault, output, seed_match="nonexistent-topic"))
    assert rc == 1
    assert not output.exists()


def test_build_html_escapes_script_tags_in_node_titles(tmp_path):
    """节点 title 字面包含 '</script>' 时必须转义，否则 <script> 块被提前关闭、
    后续 JSON 当 HTML 渲染（用户实测：legend 后大段 JSON 文字泄露）。"""
    from ovp_pipeline.graph.visualize import GraphVisualizer

    payload = {
        "day_id": "test",
        "generated_at": "2026-04-23",
        "nodes": [
            {
                "note_id": "evil",
                "title": "Title with </script><img src=x> in it",
                "note_type": "evergreen",
                "seed_role": "seed",
            }
        ],
        "edges": [],
        "seed_note_ids": ["evil"],
    }
    out = tmp_path / "evil.html"
    GraphVisualizer(payload).html(out)
    html = out.read_text(encoding="utf-8")

    # vis.js bundle close + body script close = 2; any third means injection escaped
    assert html.count("</script>") == 2
    # And the title content survives (just escaped) so the node still renders
    assert "<\\/script>" in html


def test_build_seed_match_writes_interactive_html(tmp_path):
    vault = _make_vault(tmp_path)
    output = tmp_path / "subgraph.html"

    rc = cmd_build(_build_args(vault, output, seed_match="agent memory", expand_hops=1))
    assert rc == 0
    assert output.exists()

    html = output.read_text(encoding="utf-8")
    # vis.js bundle + every kept node label rendered into the page
    assert "vis-network" in html
    assert "Agent Memory Core" in html
    assert "Episodic Buffer" in html
    assert "Investing Notes" not in html


def test_build_uses_knowledge_db_when_available(tmp_path):
    """默认走 knowledge.db，跳过昂贵的 registry 解析；扫盘 fallback 仅在 db 缺失时触发。"""
    from ovp_pipeline.knowledge_index import rebuild_knowledge_index

    vault = _make_vault(tmp_path)
    rebuild_knowledge_index(vault)
    output = tmp_path / "from-db.json"

    rc = cmd_build(_build_args(vault, output, seed_match="agent memory",
                               expand_hops=1, no_index=False))
    assert rc == 0

    data = json.loads(output.read_text(encoding="utf-8"))
    titles = {n["title"] for n in data["nodes"]}

    # 同样的子图过滤语义在两条路径下都成立
    assert "Agent Memory Core" in titles
    assert "Agent Memory Eviction" in titles
    assert "Episodic Buffer" in titles
    assert "Cache Policy" in titles
    assert "LLM Context Window" not in titles
    assert "Investing Notes" not in titles
    assert data["seed_pattern"] == "agent memory"


def test_build_falls_back_to_filesystem_when_db_missing(tmp_path, capsys):
    vault = _make_vault(tmp_path)
    output = tmp_path / "fallback.json"

    # 没跑 rebuild_knowledge_index，knowledge.db 不存在
    rc = cmd_build(_build_args(vault, output, seed_match="agent memory",
                               expand_hops=1, no_index=False))
    assert rc == 0

    out = capsys.readouterr().out
    assert "knowledge.db" in out and "回退" in out

    data = json.loads(output.read_text(encoding="utf-8"))
    assert any(n["title"] == "Agent Memory Core" for n in data["nodes"])


def test_build_without_seed_match_writes_full_graph(tmp_path):
    vault = _make_vault(tmp_path)
    output = tmp_path / "full.json"

    rc = cmd_build(_build_args(vault, output))
    assert rc == 0

    data = json.loads(output.read_text(encoding="utf-8"))
    titles = {n["title"] for n in data["nodes"]}

    # Full-graph mode: every note is present, no seed_pattern key
    assert "Investing Notes" in titles
    assert "seed_pattern" not in data
