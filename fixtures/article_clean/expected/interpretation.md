---
title: A Guide to Agent-native Product Management
source: https://every.to/guides/ai-product-management-guide
author: 原文未说明
date: 2026-05-04
type: article
tags: ["AI产品管理", "Agent", "产品策略", "LLM", "Compound-Engineering"]
status: completed
area: ai
canonical_concepts: []
concept_candidates: [8020, agent-native-product-management, approach, compound-engineering, crossing-the-chasm, good-strategy-bad-strategy, product-pulse, product-strategy, sdlc, smart, strategy-document, target-problem, 对话即工作]
link_resolution_status: resolved
link_resolution_version: v2
pipeline_run_id: 20260504-230446-cd910f66
original_note_type: ai
---

```

# Agent-native Product Management

## 一句话定义

Agent-native Product Management 是一种借助 LLM 和 AI Agent 将产品管理工作中跨学科的重复性任务（数据分析、需求撰写、工单创建等）转化为对话式交互的工作方法论，使 PM 从工具疲劳中解放，专注于真正的策略决策。

---

## 详细解释

### What：什么是 Agent-native PM

Agent-native Product Management 是一种将 AI Agent 深度整合到产品管理全流程的工作方式。其核心转变在于：**对话即工作（The conversation is the work）**。传统 PM 需要在 100+ 款软件工具之间切换，完成数据分析、SQL 查询、工单撰写、用户反馈整理等任务；而在 Agent-native 模式下，这些工作通过与 Claude Code 等 Agent 的自然语言对话即可完成。

### Why：为什么需要这种转变

1. **工具过载问题**：现代公司平均订阅超过 100 款软件，PM 因其跨职能特性受到的影响尤为严重，导致严重的职业倦怠。
2. **效率革命**：原本需要 3 小时的分析调查现在几分钟内完成；原本两周一次的产品评审现在从一条仓促的消息中就能提取关键信息。
3. **角色重新定位**：当执行层面的工作被 AI 接管后，PM 的核心价值从"做"转向"想"——专注于产品策略、优先级判断和价值创造。

### How：如何实践 Agent-native PM

文章描述的核心工作流程基于 **Compound Engineering** 框架，包含三个阶段：

**Plan（规划阶段）**：
- 从产品策略文档开始，使用 `/ce-strategy` 命令进行结构化访谈
- 策略文档指导功能构思、优先级排序和功能规格撰写
- 策略应每季度重新审视

**Ship（构建阶段）**：
- 借助 Agent 完成技术实现
- 确保功能正常工作并完成部署

**Review（复盘阶段）**：
- 收集构建过程中的学习成果
- 定期检查关键指标（Product Pulse）
- 将学习反馈到策略迭代中

---

## 重要细节

### 1. Strategy Document 的五大核心要素

基于 Richard Rumelt 的《Good Strategy Bad Strategy》，策略文档包含：

| 要素 | 说明 | 关键要点 |
|------|------|----------|
| Target Problem | 用户当前感受到的痛点 | 最好是周期性、高成本的痛点 |
| Approach | 产品的指导性政策 | 不是目标或功能描述，而是独特的解决角度 |
| Who it's for | 目标用户画像 | 早期聚焦 1-2 个核心画像（参考《Crossing the Chasm》） |
| Key Metrics | 3-5 个关键指标 | 必须是 S.M.A.R.T. 指标，至少追踪人和钱 |
| Tracks | 2-4 个核心能力轨道 | Track 1 通常是核心性能/平台 |

### 2. 从 20/80 到 80/20 的范式转变

软件开发已从"20% 规划 + 80% 执行"转变为"**80% 规划 + 20% 执行**"。这是因为 AI 大幅提升了执行效率，使得高质量的前期规划成为真正的差异化因素。

### 3. S.M.A.R.T. 指标的具体应用

作者以 Spiral 的"drafts exported"指标为例说明：避免浅层指标（如页面浏览量）和虚荣指标，选择能真正证明用户获得价值的指标。

### 4. 对话式策略访谈的工作机制

`/ce-strategy` 命令会引导用户完成结构化访谈，当答案模糊时会主动追问："Whose situation specifically? What do they try today, and why doesn't it work?" 这种设计确保输出高质量的策略文档而非泛泛而谈。

---

## 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                    Agent-native PM Workflow                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│    ┌──────────────┐     ┌──────────────┐     ┌──────────────┐   │
│    │     PLAN     │────▶│     SHIP     │────▶│    REVIEW    │   │
│    │   (80%)      │     │    (20%)     │     │              │   │
│    └──────────────┘     └──────────────┘     └──────────────┘   │
│          │                    │                    │            │
│          ▼                    ▼                    ▼            │
│    ┌──────────────┐     ┌──────────────┐     ┌──────────────┐   │
│    │  Strategy.md │     │  /ce-strategy│     │ Product Pulse│   │
│    │  + Interview │     │   + Agent    │     │  + Metrics   │   │
│    └──────────────┘     └──────────────┘     └──────────────┘   │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│                    Agent as Central Interface                    │
│                                                                  │
│              ┌─────────────────────────────────┐                │
│              │     "The conversation is       │                │
│              │          the work"             │                │
│              │                                 │                │
│              │  SQL Queries  │  Tickets  │  Analytics  │      │
│              └─────────────────────────────────┘                │
└─────────────────────────────────────────────────────────────────┘
```

---

## 行动建议

### 建议一：建立结构化的产品策略文档流程

**具体步骤**：
1. 在团队中推广使用 `/ce-strategy` 或类似工具进行策略访谈
2. 策略文档应包含：Target Problem、Approach、Who it's for、Key Metrics、Tracks 五个核心部分
3. 每季度至少重新审视一次策略，确保与市场变化同步
4. 将策略文档作为功能优先级排序的最终依据

### 建议二：将 PM 效率工具链简化为"对话即工作"模式

**具体步骤**：
1. 审计当前使用的 PM 工具（通常超过 100 个订阅），识别可被 Agent 替代的任务
2. 选择一个核心 Agent 工具（如 Claude Code）作为主要工作界面
3. 将数据查询、工单撰写、用户反馈整理等任务转化为对话式交互
4. 定期评估哪些工作仍然需要专用工具，持续优化工作流

---

## 关联知识

- Compound-Engineering — 本文基于的工程方法论框架，包含 Agent-native 开发的核心原则
- Product-Strategy — 策略文档是 Agent-native PM 的起点和核心锚点
- S.M.A.R.T. — 关键指标的制定标准，用于确保指标的有效性和可追踪性
- SDLC — 软件开发生命周期，Plan-Ship-Review 三阶段是 SDLC 的现代演进
- Crossing-the-Chasm — Geoffrey Moore 的技术采用生命周期模型，指导早期用户画像聚焦策略

<!-- ovp-promotions -->
> 由 OVP Pipeline 自动提取的 Evergreen 概念
- [[agent-native-product-management]]
- [[conversation-is-the-work]]
- [[tool-overload-problem]]
- [[pm-role-shift-from-doing-to-thinking]]
- [[compound-engineering-plan-ship-review]]
- [[strategy-document-five-elements]]
- [[target-problem-element]]
- [[approach-element]]
- [[who-its-for-element]]
- [[key-metrics-element]]
- [[tracks-element]]
- [[80-20-planning-paradigm-shift]]
- [[ce-strategy-command]]
- [[product-pulse]]
- [[drafts-exported-metric]]
- [[smart-metrics]]
- [[ai-efficiency-gain-example]]
<!-- /ovp-promotions -->
