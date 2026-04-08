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
