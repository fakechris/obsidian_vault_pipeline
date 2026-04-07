"""
Graph Builder - 构建知识图谱

从笔记元数据和链接构建 NetworkX 图
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict

try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False

from .frontmatter import NoteMetadata, FrontmatterParser
from .link_parser import LinkParser, Link
from ..concept_registry import ConceptRegistry, ResolutionAction, normalize_surface
from ..identity import canonicalize_note_id


@dataclass
class GraphNode:
    """图谱节点"""
    note_id: str
    title: str
    note_type: str
    path: str
    day_id: str
    distance_from_seed: int = 0
    seed_role: str = "seed"
    degree: int = 0
    in_degree: int = 0
    out_degree: int = 0
    seed_support: int = 0
    topic_clusters: list = None
    entities: list = None
    tags: list = None

    def __post_init__(self):
        self.topic_clusters = self.topic_clusters or []
        self.entities = self.entities or []
        self.tags = self.tags or []

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GraphEdge:
    """图谱边"""
    edge_id: str
    source: str
    target: str
    edge_type: str
    weight: float
    is_new_today: bool = False
    anchor_text: str = ""
    evidence_line: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class GraphBuilder:
    """知识图谱构建器"""

    def __init__(self, vault_dir: Path):
        self.vault_dir = vault_dir
        self.frontmatter_parser = FrontmatterParser(vault_dir)
        self.link_parser = LinkParser(vault_dir)
        self.registry = ConceptRegistry(vault_dir).load()

        # 数据存储
        self.nodes: dict[str, GraphNode] = {}
        self.edges: dict[str, GraphEdge] = {}
        self._edge_count = 0
        self._surface_to_note_id: dict[str, str] = {}

        # Graphviz/NetworkX 图
        self.graph = None
        if HAS_NETWORKX:
            self.graph = nx.DiGraph()

    def build_from_directory(self, directory: Path, recursive: bool = True) -> tuple[list, list]:
        """
        从目录构建图谱

        Returns:
            (nodes, edges) - 列表形式，便于序列化
        """
        # 1. 解析所有文件的frontmatter
        all_metadata = self.frontmatter_parser.parse_directory(directory, recursive)
        for meta in all_metadata:
            self._add_node(meta)

        # 2. 解析所有链接
        all_links = self.link_parser.parse_directory(directory, recursive)
        for link in all_links:
            self._add_edge(link)

        # 3. 计算图谱指标
        self._calculate_metrics()

        # 4. 返回列表格式
        return self._to_lists()

    def build_daily_delta(
        self,
        directory: Path,
        day_id: str,
        expand_hops: int = 1
    ) -> dict:
        """
        构建每日增量图谱

        只处理指定日期的seed notes及其N跳邻居
        """
        # 1. 找出指定日期的所有笔记作为seeds
        all_metadata = self.frontmatter_parser.parse_directory(directory, recursive=True)
        seed_ids = set()
        seed_notes = []

        for meta in all_metadata:
            if meta.day_id == day_id:
                seed_ids.add(meta.note_id)
                seed_notes.append(meta)

        if not seed_notes:
            print(f"⚠️ 没有找到 {day_id} 的笔记")
            return {"nodes": [], "edges": [], "stats": {}}

        # 2. 收集所有节点和边
        all_links = self.link_parser.parse_directory(directory, recursive=True)

        # 3. BFS扩展N跳邻居
        expanded_ids = self._expand_hops(seed_ids, all_links, expand_hops)

        # 4. 构建子图
        subgraph_nodes = {nid: self.nodes[nid] for nid in expanded_ids if nid in self.nodes}
        subgraph_edges = {
            eid: edge for eid, edge in self.edges.items()
            if edge.source in expanded_ids and edge.target in expanded_ids
        }

        # 5. 计算子图指标
        self._calculate_subgraph_metrics(subgraph_nodes, subgraph_edges)

        # 6. 转换为daily_delta格式
        nodes_list = [n.to_dict() for n in subgraph_nodes.values()]
        edges_list = [e.to_dict() for e in subgraph_edges.values()]

        stats = {
            "seed_raw_count": len([n for n in seed_notes if n.note_type == "raw"]),
            "seed_deep_dive_count": len([n for n in seed_notes if n.note_type == "deep_dive"]),
            "seed_evergreen_count": len([n for n in seed_notes if n.note_type == "evergreen"]),
            "seed_moc_count": len([n for n in seed_notes if n.note_type == "moc"]),
            "expanded_node_count": len(nodes_list),
            "expanded_edge_count": len(edges_list),
            "new_edge_count": len([e for e in edges_list if e.get('is_new_today')]),
            "new_cluster_count": 0,
        }

        return {
            "nodes": nodes_list,
            "edges": edges_list,
            "stats": stats,
            "seed_note_ids": list(seed_ids)
        }

    def _expand_hops(
        self,
        seed_ids: set[str],
        all_links: list[Link],
        max_hops: int
    ) -> set[str]:
        """BFS扩展N跳邻居"""
        expanded = set(seed_ids)
        current_hop = set(seed_ids)

        # 构建无向邻接表，daily delta 需要同时包含入边和出边邻居
        adjacency = {}
        for link in all_links:
            adjacency.setdefault(link.source, set()).add(link.target)
            adjacency.setdefault(link.target, set()).add(link.source)

        # BFS
        for _ in range(max_hops):
            next_hop = set()
            for node_id in current_hop:
                if node_id in adjacency:
                    next_hop.update(adjacency[node_id])

            next_hop -= expanded  # 移除已扩展的
            expanded.update(next_hop)
            current_hop = next_hop

            if not current_hop:
                break

        return expanded

    def _add_node(self, meta: NoteMetadata):
        """添加节点"""
        node = GraphNode(
            note_id=meta.note_id,
            title=meta.title,
            note_type=meta.note_type,
            path=meta.path,
            day_id=meta.day_id,
            topic_clusters=meta.topic_clusters,
            entities=meta.entities,
            tags=meta.tags
        )
        self.nodes[meta.note_id] = node
        self._register_surface(meta.note_id, meta.note_id)
        self._register_surface(meta.title, meta.note_id)
        if meta.path:
            self._register_surface(Path(meta.path).stem, meta.note_id)
            self._register_surface(str(Path(meta.path).with_suffix("")), meta.note_id)
        for alias in meta.aliases:
            self._register_surface(alias, meta.note_id)

        if self.graph:
            self.graph.add_node(meta.note_id, **node.to_dict())

    def _add_edge(self, link: Link):
        """添加边"""
        source_id = self._resolve_note_id(link.source)
        target_id = self._resolve_link_target(link)

        # 确保节点存在
        if source_id not in self.nodes:
            # 创建一个占位节点
            self.nodes[source_id] = GraphNode(
                note_id=source_id,
                title=source_id,
                note_type="unknown",
                path="",
                day_id=""
            )

        if target_id not in self.nodes:
            self.nodes[target_id] = GraphNode(
                note_id=target_id,
                title=link.target_raw or target_id,
                note_type="unknown",
                path="",
                day_id=""
            )

        # 生成边ID
        edge_id = f"{source_id}-{target_id}-{link.link_type}"
        if edge_id in self.edges:
            return  # 避免重复边

        self._edge_count += 1
        edge = GraphEdge(
            edge_id=edge_id,
            source=source_id,
            target=target_id,
            edge_type=link.link_type,
            weight=1.0,
            anchor_text=link.anchor,
            evidence_line=link.line_number
        )
        self.edges[edge_id] = edge

        if self.graph:
            self.graph.add_edge(source_id, target_id, **edge.to_dict())

    def _register_surface(self, surface: str, note_id: str) -> None:
        """Register a local graph surface for later link resolution."""
        if not surface:
            return
        normalized = normalize_surface(surface)
        if normalized and normalized not in self._surface_to_note_id:
            self._surface_to_note_id[normalized] = note_id

    def _resolve_note_id(self, value: str) -> str:
        """Resolve a note identifier through canonical slug normalization."""
        resolved = canonicalize_note_id(value)
        return resolved or value

    def _resolve_link_target(self, link: Link) -> str:
        """Resolve a link target using local graph nodes first, then the registry."""
        for candidate in filter(None, [link.target, link.target_raw, link.anchor]):
            normalized_id = canonicalize_note_id(candidate)
            if normalized_id in self.nodes:
                return normalized_id

            normalized_surface = normalize_surface(candidate)
            if normalized_surface in self._surface_to_note_id:
                return self._surface_to_note_id[normalized_surface]

            if candidate == link.anchor:
                continue

        for candidate in filter(None, [link.target_raw, link.anchor, link.target]):
            resolution = self.registry.resolve_mention(candidate)
            if resolution.action == ResolutionAction.LINK_EXISTING and resolution.entry:
                return resolution.entry.slug
            if resolution.action == ResolutionAction.PASSTHROUGH_PATH:
                resolved = canonicalize_note_id(candidate)
                if resolved:
                    return resolved

        resolved = canonicalize_note_id(link.target_raw or link.target or link.anchor)
        return resolved or link.target

    def _calculate_metrics(self):
        """计算图谱全局指标"""
        if not self.graph:
            return

        # 计算度
        for node_id in self.nodes:
            if node_id in self.graph:
                in_deg = self.graph.in_degree(node_id)
                out_deg = self.graph.out_degree(node_id)
                degree = in_deg + out_deg

                self.nodes[node_id].in_degree = in_deg
                self.nodes[node_id].out_degree = out_deg
                self.nodes[node_id].degree = degree

    def _calculate_subgraph_metrics(self, nodes: dict, edges: dict):
        """计算子图指标"""
        # 计算每个节点的seed_support
        seed_count = {}
        for edge in edges.values():
            if edge.is_new_today:
                target = edge.target
                seed_count[target] = seed_count.get(target, 0) + 1

        for node in nodes.values():
            node.seed_support = seed_count.get(node.note_id, 0)

    def _to_lists(self) -> tuple[list, list]:
        """转换为列表格式"""
        nodes_list = [n.to_dict() for n in self.nodes.values()]
        edges_list = [e.to_dict() for e in self.edges.values()]
        return nodes_list, edges_list

    def export_graphml(self, output_path: Path):
        """导出为GraphML格式"""
        if not HAS_NETWORKX:
            print("⚠️ networkx 未安装，无法导出 GraphML")
            return

        if self.graph:
            nx.write_graphml(self.graph, str(output_path))
            print(f"✅ 已导出 GraphML: {output_path}")

    def export_json(self, output_path: Path, daily_delta: Optional[dict] = None):
        """导出为JSON格式"""
        if daily_delta:
            data = daily_delta
        else:
            nodes, edges = self._to_lists()
            data = {
                "nodes": nodes,
                "edges": edges,
                "generated_at": datetime.now().isoformat()
            }

        output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"✅ 已导出 JSON: {output_path}")

    def get_stats(self) -> dict:
        """获取图谱统计信息"""
        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "note_types": self._count_by_type(),
            "generated_at": datetime.now().isoformat()
        }

    def _count_by_type(self) -> dict:
        """按类型统计节点"""
        counts = {}
        for node in self.nodes.values():
            t = node.note_type
            counts[t] = counts.get(t, 0) + 1
        return counts
