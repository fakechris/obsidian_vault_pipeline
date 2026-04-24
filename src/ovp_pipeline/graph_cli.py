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

    *额外补充*：absorb 管道把 evergreen 从源文档抽出来后**不会**回写
    `[[concept]]` 到源文档 body 里，所以 page_links 里压根没有
    "source_md → evergreen" 的边。但每次 promote 都发了一条
    `evergreen_auto_promoted` 审计事件，里面带 (concept, source) 对。
    我们在这里把这条隐式 provenance 翻译成显式 graph edge
    (`edge_type='promoted_from'`)，不然图谱里几乎所有源文档都会"漂"在
    evergreen 网外面。
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

        # Provenance edges: source MD → evergreen，从 audit_events 重建
        # （audit_events.payload_json.source 是 basename，所以用 LIKE '%/'||source 匹配 path）
        promo_count = 0
        for src_slug, concept_slug in conn.execute(
            """
            SELECT DISTINCT p_src.slug, p_concept.slug
            FROM audit_events a
            JOIN pages_index p_src
              ON p_src.path LIKE '%/' || json_extract(a.payload_json, '$.source')
            JOIN pages_index p_concept
              ON p_concept.slug = json_extract(a.payload_json, '$.concept')
            WHERE a.event_type='evergreen_auto_promoted'
            """
        ):
            if not src_slug or not concept_slug or src_slug == concept_slug:
                continue
            edge_id = f"promoted-{src_slug}-{concept_slug}"
            if edge_id in seen_edges:
                continue
            seen_edges.add(edge_id)
            edges.append({
                "edge_id": edge_id,
                "source": src_slug,
                "target": concept_slug,
                "edge_type": "promoted_from",
                "weight": 1.0,
                "is_new_today": False,
                "anchor_text": "",
                "evidence_line": 0,
            })
            promo_count += 1
        if promo_count:
            print(f"📜 Provenance: 从 audit_events 补 {promo_count} 条 source→evergreen 边")

    return nodes, edges, db_path


# 概念网类型：evergreen 是原子概念，moc 是索引页，两者一起构成"概念波"。
# --layered 模式下，hop 2 跳到非概念网的节点 (即 source markdown / 原文档)。
#
# 之所以用"反白名单"而不是正向枚举 source 类型：vault 里 source markdown 的
# note_type 字段实际有 50+ 种变体（deep_dive / technical-analysis / engineering /
# ai-skill / 论文深度解读 / Threat Intelligence Report ...），每加一个 pack 都要
# 维护这个列表会持续踩坑。任何"非 evergreen 非 moc"都视为源文档更稳。
CONCEPT_NETWORK_TYPES = frozenset({"evergreen", "moc"})


def _is_source_markdown(note_type: str) -> bool:
    return bool(note_type) and note_type not in CONCEPT_NETWORK_TYPES


def _expand_layered(
    seed_ids: set[str],
    edge_index: dict,
    type_by_id: dict[str, str],
) -> tuple[set[str], dict[str, int], list[dict]]:
    """两层 BFS（用于 --layered）：

        hop 1: 从 seed 出发，只跳到 evergreen 邻居（"概念波"）
        hop 2: 从概念波（evergreen seed + hop1 evergreen）出发，
               只跳到 source markdown（deep_dive / raw / article ...）

    返回 (expanded_ids, distance_map, used_edges)。used_edges 只包含
    BFS 实际走过的边，避免把剔除掉的 evergreen↔evergreen 桥又画回到子图上。
    """
    adjacency: dict[str, list[dict]] = {}
    for edge in edge_index.values():
        adjacency.setdefault(edge["source"], []).append(edge)
        adjacency.setdefault(edge["target"], []).append(edge)

    expanded = set(seed_ids)
    distance_map: dict[str, int] = {sid: 0 for sid in seed_ids}
    used_edges: list[dict] = []
    used_edge_ids: set[str] = set()

    def _record(edge: dict) -> None:
        eid = edge["edge_id"]
        if eid not in used_edge_ids:
            used_edges.append(edge)
            used_edge_ids.add(eid)

    # Hop 1: seeds → evergreen 邻居
    hop1: set[str] = set()
    for sid in seed_ids:
        for edge in adjacency.get(sid, ()):
            other = edge["target"] if edge["source"] == sid else edge["source"]
            if other in expanded:
                continue
            if type_by_id.get(other) == "evergreen":
                hop1.add(other)
                _record(edge)
    expanded |= hop1
    for nid in hop1:
        distance_map[nid] = 1

    # Hop 2: 概念层（evergreen seed + hop1 evergreen）→ source markdown
    concept_layer = {sid for sid in seed_ids if type_by_id.get(sid) == "evergreen"}
    concept_layer |= hop1
    hop2: set[str] = set()
    for cid in concept_layer:
        for edge in adjacency.get(cid, ()):
            other = edge["target"] if edge["source"] == cid else edge["source"]
            if other in expanded:
                continue
            if _is_source_markdown(type_by_id.get(other, "")):
                hop2.add(other)
                _record(edge)
    expanded |= hop2
    for nid in hop2:
        distance_map[nid] = 2

    return expanded, distance_map, used_edges


def _prune_hop1(
    seed_ids: set[str],
    expanded_ids: set[str],
    distance_map: dict[str, int],
    used_edges: list[dict],
    *,
    min_seed_degree: int = 1,
    top_k_per_seed: int | None = None,
) -> tuple[set[str], dict[str, int], list[dict], dict[str, int]]:
    """Trim hop1 nodes by their seed-connection count.

    Two filters compose. ``min_seed_degree=N`` drops hop1 nodes that bridge
    fewer than N distinct seeds (suppresses concept-drift hop1 nodes that
    each touch only one seed). ``top_k_per_seed=K`` then ranks each seed's
    surviving hop1 neighbors by their seed-degree (descending) and keeps
    only the K highest — a horizontal cap on per-seed fan-out.

    Hop2 nodes that lose all their hop1/seed bridges as a result are also
    dropped (otherwise the visualization shows orphaned source-markdown
    nodes floating in space).

    Returns ``(new_expanded_ids, new_distance_map, new_used_edges, drop_summary)``.
    """
    from collections import defaultdict

    if min_seed_degree <= 1 and not top_k_per_seed:
        return expanded_ids, distance_map, used_edges, {"hop1_dropped": 0, "hop2_dropped": 0}

    hop1_ids = {nid for nid, d in distance_map.items() if d == 1}
    hop2_ids = {nid for nid, d in distance_map.items() if d == 2}

    # For each hop1 node, collect the set of seed_ids it directly bridges.
    hop1_seed_links: dict[str, set[str]] = defaultdict(set)
    for edge in used_edges:
        for endpoint, other in (
            (edge["source"], edge["target"]),
            (edge["target"], edge["source"]),
        ):
            if endpoint in hop1_ids and other in seed_ids:
                hop1_seed_links[endpoint].add(other)

    kept_hop1 = {hid for hid, seeds in hop1_seed_links.items() if len(seeds) >= min_seed_degree}

    if top_k_per_seed:
        seed_to_hop1: dict[str, list[str]] = defaultdict(list)
        for hid in kept_hop1:
            for sid in hop1_seed_links[hid]:
                seed_to_hop1[sid].append(hid)
        kept_via_topk: set[str] = set()
        for sid, hids in seed_to_hop1.items():
            ranked = sorted(hids, key=lambda h: (-len(hop1_seed_links[h]), h))
            kept_via_topk.update(ranked[:top_k_per_seed])
        kept_hop1 = kept_hop1 & kept_via_topk

    dropped_hop1 = hop1_ids - kept_hop1

    # Re-assess hop2: keep only those still bridged by a kept hop1 or a seed.
    kept_hop2: set[str] = set()
    for edge in used_edges:
        for endpoint, other in (
            (edge["source"], edge["target"]),
            (edge["target"], edge["source"]),
        ):
            if endpoint in hop2_ids and (other in seed_ids or other in kept_hop1):
                kept_hop2.add(endpoint)
    dropped_hop2 = hop2_ids - kept_hop2

    new_expanded = expanded_ids - dropped_hop1 - dropped_hop2
    new_distance_map = {nid: d for nid, d in distance_map.items() if nid in new_expanded}
    new_edges = [
        e for e in used_edges if e["source"] in new_expanded and e["target"] in new_expanded
    ]
    return new_expanded, new_distance_map, new_edges, {
        "hop1_dropped": len(dropped_hop1),
        "hop2_dropped": len(dropped_hop2),
    }


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

    print("\n✅ 图谱构建完成:")
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

        layered = getattr(args, "layered", False)
        source_walk = getattr(args, "source_walk", False)
        if layered and source_walk:
            print("❌ --layered 和 --source-walk 互斥，请只选一个")
            return 1

        type_by_id = {n["note_id"]: n.get("note_type", "") for n in all_nodes}

        if layered:
            if expand_hops != 1:
                print(
                    f"ℹ️  --layered 固定为两层（hop1=evergreen, hop2=source-md），"
                    f"忽略 --expand-hops {expand_hops}"
                )
            edge_index = {edge["edge_id"]: edge for edge in all_edges}
            expanded_ids, distance_map, used_edges = _expand_layered(
                seed_ids, edge_index, type_by_id
            )

            min_seed_degree = max(1, getattr(args, "min_seed_degree", 1) or 1)
            top_k_per_seed = getattr(args, "top_k_per_seed", None)
            if min_seed_degree > 1 or top_k_per_seed:
                expanded_ids, distance_map, used_edges, drop_summary = _prune_hop1(
                    seed_ids,
                    expanded_ids,
                    distance_map,
                    used_edges,
                    min_seed_degree=min_seed_degree,
                    top_k_per_seed=top_k_per_seed,
                )
                print(
                    f"✂️  hop1 prune: dropped {drop_summary['hop1_dropped']} hop1 / "
                    f"{drop_summary['hop2_dropped']} hop2 nodes "
                    f"(min_seed_degree={min_seed_degree}, top_k_per_seed={top_k_per_seed})"
                )

            all_nodes = [n for n in all_nodes if n["note_id"] in expanded_ids]
            # 只画 BFS 实际走过的边，否则 hop1 的 evergreen 之间又会拉一堆桥
            all_edges = used_edges
            mode_label = "--layered (hop1=evergreen, hop2=source-md)"
            if min_seed_degree > 1 or top_k_per_seed:
                mode_label += (
                    f" + prune(min_seed_degree={min_seed_degree}, top_k_per_seed={top_k_per_seed})"
                )
        else:
            # --source-walk: 剔除 evergreen↔evergreen / evergreen↔moc 这种"概念内部"
            # 的边，让 BFS 从概念种子直接跳到产生它的源文档（deep_dive / raw / article ...）
            # 而不是在 evergreen 网里空转。
            traversal_edges = all_edges
            if source_walk:
                bridge_types = {"evergreen", "moc"}
                traversal_edges = [
                    e
                    for e in all_edges
                    if not (
                        type_by_id.get(e["source"]) in bridge_types
                        and type_by_id.get(e["target"]) in bridge_types
                    )
                ]
                print(
                    f"🚦 source-walk: 剔除 evergreen/moc 之间的桥接边"
                    f" ({len(all_edges) - len(traversal_edges)}/{len(all_edges)})"
                )

            edge_index = {edge["edge_id"]: edge for edge in traversal_edges}
            delta_helper = DailyDelta(vault_dir)
            expanded_ids, distance_map = delta_helper._expand_hops(
                seed_ids, edge_index, expand_hops
            )

            all_nodes = [n for n in all_nodes if n["note_id"] in expanded_ids]
            # 渲染用的边集和 BFS 用的边集保持一致——source-walk 模式下不要把刚剔掉的
            # evergreen↔evergreen 桥又画回到图上，否则视觉上还是一团 evergreen mesh
            all_edges = [
                e for e in traversal_edges
                if e["source"] in expanded_ids and e["target"] in expanded_ids
            ]
            mode_label = f"--expand-hops {expand_hops}" + (
                " --source-walk" if source_walk else ""
            )

        for node in all_nodes:
            distance = distance_map.get(node["note_id"], 99)
            node["distance_from_seed"] = distance
            if distance == 0:
                node["seed_role"] = "seed"
            else:
                node["seed_role"] = f"neighbor_{min(distance, 3)}hop"

        print(f"\n🎯 子图过滤 (--seed-match {seed_match!r}, {mode_label}):")
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
        print("📊 构建完整图谱...")

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

        delta_computer.save(delta)
    else:
        print(f"📅 生成每日增量图谱: {day_id}")

        delta = delta_computer.generate(
            day_id=day_id,
            vault_id=args.vault_id or "ovp",
            expand_hops=args.expand_hops
        )

        delta_computer.save(delta)

    # 打印统计
    stats = delta.get('stats', {})
    print("\n📊 统计:")
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

    print("\n📈 全局统计:")
    print(f"   总节点: {stats['total_nodes']}")
    print(f"   总边: {stats['total_edges']}")
    print("\n📚 按类型分布:")
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
    build_parser.add_argument(
        "--source-walk",
        action="store_true",
        help="BFS 时剔除 evergreen↔evergreen / evergreen↔moc 边，让 evergreen 种子"
             "直接跳到产生它的源文档（deep_dive / raw / article），而不是在概念网里空转",
    )
    build_parser.add_argument(
        "--layered",
        action="store_true",
        help="两层 BFS：hop1 只扩 evergreen 邻居（概念波），"
             "hop2 只从这些 evergreen 跳到 source markdown。--expand-hops 被忽略。"
             "和 --source-walk 互斥",
    )
    build_parser.add_argument(
        "--min-seed-degree",
        type=int,
        default=1,
        help="(仅 --layered) 丢弃只连接到 <N 个 seed 的 hop1 节点 — 抑制 concept drift。"
             "默认 1 (关闭)，常用 2",
    )
    build_parser.add_argument(
        "--top-k-per-seed",
        type=int,
        default=None,
        help="(仅 --layered) 每个 seed 只保留 K 个 seed-degree 最高的 hop1 邻居。"
             "用于横向裁剪极宽的 fan-out",
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
