#!/usr/bin/env python3
"""
Registry Cleanup Script

执行 registry 清洗流程：
1. P0: sentence-like titles 退出 resolver
2. P1: 处理 separate (删误导性 alias)
3. P2: 处理 merge (合并重复概念)

Usage:
    python registry-cleanup.py --dry-run
    python registry-cleanup.py --execute
    python registry-cleanup.py --status  # 只显示状态
"""

#!/usr/bin/env python3
"""
Registry Cleanup Script

执行 registry 清洗流程：
1. P0: sentence-like titles 退出 resolver
2. P1: 处理 separate (删误导性 alias)
3. P2: 处理 merge (合并重复概念)

Usage:
    python -m openclaw_pipeline.registry_cleanup <vault_dir> --dry-run
    python -m openclaw_pipeline.registry_cleanup <vault_dir> --execute
    python -m openclaw_pipeline.registry_cleanup <vault_dir> --status  # 只显示状态
"""

import argparse
from pathlib import Path

from openclaw_pipeline.concept_registry import (
    ConceptRegistry, is_sentence_like_title,
    KIND_PROPOSITION, STATUS_DEPRECATED
)


def print_status(registry: ConceptRegistry):
    """显示当前 registry 状态"""
    print(f"\n{'='*60}")
    print("Registry Status")
    print(f"{'='*60}")

    total = len(registry.entries)
    eligible = sum(1 for e in registry._entries if e.is_resolver_eligible())
    disabled = total - eligible
    sentence_like = sum(1 for e in registry._entries if is_sentence_like_title(e.title))

    print(f"Total entries: {total}")
    print(f"Resolver eligible: {eligible}")
    print(f"Resolver disabled: {disabled}")
    print(f"Sentence-like titles: {sentence_like}")
    print(f"Surface records: {len(registry._surface_records)}")

    # 运行 conflict 检测
    results = registry.fix_surface_conflicts(dry_run=True)

    print(f"\nConflicts: {results['total_conflicts']}")
    print(f"  Merge: {len(results['merge_candidates'])}")
    print(f"  Review (similarity): {len([c for c in results['review_needed'] if c.get('action') == 'review'])}")
    print(f"  Separate: {len(results['separate_recommendations'])}")


def apply_p0(registry: ConceptRegistry, dry_run: bool = True) -> list[str]:
    """
    P0: sentence-like titles 退出 resolver
    """
    changed = []
    for entry in registry._entries:
        if is_sentence_like_title(entry.title) and entry.resolver_enabled:
            if not dry_run:
                entry.resolver_enabled = False
                if entry.kind == "concept":
                    entry.kind = KIND_PROPOSITION
            changed.append(entry.slug)

    return changed


def apply_p1_separate(registry: ConceptRegistry, dry_run: bool = True) -> list[dict]:
    """
    P1: 处理 separate 冲突

    策略：
    - 如果是 entity vs concept，保持 entity，删概念的 alias
    - 如果是同级别概念，检查是否有更合适的 owner
    """
    changes = []

    # 定义已知的 separate 冲突处理规则
    separate_rules = {
        # Polymarket vs Prediction-Market: 保持 entity，删 category 的 alias
        "polymarket": {
            "keep": "Polymarket",
            "remove_alias_from": "Prediction-Market",
            "surface": "polymarket",
        },
        "预测市场": {
            "keep": "Prediction-Market",
            "remove_alias_from": "Polymarket",
            "surface": "预测市场",
        },
        # Deep-Agents vs Long-Running-Agent-Harness: 拆分
        "long running agent": {
            "keep": "Deep-Agents",
            "remove_alias_from": "Long-Running-Agent-Harness",
            "surface": "long running agent",
        },
        # Multi-Agent Patterns: 保留 Multi-Agent-Patterns，删 Agent-Architecture-Patterns 的 alias
        "multi agent patterns": {
            "keep": "Multi-Agent-Patterns",
            "remove_alias_from": "Agent-Architecture-Patterns",
            "surface": "multi agent patterns",
        },
        # Harness-Engineering vs Why-How-Loop-Framework: 保持独立，删交叉 alias
        "harness engineering": {
            "keep": "Harness-Engineering",
            "remove_alias_from": "Why-How-Loop-Framework",
            "surface": "harness engineering",
        },
    }

    for surface, rule in separate_rules.items():
        keep_slug = rule["keep"]
        remove_from_slug = rule["remove_alias_from"]

        keep_entry = registry.find_by_slug(keep_slug)
        remove_entry = registry.find_by_slug(remove_from_slug)

        if not keep_entry or not remove_entry:
            continue

        # 从 remove_entry 删掉冲突的 alias
        if surface in remove_entry.aliases:
            if not dry_run:
                remove_entry.aliases.remove(surface)
            changes.append({
                "surface": surface,
                "action": "remove_alias",
                "from": remove_from_slug,
                "to": keep_slug,
            })

    return changes


def apply_p2_merge(registry: ConceptRegistry, dry_run: bool = True) -> list[dict]:
    """
    P2: 处理 merge 冲突

    策略：
    - MCP-Protocol 和 MCP 合并，保留 MCP（更简洁）
    - 重复条目合并到 canonical slug
    """
    changes = []

    # MCP: 合并 MCP-Protocol 到 MCP
    mcp_rules = [
        {"source": "MCP-Protocol", "target": "MCP", "surface": "mcp protocol"},
        {"source": "MCP-Protocol", "target": "MCP", "surface": "mcp"},
        {"source": "MCP-Protocol", "target": "MCP", "surface": "model context protocol"},
    ]

    for rule in mcp_rules:
        source_entry = registry.find_by_slug(rule["source"])
        target_entry = registry.find_by_slug(rule["target"])
        if not source_entry or not target_entry:
            continue

        if source_entry.status == STATUS_DEPRECATED:
            continue

        if not dry_run:
            source_entry.status = STATUS_DEPRECATED
            source_entry.resolver_enabled = False
            source_entry.replaced_by = rule["target"]
            if rule["surface"] not in target_entry.aliases:
                target_entry.aliases.append(rule["surface"])

        changes.append({
            "surface": rule["surface"],
            "action": "merge",
            "from": rule["source"],
            "to": rule["target"],
        })

    # 重复标题合并
    duplicate_rules = [
        # Knowledge Overhang
        {
            "source": "knowledge-overhang",
            "target": "knowledge-overhang-in-llm-behavior",
            "surface": "knowledge overhang the gap between knowing and doing"
        },
        # LLM VRAM Formula
        {
            "source": "llm-vram-estimation-formula",
            "target": "llm-vram-formula-for-inference",
            "surface": "llm inference vram equals parameters times effective bits divided by 8"
        },
        # Prompt Caching Viability
        {
            "source": "prompt-caching-viability-constraint",
            "target": "prompt-caching-as-viability-constraint",
            "surface": "prompt caching 是 ai agent harness 工程的 viability constraint"
        },
    ]

    for rule in duplicate_rules:
        source_entry = registry.find_by_slug(rule["source"])
        target_entry = registry.find_by_slug(rule["target"])
        if not source_entry or not target_entry:
            continue

        if source_entry.status == STATUS_DEPRECATED:
            continue

        if not dry_run:
            source_entry.status = STATUS_DEPRECATED
            source_entry.resolver_enabled = False
            source_entry.replaced_by = rule["target"]

        changes.append({
            "surface": rule["surface"],
            "action": "merge",
            "from": rule["source"],
            "to": rule["target"],
        })

    # Agentic Loop: 保留 agentic-loop（通用概念），禁用 Claude-Code-Agentic-Loop
    agentic_entry = registry.find_by_slug("agentic-loop")
    claude_entry = registry.find_by_slug("Claude-Code-Agentic-Loop")

    if agentic_entry and claude_entry:
        if not dry_run:
            claude_entry.resolver_enabled = False
            # 保留 alias 让它可以解析到 agentic-loop
            if "agentic-loop" not in agentic_entry.aliases:
                agentic_entry.aliases.append("agentic-loop")

        changes.append({
            "surface": "agentic loop",
            "action": "disable",
            "from": "Claude-Code-Agentic-Loop",
            "to": "agentic-loop",
        })

    return changes


def main():
    parser = argparse.ArgumentParser(description="Registry Cleanup")
    parser.add_argument("vault_dir", type=Path, help="Path to vault directory")
    parser.add_argument("--status", action="store_true", help="Show status only")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run mode")
    parser.add_argument("--execute", action="store_true", help="Execute changes")
    parser.add_argument("--p0", action="store_true", help="Apply P0 (sentence-like)")
    parser.add_argument("--p1", action="store_true", help="Apply P1 (separate)")
    parser.add_argument("--p2", action="store_true", help="Apply P2 (merge)")
    parser.add_argument("--all", action="store_true", help="Apply all phases")

    args = parser.parse_args()

    vault_dir = args.vault_dir
    registry = ConceptRegistry(vault_dir).load()

    if args.status:
        print_status(registry)
        return

    dry_run = not args.execute

    if dry_run and not any([args.p0, args.p1, args.p2, args.all]):
        print("No action specified. Use --dry-run --p0/--p1/--p2/--all or --execute --p0/--p1/--p2/--all")
        print("\nUse --status to see current state.")
        return

    mode = "DRY-RUN" if dry_run else "EXECUTE"
    print(f"\n{'='*60}")
    print(f"Registry Cleanup ({mode})")
    print(f"{'='*60}")

    all_changes = {"p0": [], "p1": [], "p2": []}

    if args.p0 or args.all:
        print("\n[P0] Sentence-like titles -> resolver_enabled: False")
        changed = apply_p0(registry, dry_run=dry_run)
        all_changes["p0"] = changed
        print(f"  Changed: {len(changed)}")

    if args.p1 or args.all:
        print("\n[P1] Separate conflicts -> remove misleading aliases")
        changes = apply_p1_separate(registry, dry_run=dry_run)
        all_changes["p1"] = changes
        for c in changes:
            print(f"  {c['action']}: {c['surface']}")
            print(f"    {c['from']} -> {c['to']}")

    if args.p2 or args.all:
        print("\n[P2] Merge conflicts -> deprecated + redirects")
        changes = apply_p2_merge(registry, dry_run=dry_run)
        all_changes["p2"] = changes
        for c in changes:
            print(f"  {c['action']}: {c['surface']}")
            print(f"    {c['from']} -> {c['to']}")

    if not dry_run:
        registry.save()
        print("\n[SAVED] Changes persisted to registry")

    # 显示结果
    print(f"\n{'='*60}")
    print("After Cleanup Status")
    print(f"{'='*60}")

    registry2 = ConceptRegistry(vault_dir).load()
    print_status(registry2)


if __name__ == "__main__":
    main()
