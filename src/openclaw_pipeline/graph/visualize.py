"""
Visualization - 图谱可视化

支持:
- ASCII art (终端)
- HTML (浏览器打开)
- GraphML (Gephi等工具)
"""

from pathlib import Path
from typing import Optional
from dataclasses import asdict
import json


class GraphVisualizer:
    """图谱可视化器"""

    def __init__(self, delta: dict):
        self.delta = delta
        self.nodes = {n['note_id']: n for n in delta.get('nodes', [])}
        self.edges = delta.get('edges', [])

    def ascii(self) -> str:
        """生成ASCII艺术图"""
        lines = []
        lines.append("=" * 60)
        lines.append(f"📊 Daily Delta Graph: {self.delta['day_id']}")
        lines.append("=" * 60)

        # 统计
        stats = self.delta.get('stats', {})
        lines.append(f"\n📈 统计:")
        lines.append(f"   Seeds: {len(self.delta.get('seed_note_ids', []))}")
        lines.append(f"   Nodes: {stats.get('expanded_node_count', 0)}")
        lines.append(f"   Edges: {stats.get('expanded_edge_count', 0)}")

        # 图例
        lines.append(f"\n📝 图例:")
        lines.append(f"   🌱 seed        - 今日新增/修改")
        lines.append(f"   🔗 1-hop      - 直接关联")
        lines.append(f"   🔄 2-hop      - 2跳关联")
        lines.append(f"   📦 3-hop      - 3跳关联")

        # 过滤模板文件
        def is_valid_node(node: dict) -> bool:
            title = node.get('title', '')
            note_id = node.get('note_id', '')
            path = node.get('path', '')
            # 跳过模板占位符
            if '{{' in title or '{{' in note_id:
                return False
            # 跳过模板文件
            if '_template' in path.lower() or note_id.startswith('_'):
                return False
            return True

        # 按类型分组
        by_type = {}
        valid_nodes = []
        for node in self.nodes.values():
            if is_valid_node(node):
                valid_nodes.append(node)
                t = node.get('note_type', 'unknown')
                by_type.setdefault(t, []).append(node)

        lines.append(f"\n📚 按类型:")
        type_icons = {
            'raw': '📄',
            'deep_dive': '📑',
            'evergreen': '🌲',
            'moc': '🗺️',
            'daily_view': '📅',
        }
        for note_type, nodes in by_type.items():
            icon = type_icons.get(note_type, '📝')
            lines.append(f"   {icon} {note_type}: {len(nodes)}")

        # 节点列表
        lines.append(f"\n🌐 节点 ({len(valid_nodes)}):")
        for node in sorted(valid_nodes, key=lambda n: n.get('seed_role', '')):
            seed_role = node.get('seed_role', 'unknown')
            role_icon = {
                'seed': '🌱',
                'neighbor_1hop': '🔗',
                'neighbor_2hop': '🔄',
                'neighbor_3hop': '📦',
            }.get(seed_role, '❓')

            title = node.get('title', '') or node['note_id'][:30]
            note_type = node.get('note_type', '?')[:8]

            lines.append(f"   {role_icon} [{note_type}] {title}")

        # 边
        if self.edges:
            lines.append(f"\n🔗 边 ({len(self.edges)}):")
            for edge in self.edges[:10]:  # 最多显示10条
                src = edge['source'][:15]
                tgt = edge['target'][:15]
                etype = edge.get('edge_type', '?')[:8]
                lines.append(f"   {src} ──{etype}──> {tgt}")
            if len(self.edges) > 10:
                lines.append(f"   ... 还有 {len(self.edges) - 10} 条边")

        lines.append("\n" + "=" * 60)
        return "\n".join(lines)

    def html(self, output_path: Optional[Path] = None) -> str:
        """生成交互式HTML可视化 (使用vis.js)"""
        html_template = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>OVP Graph - {day_id}</title>
    <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            padding: 20px;
            background: #1a1a2e;
            color: #eee;
        }}
        h1 {{ color: #00d4ff; margin-bottom: 10px; }}
        .stats {{
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }}
        .stat {{
            background: #16213e;
            padding: 10px 20px;
            border-radius: 8px;
            border: 1px solid #0f3460;
        }}
        .stat-value {{ font-size: 24px; font-weight: bold; color: #00d4ff; }}
        .stat-label {{ font-size: 12px; color: #888; }}
        #graph {{ width: 100%; height: 600px; border: 1px solid #0f3460; border-radius: 8px; }}
        .legend {{
            margin-top: 20px;
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 5px;
            font-size: 12px;
        }}
        .legend-color {{
            width: 12px;
            height: 12px;
            border-radius: 50%;
        }}
        .seed {{ background: #00d4ff; }}
        .1hop {{ background: #4ade80; }}
        .2hop {{ background: #fbbf24; }}
        .3hop {{ background: #f87171; }}
        .note-raw {{ background: #94a3b8; }}
        .note-deep_dive {{ background: #818cf8; }}
        .note-evergreen {{ background: #34d399; }}
        .note-moc {{ background: #f472b6; }}
    </style>
</head>
<body>
    <h1>📊 OVP Daily Delta Graph</h1>
    <p>日期: {day_id} | 生成时间: {generated_at}</p>

    <div class="stats">
        <div class="stat">
            <div class="stat-value">{node_count}</div>
            <div class="stat-label">节点</div>
        </div>
        <div class="stat">
            <div class="stat-value">{edge_count}</div>
            <div class="stat-label">边</div>
        </div>
        <div class="stat">
            <div class="stat-value">{seed_count}</div>
            <div class="stat-label">Seeds</div>
        </div>
    </div>

    <div id="graph"></div>

    <div class="legend">
        <div class="legend-item"><div class="legend-color seed"></div> Seed</div>
        <div class="legend-item"><div class="legend-color 1hop"></div> 1-hop</div>
        <div class="legend-item"><div class="legend-color 2hop"></div> 2-hop</div>
        <div class="legend-item"><div class="legend-color 3hop"></div> 3-hop</div>
        <div style="width:20px"></div>
        <div class="legend-item"><div class="legend-color note-raw"></div> Raw</div>
        <div class="legend-item"><div class="legend-color note-deep_dive"></div> Deep Dive</div>
        <div class="legend-item"><div class="legend-color note-evergreen"></div> Evergreen</div>
        <div class="legend-item"><div class="legend-color note-moc"></div> MOC</div>
    </div>

    <script>
    var nodes = new vis.DataSet({nodes_json});
    var edges = new vis.DataSet({edges_json});

    var container = document.getElementById('graph');
    var data = {{ nodes: nodes, edges: edges }};
    var options = {{
        nodes: {{
            shape: 'dot',
            size: 15,
            font: {{ color: '#eee', size: 12 }},
            borderWidth: 2,
            shadow: true
        }},
        edges: {{
            width: 1,
            color: {{ color: '#555', highlight: '#00d4ff' }},
            smooth: {{ type: 'continuous' }}
        }},
        physics: {{
            stabilization: {{ iterations: 100 }},
            barnesHut: {{
                gravitationalConstant: -2000,
                springLength: 150
            }}
        }},
        interaction: {{
            hover: true,
            tooltipDelay: 200
        }},
        layout: {{
            improvedLayout: true
        }}
    }};

    var network = new vis.Network(container, data, options);
    </script>
</body>
</html>"""

        # 构建节点数据
        nodes_data = []
        for node in self.delta.get('nodes', []):
            seed_role = node.get('seed_role', 'unknown')
            note_type = node.get('note_type', 'unknown')

            # 颜色映射
            role_colors = {
                'seed': '#00d4ff',
                'neighbor_1hop': '#4ade80',
                'neighbor_2hop': '#fbbf24',
                'neighbor_3hop': '#f87171',
            }
            type_colors = {
                'raw': '#94a3b8',
                'deep_dive': '#818cf8',
                'evergreen': '#34d399',
                'moc': '#f472b6',
                'daily_view': '#fb923c',
            }

            color = type_colors.get(note_type, '#888')

            title = node.get('title', '') or node['note_id']
            title_short = title[:40] + '...' if len(title) > 40 else title

            nodes_data.append({
                'id': node['note_id'],
                'label': title_short,
                'title': f"{title}\n类型: {note_type}\n角色: {seed_role}",
                'color': {
                    'background': color,
                    'border': role_colors.get(seed_role, '#888')
                },
                'size': 25 if seed_role == 'seed' else 15
            })

        # 构建边数据
        edges_data = []
        for edge in self.delta.get('edges', []):
            edges_data.append({
                'from': edge['source'],
                'to': edge['target'],
                'label': edge.get('edge_type', ''),
                'title': f"{edge.get('edge_type', '')}\n{edge['source']} → {edge['target']}"
            })

        # 渲染
        html = html_template.format(
            day_id=self.delta.get('day_id', ''),
            generated_at=self.delta.get('generated_at', ''),
            node_count=len(nodes_data),
            edge_count=len(edges_data),
            seed_count=len(self.delta.get('seed_note_ids', [])),
            nodes_json=json.dumps(nodes_data, ensure_ascii=False),
            edges_json=json.dumps(edges_data, ensure_ascii=False)
        )

        if output_path:
            output_path.write_text(html, encoding='utf-8')
            print(f"✅ HTML已生成: {output_path}")

        return html

    def export_graphml(self, output_path: Path):
        """导出为GraphML格式 (兼容Gephi, yEd)"""
        lines = []
        lines.append('<?xml version="1.0" encoding="UTF-8"?>')
        lines.append('<graphml xmlns="http://graphml.graphdrawing.org/xmlns">')

        # 定义节点属性
        lines.append('  <key id="title" for="node" attr.name="title" attr.type="string"/>')
        lines.append('  <key id="note_type" for="node" attr.name="note_type" attr.type="string"/>')
        lines.append('  <key id="seed_role" for="node" attr.name="seed_role" attr.type="string"/>')
        lines.append('  <key id="day_id" for="node" attr.name="day_id" attr.type="string"/>')
        lines.append('  <key id="path" for="node" attr.name="path" attr.type="string"/>')
        lines.append('  <key id="edge_type" for="edge" attr.name="edge_type" attr.type="string"/>')

        lines.append('  <graph id="G" edgedefault="directed">')

        # 节点
        for node in self.delta.get('nodes', []):
            nid = node['note_id']
            title = node.get('title', '') or ''
            note_type = node.get('note_type', '')
            seed_role = node.get('seed_role', '')
            day_id = node.get('day_id', '')
            path = node.get('path', '')

            lines.append(f'    <node id="{nid}">')
            lines.append(f'      <data key="title">{self._escape_xml(title)}</data>')
            lines.append(f'      <data key="note_type">{note_type}</data>')
            lines.append(f'      <data key="seed_role">{seed_role}</data>')
            lines.append(f'      <data key="day_id">{day_id}</data>')
            lines.append(f'      <data key="path">{self._escape_xml(path)}</data>')
            lines.append('    </node>')

        # 边
        for edge in self.delta.get('edges', []):
            eid = edge.get('edge_id', f"{edge['source']}-{edge['target']}")
            edge_type = edge.get('edge_type', '')
            lines.append(f'    <edge id="{eid}" source="{edge["source"]}" target="{edge["target"]}">')
            lines.append(f'      <data key="edge_type">{edge_type}</data>')
            lines.append('    </edge>')

        lines.append('  </graph>')
        lines.append('</graphml>')

        output_path.write_text('\n'.join(lines), encoding='utf-8')
        print(f"✅ GraphML已导出: {output_path}")

    @staticmethod
    def _escape_xml(s: str) -> str:
        """转义XML特殊字符"""
        return (s.replace('&', '&amp;')
                  .replace('<', '&lt;')
                  .replace('>', '&gt;')
                  .replace('"', '&quot;')
                  .replace("'", '&apos;'))
