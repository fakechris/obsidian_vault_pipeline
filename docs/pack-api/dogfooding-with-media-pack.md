# Dogfooding With A Media Pack

这份文档解释为什么媒体 pack 要严格按 Pack API 来做，而不是在 core 仓库里硬编码。

## 1. 为什么要吃自己的狗粮

如果 Pack API 只是对外文档，而我们自己做媒体项目时不用它，这套 API 很快就会变成空壳。

真正能把接口做对的方法只有一个：

> 我们自己先按这套接口做第一个强需求外部 pack。

媒体领域正适合承担这个角色，因为它和 `default-knowledge` 的差异足够大：

- 对象模型不同
- workflow DAG 不同
- lint 规则不同
- 反馈闭环不同

这会逼 core 把边界做实。

## 2. 媒体 Pack 不应该直接进 Core

媒体项目应该单独一个工程，例如：

```text
openclaw-pack-media-editorial
```

原因：

- 它的对象模型不是 core 默认对象模型
- 它的 prompts / schemas 更新速度更快
- 它的评估标准更依赖编辑部实际反馈
- 它的发布节奏不该和 core 绑定

## 3. 媒体 Pack 如何验证 Pack API 是否可用

媒体 pack 应该至少覆盖下面这些对象：

- `raw_source`
- `evidence_packet`
- `event`
- `angle`
- `writing_sheet`
- `topic_card`
- `research_brief`
- `draft`
- `feedback`

如果 Pack API 能把这些对象接进来，说明平台边界基本是成立的。

## 4. 媒体 Pack 应该优先验证什么

不是先验证“能不能自动写出爆款稿”，而是先验证：

1. 能不能定义对象模型
2. 能不能注册 workflow profiles
3. 能不能通过 discovery hooks 找到事件/角度/历史相似稿
4. 能不能通过 lint hooks 执行事实与风格门禁
5. 能不能把编辑反馈写回 pack 自己的规则系统

## 5. 建议的媒体 Pack 落地顺序

### Phase 1

- `daily-desk` profile
- Topic Card
- Research Brief
- Fact Lint
- Style Lint

### Phase 2

- Outline
- Neutral Draft
- Style Pass

### Phase 3

- Publish feedback
- Writing Sheet 自动更新
- Topic scoring 更新

## 6. 这样做的收益

这样做的好处正是你刚才说的两点：

1. 我们建立自己的 Pack API 文档和开发体系
2. 媒体项目直接套用这套体系，逼着我们把接口、流程、边界都跑通

结果是：

- 我们不是“先写一套文档，再希望别人用”
- 而是“先自己按文档做出真实 pack，再把这套文档变成真正可复用的开发体系”

## 7. 最终原则

core 应该服务 pack，pack 不应该污染 core。

`default-knowledge` 作为第一个标准 pack，负责稳定平台基线。  
`media-editorial` 作为第一个强验证外部 pack，负责验证平台是否真的可扩展。  
其他领域包以后都沿着同一条路开发。
