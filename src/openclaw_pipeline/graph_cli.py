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
from pathlib import Path

# 默认vault_dir: 假设从vault根目录运行
# 通过 --vault-dir 或环境变量覆盖
VAULT_DIR = Path.cwd()


def cmd_build(args):
    """全量构建图谱"""
    from openclaw_pipeline.graph import GraphBuilder

    vault_dir = Path(args.vault_dir) if args.vault_dir else VAULT_DIR
    builder = GraphBuilder(vault_dir)

    print("📊 开始构建全量图谱...")

    directories = [
        vault_dir / "10-Knowledge" / "Evergreen",
        vault_dir / "20-Areas",
        vault_dir / "50-Inbox" / "01-Raw",
    ]

    all_nodes = []
    all_edges = []

    for directory in directories:
        if directory.exists():
            print(f"  处理: {directory.relative_to(vault_dir)}")
            nodes, edges = builder.build_from_directory(directory, recursive=True)
            all_nodes.extend(nodes)
            all_edges.extend(edges)

    print(f"\n✅ 图谱构建完成:")
    print(f"   节点: {len(all_nodes)}")
    print(f"   边: {len(all_edges)}")

    # 导出
    if args.output:
        output_path = Path(args.output)
        if args.output.endswith('.json'):
            builder.export_json(output_path)
        elif args.output.endswith('.graphml'):
            builder.export_graphml(output_path)


def cmd_daily(args):
    """生成每日增量图谱"""
    from openclaw_pipeline.graph import DailyDelta

    vault_dir = Path(args.vault_dir) if args.vault_dir else VAULT_DIR
    delta_computer = DailyDelta(vault_dir)

    day_id = args.day
    if day_id == "today":
        from datetime import datetime
        day_id = datetime.now().strftime('%Y-%m-%d')

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
    print(f"   Seed笔记: {len(delta.get('seed_note_ids', []))}")
    print(f"   扩展节点: {stats.get('expanded_node_count', 0)}")
    print(f"   扩展边: {stats.get('expanded_edge_count', 0)}")

    # 可视化
    if args.viz:
        from openclaw_pipeline.graph.visualize import GraphVisualizer

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
    from openclaw_pipeline.graph.validators import validate_frontmatter_file

    vault_dir = Path(args.vault_dir) if args.vault_dir else VAULT_DIR

    print("🔍 验证frontmatter...")

    all_valid = True
    error_count = 0

    for pattern in ["**/*.md"]:
        for md_file in vault_dir.glob(pattern):
            if any(part.startswith('.') for part in md_file.parts):
                continue

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
    from openclaw_pipeline.graph import FrontmatterParser

    vault_dir = Path(args.vault_dir) if args.vault_dir else VAULT_DIR
    parser = FrontmatterParser(vault_dir)

    print("🔧 升级frontmatter...")

    upgraded_count = 0

    for pattern in ["**/*.md"]:
        for md_file in vault_dir.glob(pattern):
            if any(part.startswith('.') for part in md_file.parts):
                continue

            if parser.upgrade_frontmatter(md_file):
                upgraded_count += 1
                print(f"  ✅ 升级: {md_file.relative_to(vault_dir)}")

    print(f"\n✅ 完成: {upgraded_count} 个文件已升级")


def cmd_stats(args):
    """显示图谱统计"""
    from openclaw_pipeline.graph import GraphBuilder

    vault_dir = Path(args.vault_dir) if args.vault_dir else VAULT_DIR
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

    # ovp-graph --daily
    daily_parser = subparsers.add_parser("daily", help="生成每日增量图谱")
    daily_parser.add_argument("day", nargs="?", default="today", help="日期 YYYY-MM-DD 或 'today'")
    daily_parser.add_argument("--vault-id", default="ovp", help="Vault标识")
    daily_parser.add_argument("--expand-hops", type=int, default=1, help="扩展跳数 (0-3)")
    daily_parser.add_argument("--viz", choices=["ascii", "html", "graphml"], help="可视化类型")
    daily_parser.add_argument("--open", action="store_true", help="生成后自动在浏览器打开")

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
