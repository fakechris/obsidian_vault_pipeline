---
title: "ReAct strategy combines reasoning with action execution"
type: evergreen
date: 2026-04-06
tags: [evergreen]
aliases: ["react-reasoning-strategy"]
---

# ReAct strategy combines reasoning with action execution

> **一句话定义**: ReAct（Reasoning + Action）是一种结合推理与行动的prompt策略，使Agent在推理过程中同步规划行动。

## 📝 详细解释

### 是什么？
ReAct是Google提出的推理策略，核心思想是在推理过程中同时考虑行动方案。传统Chain of Thought（思维链）仅关注推理过程，而ReAct将推理分为‘思考(Thought)’和‘行动(Act)’两个交替阶段：思考阶段分析情况、规划行动；行动阶段执行操作、获取结果。这种交替迭代使Agent能够在推理过程中实时利用环境反馈调整行动方案，特别适合需要多步骤工具调用的复杂任务。ReAct已成为AI Agent推理层的核心技术策略之一。

### 为什么重要？
ReAct策略是将推理能力转化为实际行动的关键技术，是实现Agent自主性的核心机制。

## 🔗 关联概念
- [[Chain-of-Thought]]
- [[ai-agent-perception-reasoning-action-loop]]
- [[Tool-Use]]

## 📚 来源与扩展阅读
- [[2026-04-06_test-ai-article_深度解读]]
