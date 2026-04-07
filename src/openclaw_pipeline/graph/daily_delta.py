"""
Daily Delta - 每日增量图谱计算

根据 frontmatter.schema.json 的 daily-delta.schema.json 生成每日增量图谱
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from .frontmatter import FrontmatterParser
from .link_parser import LinkParser
from .graph_builder import GraphBuilder


class DailyDelta:
    """每日增量图谱计算器"""

    def __init__(self, vault_dir: Path):
        self.vault_dir = vault_dir
        self.graph_builder = GraphBuilder(vault_dir)

    def generate(
        self,
        day_id: str,
        vault_id: str = "default",
        expand_hops: int = 1,
        timezone: str = "Asia/Tokyo"
    ) -> dict:
        """
        生成指定日期的增量图谱

        Args:
            day_id: 日期 YYYY-MM-DD
            vault_id: Vault标识
            expand_hops: 扩展跳数 (0-3)
            timezone: 时区

        Returns:
            符合 daily-delta.schema.json 的字典
        """
        # 扫描目录
        directories = [
            self.vault_dir / "10-Knowledge" / "Evergreen",
            self.vault_dir / "20-Areas",
            self.vault_dir / "50-Inbox" / "01-Raw",
        ]

        all_nodes = {}
        all_edges = {}

        for directory in directories:
            if not directory.exists():
                continue

            result = self.graph_builder.build_from_directory(directory, recursive=True)
            nodes, edges = result if isinstance(result, tuple) else ([], [])

            for node in nodes:
                all_nodes[node['note_id']] = node
            for edge in edges:
                all_edges[edge['edge_id']] = edge

        # 找出指定日期的seed notes
        seed_ids = set()
        seed_notes = []

        for note_id, node in all_nodes.items():
            if node.get('day_id') == day_id:
                seed_ids.add(note_id)
                seed_notes.append(node)

        # BFS扩展邻居
        expanded_ids, distance_map = self._expand_hops(seed_ids, all_edges, expand_hops)

        # 构建子图
        subgraph_nodes = {
            nid: node for nid, node in all_nodes.items()
            if nid in expanded_ids
        }
        subgraph_edges = {
            eid: edge for eid, edge in all_edges.items()
            if edge['source'] in expanded_ids and edge['target'] in expanded_ids
        }

        # 计算seed_role
        for nid, node in subgraph_nodes.items():
            distance = distance_map.get(nid, expand_hops + 1)
            if distance == 0:
                node['seed_role'] = 'seed'
                node['distance_from_seed'] = 0
            elif distance == 1:
                node['seed_role'] = 'neighbor_1hop'
                node['distance_from_seed'] = 1
            elif distance == 2:
                node['seed_role'] = 'neighbor_2hop'
                node['distance_from_seed'] = 2
            else:
                node['seed_role'] = 'neighbor_3hop'
                node['distance_from_seed'] = distance

        # 标记新边
        for eid, edge in subgraph_edges.items():
            edge['is_new_today'] = edge['source'] in seed_ids or edge['target'] in seed_ids

        # 构建输出
        now = datetime.now().isoformat()
        start_of_day = f"{day_id}T00:00:00"
        end_of_day = f"{day_id}T23:59:59"

        # 统计
        stats = {
            "seed_raw_count": len([n for n in seed_notes if n.get('note_type') == 'raw']),
            "seed_deep_dive_count": len([n for n in seed_notes if n.get('note_type') == 'deep_dive']),
            "seed_evergreen_count": len([n for n in seed_notes if n.get('note_type') == 'evergreen']),
            "seed_moc_count": len([n for n in seed_notes if n.get('note_type') == 'moc']),
            "expanded_node_count": len(subgraph_nodes),
            "expanded_edge_count": len(subgraph_edges),
            "new_edge_count": len([e for e in subgraph_edges.values() if e.get('is_new_today')]),
            "new_cluster_count": 0,
        }

        delta = {
            "schema_version": "1.0.0",
            "vault_id": vault_id,
            "day_id": day_id,
            "timezone": timezone,
            "generated_at": now,
            "window": {
                "start_at": start_of_day,
                "end_at": end_of_day,
                "expand_hops": expand_hops,
                "seed_rule": "day_id"
            },
            "stats": stats,
            "seed_note_ids": list(seed_ids),
            "nodes": list(subgraph_nodes.values()),
            "edges": list(subgraph_edges.values()),
            "components": [],  # 可后续计算
            "exports": {}
        }

        return delta

    def _expand_hops(
        self,
        seed_ids: set[str],
        all_edges: dict,
        max_hops: int
    ) -> tuple[set[str], dict[str, int]]:
        """BFS扩展N跳邻居，按无向邻接扩展并返回距离映射。"""
        expanded = set(seed_ids)
        current_hop = set(seed_ids)
        distance_map = {seed_id: 0 for seed_id in seed_ids}

        # 构建无向邻接表，daily delta 应同时观察入边和出边邻居
        adjacency = {}
        for edge in all_edges.values():
            src = edge['source']
            tgt = edge['target']
            adjacency.setdefault(src, set()).add(tgt)
            adjacency.setdefault(tgt, set()).add(src)

        for hop in range(1, max_hops + 1):
            next_hop = set()
            for node_id in current_hop:
                if node_id in adjacency:
                    next_hop.update(adjacency[node_id])

            next_hop -= expanded
            for node_id in next_hop:
                distance_map[node_id] = hop
            expanded.update(next_hop)
            current_hop = next_hop

            if not current_hop:
                break

        return expanded, distance_map

    def _is_1hop(self, node_id: str, seed_ids: set[str], all_edges: dict) -> bool:
        """判断是否是1跳邻居"""
        for edge in all_edges.values():
            if edge['source'] in seed_ids and edge['target'] == node_id:
                return True
            if edge['target'] in seed_ids and edge['source'] == node_id:
                return True
        return False

    def _is_2hop(self, node_id: str, seed_ids: set[str], all_edges: dict) -> bool:
        """判断是否是2跳邻居"""
        # 找1跳邻居
        one_hop = set()
        for edge in all_edges.values():
            if edge['source'] in seed_ids:
                one_hop.add(edge['target'])
            if edge['target'] in seed_ids:
                one_hop.add(edge['source'])

        # 检查是否在2跳内
        for edge in all_edges.values():
            if edge['source'] in one_hop and edge['target'] == node_id:
                return True
            if edge['target'] in one_hop and edge['source'] == node_id:
                return True
        return False

    def save(self, delta: dict, output_dir: Optional[Path] = None) -> Path:
        """保存增量图谱到文件"""
        if output_dir is None:
            output_dir = self.vault_dir / "60-Logs" / "daily-deltas"

        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"delta-{delta['day_id']}.json"

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(delta, f, ensure_ascii=False, indent=2)

        print(f"✅ 已保存每日增量: {output_file}")
        return output_file

    def load(self, day_id: str, delta_dir: Optional[Path] = None) -> Optional[dict]:
        """加载指定日期的增量图谱"""
        if delta_dir is None:
            delta_dir = self.vault_dir / "60-Logs" / "daily-deltas"

        delta_file = delta_dir / f"delta-{day_id}.json"

        if not delta_file.exists():
            return None

        with open(delta_file, 'r', encoding='utf-8') as f:
            return json.load(f)

    def generate_today(self, **kwargs) -> dict:
        """生成今天的增量图谱"""
        today = datetime.now().strftime('%Y-%m-%d')
        return self.generate(today, **kwargs)
