#!/usr/bin/env python3
"""
OVP Graph CLI - 知识图谱构建工具

Usage:
    ovp-graph --build                  # 全量构建
    ovp-graph --daily 2026-04-07       # 生成每日增量
    ovp-graph --daily today             # 生成今天增量
    ovp-graph --validate                # 验证frontmatter
    ovp-graph --upgrade                 # 升级现有文件frontmatter
    ovp-graph --export-graphml          # 导出GraphML格式
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from ovp_pipeline.runtime import iter_markdown_files, resolve_vault_dir

# 默认vault_dir: 假设从vault根目录运行
# 通过 --vault-dir 或环境变量覆盖
VAULT_DIR = resolve_vault_dir()


def _load_graph_from_index(vault_dir: Path) -> tuple[list[dict], list[dict], Path]:
    """读取已经在 ovp-knowledge-index 阶段持久化的图谱。

    pages_index → nodes、page_links → edges。Registry 解析在那边一次性跑完，
    这里只做 SELECT，比扫盘快几个数量级。
    """
    import sqlite3

    from ovp_pipeline.runtime import VaultLayout

    layout = VaultLayout(vault_dir)
    db_path = layout.knowledge_db
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_edges: set[str] = set()

    with sqlite3.connect(db_path) as conn:
        for slug, title, note_type, path, day_id in conn.execute(
            "SELECT slug, title, note_type, path, day_id FROM pages_index"
        ):
            nodes.append({
                "note_id": slug,
                "title": title or slug,
                "note_type": note_type or "unknown",
                "path": path or "",
                "day_id": day_id or "",
                "distance_from_seed": 0,
                "seed_role": "seed",
                "degree": 0,
                "in_degree": 0,
                "out_degree": 0,
                "seed_support": 0,
                "topic_clusters": [],
                "entities": [],
                "tags": [],
            })
        for source_slug, target_slug, target_raw, link_type, line_number in conn.execute(
            "SELECT source_slug, target_slug, target_raw, link_type, line_number"
            " FROM page_links"
        ):
            edge_id = f"{source_slug}-{target_slug}-{link_type or 'wikilink'}"
            if edge_id in seen_edges:
                continue
            seen_edges.add(edge_id)
            edges.append({
                "edge_id": edge_id,
                "source": source_slug,
                "target": target_slug,
                "edge_type": link_type or "wikilink",
                "weight": 1.0,
                "is_new_today": False,
                "anchor_text": target_raw or "",
                "evidence_line": line_number or 0,
            })

    return nodes, edges, db_path


def _scan_graph_from_filesystem(vault_dir: Path) -> tuple[list[dict], list[dict]]:
    """扫盘 fallback：当 knowledge.db 还没建立时使用。"""
    from ovp_pipeline.graph import GraphBuilder

    builder = GraphBuilder(vault_dir)
    directories = [
        vault_dir / "10-Knowledge" / "Evergreen",
        vault_dir / "20-Areas",
        vault_dir / "50-Inbox" / "01-Raw",
    ]
    all_nodes: list[dict] = []
    all_edges: list[dict] = []
    for directory in directories:
        if directory.exists():
            print(f"  处理: {directory.relative_to(vault_dir)}")
            nodes, edges = builder.build_from_directory(directory, recursive=True)
            all_nodes.extend(nodes)
            all_edges.extend(edges)
    return all_nodes, all_edges


def cmd_build(args):
    """全量构建图谱（默认从 knowledge.db 读取，可选 --seed-match 子图过滤）"""
    from ovp_pipeline.graph.daily_delta import DailyDelta

    vault_dir = resolve_vault_dir(args.vault_dir or VAULT_DIR)
    no_index = getattr(args, "no_index", False)

    all_nodes: list[dict] = []
    all_edges: list[dict] = []

    if not no_index:
        try:
            all_nodes, all_edges, db_path = _load_graph_from_index(vault_dir)
            try:
                rel = db_path.relative_to(vault_dir)
            except ValueError:
                rel = db_path
            print(f"📊 从 knowledge.db 加载图谱: {rel}")
        except FileNotFoundError:
            print(
                "⚠️ 未找到 knowledge.db，回退到扫盘模式（先跑 ovp-knowledge-index 会快几个数量级）"
            )
            no_index = True

    if no_index:
        print("📊 扫盘构建全量图谱...")
        all_nodes, all_edges = _scan_graph_from_filesystem(vault_dir)

    print(f"\n✅ 图谱构建完成:")
    print(f"   节点: {len(all_nodes)}")
    print(f"   边: {len(all_edges)}")

    seed_match = getattr(args, "seed_match", None)
    expand_hops = getattr(args, "expand_hops", 1)

    if seed_match:
        import re

        try:
            pattern = re.compile(seed_match, re.IGNORECASE)
        except re.error as exc:
            print(f"❌ 无效的 --seed-match 正则: {exc}")
            return 1

        seed_ids = {
            node["note_id"]
            for node in all_nodes
            if pattern.search(node.get("title", "") or "")
            or pattern.search(node.get("note_id", "") or "")
        }
        if not seed_ids:
            print(f"⚠️ --seed-match {seed_match!r} 没有匹配到任何节点")
            return 1

        edge_index = {edge["edge_id"]: edge for edge in all_edges}
        delta_helper = DailyDelta(vault_dir)
        expanded_ids, distance_map = delta_helper._expand_hops(
            seed_ids, edge_index, expand_hops
        )

        all_nodes = [n for n in all_nodes if n["note_id"] in expanded_ids]
        all_edges = [
            e for e in all_edges
            if e["source"] in expanded_ids and e["target"] in expanded_ids
        ]
        for node in all_nodes:
            distance = distance_map.get(node["note_id"], expand_hops + 1)
            node["distance_from_seed"] = distance
            if distance == 0:
                node["seed_role"] = "seed"
            else:
                node["seed_role"] = f"neighbor_{min(distance, 3)}hop"

        print(f"\n🎯 子图过滤 (--seed-match {seed_match!r}, --expand-hops {expand_hops}):")
        print(f"   Seeds: {len(seed_ids)}")
        print(f"   节点: {len(all_nodes)}")
        print(f"   边: {len(all_edges)}")

    # 构建 daily-delta 形态的 dict，供 JSON / HTML 共享
    delta_payload = {
        "schema_version": "1.0.0",
        "day_id": "full" if not seed_match else f"seed-{seed_match}",
        "generated_at": datetime.now().isoformat(),
        "nodes": all_nodes,
        "edges": all_edges,
        "stats": {
            "expanded_node_count": len(all_nodes),
            "expanded_edge_count": len(all_edges),
        },
        "seed_note_ids": list(seed_ids) if seed_match else [],
    }
    if seed_match:
        delta_payload["seed_pattern"] = seed_match
        delta_payload["expand_hops"] = expand_hops

    # 导出（全部基于 dict payload，不依赖 GraphBuilder 内部状态）
    if args.output:
        import json as _json

        from ovp_pipeline.graph.visualize import GraphVisualizer

        output_path = Path(args.output)
        suffix = output_path.suffix.lower()
        if suffix == ".json":
            output_path.write_text(
                _json.dumps(delta_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"✅ 已导出 JSON: {output_path}")
        elif suffix == ".graphml":
            GraphVisualizer(delta_payload).export_graphml(output_path)
        elif suffix in (".html", ".htm"):
            GraphVisualizer(delta_payload).html(output_path)
            if getattr(args, "open", False):
                import webbrowser
                webbrowser.open(f"file://{output_path.resolve()}")
        else:
            print(f"⚠️ 未知输出格式 {suffix!r}，支持 .json / .graphml / .html")
            return 1

    return 0


def cmd_daily(args):
    """生成每日增量图谱"""
    from ovp_pipeline.graph import DailyDelta, GraphBuilder

    vault_dir = resolve_vault_dir(args.vault_dir or VAULT_DIR)
    delta_computer = DailyDelta(vault_dir)

    day_id = args.day
    if day_id == "today":
        from datetime import datetime
        day_id = datetime.now().strftime('%Y-%m-%d')

    # --full 模式: 显示完整图谱
    if args.full:
        print(f"📊 构建完整图谱...")

        builder = GraphBuilder(vault_dir)
        directories = [
            vault_dir / "10-Knowledge" / "Evergreen",
            vault_dir / "20-Areas",
            vault_dir / "50-Inbox" / "01-Raw",
        ]

        all_nodes = []
        all_edges = []

        for directory in directories:
            if directory.exists():
                nodes, edges = builder.build_from_directory(directory, recursive=True)
                all_nodes.extend(nodes)
                all_edges.extend(edges)

        # 构建完整图谱的delta格式
        delta = {
            "schema_version": "1.0.0",
            "vault_id": args.vault_id or "ovp",
            "day_id": day_id,
            "generated_at": datetime.now().isoformat(),
            "window": {"expand_hops": 99, "full": True},
            "stats": {
                "expanded_node_count": len(all_nodes),
                "expanded_edge_count": len(all_edges),
            },
            "seed_note_ids": [],
            "nodes": all_nodes,
            "edges": all_edges,
        }

        output_file = vault_dir / "60-Logs" / "daily-deltas" / f"delta-{day_id}.json"
        delta_computer.save(delta)
    else:
        print(f"📅 生成每日增量图谱: {day_id}")

        delta = delta_computer.generate(
            day_id=day_id,
            vault_id=args.vault_id or "ovp",
            expand_hops=args.expand_hops
        )

        # 保存JSON
        output_file = delta_computer.save(delta)

    # 打印统计
    stats = delta.get('stats', {})
    print(f"\n📊 统计:")
    print(f"   节点: {stats.get('expanded_node_count', 0)}")
    print(f"   边: {stats.get('expanded_edge_count', 0)}")
    if not args.full:
        print(f"   Seed笔记: {len(delta.get('seed_note_ids', []))}")

    # 可视化
    if args.viz:
        from ovp_pipeline.graph.visualize import GraphVisualizer

        viz = GraphVisualizer(delta)

        if args.viz == 'ascii':
            print("\n" + viz.ascii())
        elif args.viz == 'html':
            html_path = vault_dir / "60-Logs" / "daily-deltas" / f"delta-{day_id}.html"
            viz.html(html_path)
            print(f"\n🌐 HTML已生成: {html_path}")
            if args.open:
                import webbrowser
                webbrowser.open(f"file://{html_path}")
        elif args.viz == 'graphml':
            graphml_path = vault_dir / "60-Logs" / "daily-deltas" / f"delta-{day_id}.graphml"
            viz.export_graphml(graphml_path)

    return 0


def cmd_validate(args):
    """验证frontmatter"""
    from ovp_pipeline.graph.validators import validate_frontmatter_file

    vault_dir = resolve_vault_dir(args.vault_dir or VAULT_DIR)

    print("🔍 验证frontmatter...")

    all_valid = True
    error_count = 0

    for md_file in iter_markdown_files(vault_dir, recursive=True):
        valid, errors = validate_frontmatter_file(md_file)
        if not valid:
            all_valid = False
            error_count += 1
            print(f"\n❌ {md_file.relative_to(vault_dir)}:")
            for error in errors:
                print(f"   - {error}")

    if all_valid:
        print("\n✅ 所有文件验证通过")
    else:
        print(f"\n⚠️ {error_count} 个文件有问题")

    return 0 if all_valid else 1


def cmd_upgrade(args):
    """升级现有文件frontmatter"""
    from ovp_pipeline.graph import FrontmatterParser

    vault_dir = resolve_vault_dir(args.vault_dir or VAULT_DIR)
    parser = FrontmatterParser(vault_dir)

    print("🔧 升级frontmatter...")

    upgraded_count = 0

    for md_file in iter_markdown_files(vault_dir, recursive=True):
        if parser.upgrade_frontmatter(md_file):
            upgraded_count += 1
            print(f"  ✅ 升级: {md_file.relative_to(vault_dir)}")

    print(f"\n✅ 完成: {upgraded_count} 个文件已升级")


def cmd_stats(args):
    """显示图谱统计"""
    from ovp_pipeline.graph import GraphBuilder

    vault_dir = resolve_vault_dir(args.vault_dir or VAULT_DIR)
    builder = GraphBuilder(vault_dir)

    print("📊 图谱统计...")

    directories = [
        vault_dir / "10-Knowledge" / "Evergreen",
        vault_dir / "20-Areas",
        vault_dir / "50-Inbox" / "01-Raw",
    ]

    all_nodes = []
    all_edges = []

    for directory in directories:
        if directory.exists():
            nodes, edges = builder.build_from_directory(directory, recursive=True)
            all_nodes.extend(nodes)
            all_edges.extend(edges)

    stats = builder.get_stats()

    print(f"\n📈 全局统计:")
    print(f"   总节点: {stats['total_nodes']}")
    print(f"   总边: {stats['total_edges']}")
    print(f"\n📚 按类型分布:")
    for note_type, count in stats.get('note_types', {}).items():
        print(f"   {note_type}: {count}")


def main():
    parser = argparse.ArgumentParser(
        description="OVP Graph - 知识图谱构建工具"
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        help="Vault目录 (默认: 当前目录)"
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # ovp-graph --build
    build_parser = subparsers.add_parser("build", help="全量构建图谱")
    build_parser.add_argument("--output", "-o", help="输出文件路径")
    build_parser.add_argument(
        "--seed-match",
        help="正则匹配 title/note_id，把命中节点作为种子并截取子图",
    )
    build_parser.add_argument(
        "--expand-hops",
        type=int,
        default=1,
        help="--seed-match 命中后向邻居扩展的跳数 (默认 1)",
    )
    build_parser.add_argument(
        "--open",
        action="store_true",
        help="导出 .html 后自动在浏览器打开",
    )
    build_parser.add_argument(
        "--no-index",
        action="store_true",
        help="跳过 knowledge.db，强制扫盘构建（默认走 db，秒级；扫盘可能十分钟）",
    )

    # ovp-graph --daily
    daily_parser = subparsers.add_parser("daily", help="生成每日增量图谱")
    daily_parser.add_argument("day", nargs="?", default="today", help="日期 YYYY-MM-DD 或 'today'")
    daily_parser.add_argument("--vault-id", default="ovp", help="Vault标识")
    daily_parser.add_argument("--expand-hops", type=int, default=1, help="扩展跳数 (0-3)")
    daily_parser.add_argument("--viz", choices=["ascii", "html", "graphml"], help="可视化类型")
    daily_parser.add_argument("--open", action="store_true", help="生成后自动在浏览器打开")
    daily_parser.add_argument("--full", action="store_true", help="显示完整图谱（忽略日期筛选）")

    # ovp-graph --validate
    subparsers.add_parser("validate", help="验证frontmatter")

    # ovp-graph --upgrade
    upgrade_parser = subparsers.add_parser("upgrade", help="升级现有文件frontmatter")
    upgrade_parser.add_argument("--dry-run", action="store_true", help="预览模式")

    # ovp-graph --stats
    subparsers.add_parser("stats", help="显示图谱统计")

    args = parser.parse_args()

    if not args.command:
        # 默认行为：构建
        cmd_build(args)
        return 0

    if args.command == "build":
        return cmd_build(args)
    elif args.command == "daily":
        return cmd_daily(args)
    elif args.command == "validate":
        return cmd_validate(args)
    elif args.command == "upgrade":
        return cmd_upgrade(args)
    elif args.command == "stats":
        return cmd_stats(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
