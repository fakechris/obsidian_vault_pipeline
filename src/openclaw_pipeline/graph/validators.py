"""
Validators - Schema验证器

验证frontmatter和daily-delta是否符合schema规范
"""

import json
from pathlib import Path
from typing import Optional
from dataclasses import asdict


class FrontmatterValidator:
    """Frontmatter Schema验证器"""

    REQUIRED_FIELDS = [
        "note_id",
        "title",
        "note_type",
        "created_at",
        "updated_at",
        "day_id"
    ]

    VALID_NOTE_TYPES = ["raw", "deep_dive", "evergreen", "moc", "daily_view"]
    VALID_STATUSES = ["draft", "reviewed", "stable", "archived"]

    def validate(self, meta) -> tuple[bool, list[str]]:
        """
        验证元数据对象

        Returns:
            (is_valid, error_messages)
        """
        errors = []

        # 检查必需字段
        for field in self.REQUIRED_FIELDS:
            value = getattr(meta, field, None)
            if not value:
                errors.append(f"缺少必需字段: {field}")

        # 验证note_type
        if meta.note_type and meta.note_type not in self.VALID_NOTE_TYPES:
            errors.append(f"无效的note_type: {meta.note_type}")

        # 验证status
        if meta.status and meta.status not in self.VALID_STATUSES:
            errors.append(f"无效的status: {meta.status}")

        # 验证日期格式
        if meta.day_id and not self._is_valid_day_id(meta.day_id):
            errors.append(f"无效的day_id格式: {meta.day_id}")

        # 验证质量分数范围
        if meta.quality_score and (meta.quality_score < 0 or meta.quality_score > 5):
            errors.append(f"quality_score超出范围(0-5): {meta.quality_score}")

        # 验证置信度范围
        if meta.confidence_score and (meta.confidence_score < 0 or meta.confidence_score > 1):
            errors.append(f"confidence_score超出范围(0-1): {meta.confidence_score}")

        return len(errors) == 0, errors

    def _is_valid_day_id(self, day_id: str) -> bool:
        """验证日期格式 YYYY-MM-DD"""
        import re
        return bool(re.match(r'^\d{4}-\d{2}-\d{2}$', day_id))


class DailyDeltaValidator:
    """Daily Delta Schema验证器"""

    REQUIRED_NODE_FIELDS = [
        "note_id", "title", "note_type", "path",
        "distance_from_seed", "seed_role", "day_id"
    ]

    REQUIRED_EDGE_FIELDS = [
        "edge_id", "source", "target", "edge_type", "weight", "is_new_today"
    ]

    VALID_EDGE_TYPES = [
        "wikilink", "derived_from", "mentions_entity",
        "shares_cluster", "moc_membership", "reference"
    ]

    VALID_SEED_ROLES = [
        "seed", "neighbor_1hop", "neighbor_2hop", "neighbor_3hop"
    ]

    def validate(self, delta: dict) -> tuple[bool, list[str]]:
        """
        验证daily_delta字典

        Returns:
            (is_valid, error_messages)
        """
        errors = []

        # 检查顶层字段
        required_top = [
            "schema_version", "vault_id", "day_id", "timezone",
            "generated_at", "window", "stats", "nodes", "edges"
        ]

        for field in required_top:
            if field not in delta:
                errors.append(f"缺少顶层字段: {field}")

        # 验证nodes
        if "nodes" in delta:
            for i, node in enumerate(delta["nodes"]):
                node_errors = self._validate_node(node, i)
                errors.extend(node_errors)

        # 验证edges
        if "edges" in delta:
            for i, edge in enumerate(delta["edges"]):
                edge_errors = self._validate_edge(edge, i)
                errors.extend(edge_errors)

        # 验证stats
        if "stats" in delta:
            stat_errors = self._validate_stats(delta["stats"])
            errors.extend(stat_errors)

        return len(errors) == 0, errors

    def _validate_node(self, node: dict, index: int) -> list[str]:
        """验证单个节点"""
        errors = []
        prefix = f"nodes[{index}]"

        for field in self.REQUIRED_NODE_FIELDS:
            if field not in node:
                errors.append(f"{prefix}: 缺少字段 {field}")

        # 验证seed_role
        if "seed_role" in node and node["seed_role"] not in self.VALID_SEED_ROLES:
            errors.append(f"{prefix}: 无效的seed_role {node['seed_role']}")

        # 验证distance_from_seed
        if "distance_from_seed" in node:
            dist = node["distance_from_seed"]
            if not (0 <= dist <= 3):
                errors.append(f"{prefix}: distance_from_seed超出范围(0-3)")

        return errors

    def _validate_edge(self, edge: dict, index: int) -> list[str]:
        """验证单条边"""
        errors = []
        prefix = f"edges[{index}]"

        for field in self.REQUIRED_EDGE_FIELDS:
            if field not in edge:
                errors.append(f"{prefix}: 缺少字段 {field}")

        # 验证edge_type
        if "edge_type" in edge and edge["edge_type"] not in self.VALID_EDGE_TYPES:
            errors.append(f"{prefix}: 无效的edge_type {edge['edge_type']}")

        # 验证weight
        if "weight" in edge:
            weight = edge["weight"]
            if not (0 <= weight <= 1):
                errors.append(f"{prefix}: weight超出范围(0-1)")

        return errors

    def _validate_stats(self, stats: dict) -> list[str]:
        """验证统计信息"""
        errors = []
        required_stats = [
            "seed_raw_count", "seed_deep_dive_count", "seed_evergreen_count",
            "seed_moc_count", "expanded_node_count", "expanded_edge_count"
        ]

        for field in required_stats:
            if field not in stats:
                errors.append(f"stats: 缺少字段 {field}")

        return errors


def validate_frontmatter_file(file_path: Path) -> tuple[bool, list[str]]:
    """验证单个frontmatter文件"""
    from .frontmatter import NoteMetadata

    try:
        meta = NoteMetadata.from_file(file_path)
        validator = FrontmatterValidator()
        return validator.validate(meta)
    except Exception as e:
        return False, [f"解析失败: {e}"]


def validate_daily_delta_file(delta_path: Path) -> tuple[bool, list[str]]:
    """验证daily delta文件"""
    try:
        with open(delta_path, 'r', encoding='utf-8') as f:
            delta = json.load(f)

        validator = DailyDeltaValidator()
        return validator.validate(delta)
    except Exception as e:
        return False, [f"解析失败: {e}"]
