# Pack Manifest And Hooks

本文件定义 Pack API v1 的建议接口。

注意：

- 当前仓库已经实现了最小 pack runtime、profile 选择和 plugin loader
- 这里既描述 **当前已支持的接口**，也描述 Pack API v1 的目标接口
- pack 作者应该优先按“已支持部分”接入，再逐步使用更完整的 hooks 面

当前已支持的最小能力：

- manifest 校验
- `entrypoints.pack`
- `BaseDomainPack`
- `--pack / --profile`
- entry point / manifest 两种发现方式
- `api_version` 兼容性检查

## 1. Manifest

每个 pack 应提供一个 manifest。

推荐 YAML：

```yaml
name: media-editorial
version: 0.1.0
api_version: 1
display_name: Media Editorial Pack
description: Editorial workflow pack for finance/media production

object_kinds:
  - raw_source
  - evidence_packet
  - event
  - angle
  - writing_sheet
  - topic_card
  - research_brief
  - draft
  - feedback

workflow_profiles:
  - daily-desk
  - weibo-fastlane

resources:
  schemas:
    - schemas/event.yaml
    - schemas/topic_card.yaml
  templates:
    - templates/topic-card.md
    - templates/research-brief.md
  prompts:
    - prompts/topic-card-generator.md
    - prompts/research-brief.md

entrypoints:
  pack: openclaw_pack_media.plugin:get_pack
```

## 2. Python Entrypoint

Pack 必须暴露一个 entrypoint：

```python
def get_pack() -> BaseDomainPack:
    ...
```

core 通过它拿到 pack 对象。

当前实现支持两种加载路径：

1. 通过 Python entry point 组 `openclaw_pipeline.packs`
2. 通过 manifest 文件列表 `OPENCLAW_PACK_MANIFESTS`

manifest 的 `entrypoints.pack` 仍然是最终入口。

## 3. BaseDomainPack 建议接口

```python
class BaseDomainPack:
    name: str
    version: str
    api_version: int

    def object_kinds(self) -> list[ObjectKindSpec]:
        ...

    def workflow_profiles(self) -> list[WorkflowProfile]:
        ...

    def artifact_specs(self) -> list[ArtifactSpec]:
        ...

    def assembly_recipes(self) -> list[AssemblyRecipeSpec]:
        ...

    def governance_specs(self) -> list[GovernanceSpec]:
        ...

    def discovery_hooks(self) -> DiscoveryHooks:
        ...

    def absorb_hooks(self) -> AbsorbHooks:
        ...

    def refine_hooks(self) -> RefineHooks:
        ...

    def lint_hooks(self) -> LintHooks:
        ...

    def templates_dir(self) -> Path:
        ...

    def prompts_dir(self) -> Path:
        ...
```

### Contract Families: Minimal Examples

`ArtifactSpec` 声明 pack 真正拥有的持久 artifact family：

```python
ArtifactSpec(
    name="research_claim",
    pack="research-tech",
    layer="canonical",
    family="claim",
    object_kind="claim",
    description="Source-grounded claim rows and canonical note fields.",
    storage_policy=ArtifactStoragePolicy(
        storage_mode="truth_row",
        truth_row_family="claims",
    ),
)
```

`AssemblyRecipeSpec` 声明 pack 能稳定编译出的 access artifact：

```python
AssemblyRecipeSpec(
    name="topic_overview",
    pack="research-tech",
    recipe_kind="topic_overview",
    description="Compile a topic-facing overview page.",
    source_contract_kind="wiki_view",
    source_contract_name="overview/topic",
    output=AssemblyOutputSpec(
        output_mode="markdown",
        publish_target="compiled_markdown",
    ),
)
```

`GovernanceSpec` 声明 pack 暴露的 review / signal / resolver policy：

```python
GovernanceSpec(
    name="research_governance",
    pack="research-tech",
    description="Review queues, signal semantics, and resolver rules.",
    review_queues=[
        ReviewQueueSpec(name="contradictions", description="Contradiction review queue"),
    ],
    signal_rules=[
        SignalRuleSpec(
            signal_type="source_needs_deep_dive",
            description="Processed source note missing a deep dive.",
            resolver_rule="deep_dive_workflow",
            auto_queue=True,
        ),
    ],
    resolver_rules=[
        ResolverRuleSpec(
            name="deep_dive_workflow",
            description="Queue a deep-dive workflow.",
            resolution_kind="focused_action",
            target_name="deep_dive_workflow",
            dispatch_mode="queue_only",
            executable=True,
            safe_to_run=True,
        ),
    ],
)
```

### Declared / Inherited / Missing

- `declared`: 当前 pack 自己声明了这个 contract family。
- `inherited`: 当前 pack 没有声明，但 compatibility chain 上游 pack 提供了可生效 contract。
- `missing`: 当前 pack scope 下没有可解析的 contract；UI / export / doctor 应该把它当成真实缺口，而不是静默回退。

兼容 pack 的规则应该始终按这个顺序理解：

1. 先看当前 pack `declared`
2. 再看 compatibility base 上的 `inherited`
3. 最后才是 `missing`

### Pack 作者怎么验证

- `ovp-doctor --pack <pack> --json`
  - 看 `contracts.declared.*` 确认 pack 自己声明了什么
  - 看 `contracts.effective.*` 确认 compatibility chain 实际生效了什么
  - 看 `contracts.shell.governance_contract` 确认 shared shell 的治理合同来自谁
- `ovp-export --pack <pack> --target <target>`
  - 验证 export target 是否能解析到 `assembly recipe -> source contract -> source provider`
- `ovp-ui --vault-dir <vault>`
  - 验证页面级 `Assembly Contract` / `Governance Contract`
  - 验证 signals / actions / briefing 等 item-level provenance 是否显示
    - `resolver_rule_name`
    - `governance_provider_name`
    - `governance_provider_pack`

## 4. Object Kind Spec

Pack 可以注册新的 object kinds。

```python
@dataclass
class ObjectKindSpec:
    kind: str
    display_name: str
    description: str
    canonical: bool
    schema_ref: str | None = None
```

示例：

- `concept`
- `entity`
- `event`
- `angle`
- `claim`
- `writing_sheet`

## 5. Workflow Profile

每个 pack 可以注册一个或多个 profile。

```python
@dataclass
class WorkflowProfile:
    name: str
    description: str
    stages: list[str]
    supports_autopilot: bool = False
```

例如：

```python
WorkflowProfile(
    name="daily-desk",
    description="Daily editorial desk workflow",
    stages=[
        "ingest",
        "normalize",
        "event_cluster",
        "topic_card",
        "desk_review",
        "research_brief",
        "outline",
        "neutral_draft",
        "style_pass",
        "fact_lint",
        "style_lint",
        "editor_review",
        "derive",
    ],
)
```

## 6. Discovery Hooks

Pack 不能改变 core 的 identity 原则，但可以改变“发现什么对象”。

建议：

```python
class DiscoveryHooks(Protocol):
    def discover_object_candidates(
        self,
        *,
        vault_dir: Path,
        query: str,
        retrieval_evidence: list[dict[str, object]],
        limit: int = 10,
    ) -> list[dict[str, object]]:
        ...
```

输出应该是结构化对象候选，而不是随便一段文本。

## 7. Absorb Hooks

Absorb hook 负责把 interpret 层产物编入该领域知识体系。

```python
class AbsorbHooks(Protocol):
    def absorb(
        self,
        *,
        source_note: Path,
        evidence: dict[str, object],
    ) -> dict[str, object]:
        ...
```

输出必须结构化，至少包含：

- `decision_type`
- `action`
- `target_kind`
- `target_id`
- `confidence`
- `requires_review`

## 8. Refine Hooks

Refine hook 负责整理已有对象。

```python
class RefineHooks(Protocol):
    def propose_cleanup(...): ...
    def propose_breakdown(...): ...
    def execute_mutation(...): ...
```

媒体 pack 可以把它扩展成：

- `topic_card`
- `research_brief`
- `outline`
- `style_pass`

但执行副作用仍要经过 core 审计。

## 9. Lint Hooks

不同领域的质量门槛不同，lint 必须 pack 化。

```python
class LintHooks(Protocol):
    def run_fact_lint(...): ...
    def run_style_lint(...): ...
    def run_domain_lint(...): ...
```

示例：

- 媒体：事实漂移、标题正文不一致、AI 腔、低可信来源
- 医疗：证据等级、危险建议、禁忌症遗漏
- 编程：过时信息、错误 API、性能/安全误导

## 10. Evidence Schema

所有 pack 都应消费同一套 core evidence buckets：

- `identity_evidence`
- `retrieval_evidence`
- `graph_evidence`
- `audit_evidence`

Pack 可以追加领域 evidence，但不能删除 core evidence。

示例：

```python
{
  "identity_evidence": [...],
  "retrieval_evidence": [...],
  "graph_evidence": [...],
  "audit_evidence": [...],
  "domain_evidence": {
    "topic_fit": [...],
    "writing_sheet_matches": [...],
  }
}
```

## 11. Versioning

Pack 需要双版本：

- `version`
  pack 自己的版本
- `api_version`
  对应 core Pack API 的版本

core 应只加载兼容 `api_version` 的 pack。

## 12. 测试要求

每个 pack 至少要有：

- manifest validation tests
- workflow profile tests
- discovery hook tests
- absorb/refine contract tests
- lint rule tests

如果 pack 要接 autopilot，还要有：

- idempotency tests
- retry safety tests
- audit log tests

## 13. Pack 作者检查清单

- 对象模型是否独立清楚
- schema 是否完整
- workflow 是否不是“一键全自动乱写”
- 重大判断是否输出结构化结果
- 是否复用了 core evidence schema
- 是否服从 core identity / audit / derived 规则

如果这些做不到，这个 pack 还不应该发布。
