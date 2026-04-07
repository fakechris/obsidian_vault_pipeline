---
title: "ReAct Reasoning结合推理与行动执行"
type: evergreen
date: 2026-04-06
tags: [evergreen]
aliases: ["react-reasoning-combines-inference-with-action"]
---

# ReAct Reasoning结合推理与行动执行

> **一句话定义**: ReAct（Reasoning + Acting）是一种prompting策略，在推理过程中交替进行思考和行动调用，形成推理与行动的交替循环。

## 📝 详细解释

### 是什么？
ReAct策略的核心是在每个推理步骤中：先思考当前状态和目标，然后决定是否需要调用工具行动，再根据行动结果进行下一轮推理。与纯推理的Chain of Thought不同，ReAct强调与环境的交互——通过行动获取额外信息（如搜索结果、API响应），将结果纳入上下文再继续推理。这种方式使Agent能够处理需要外部信息的多步骤任务。

### 为什么重要？
ReAct是Agent实现复杂任务自动化的关键推理策略，解决纯语言模型无法获取实时信息的局限。

## 🔗 关联概念
- [[Chain-of-Thought]]
- [[Perception-Reasoning-Action-Loop]]
- [[AI-Agent]]

## 📚 来源与扩展阅读
- [[2026-04-06_test-ai-article_深度解读]]
