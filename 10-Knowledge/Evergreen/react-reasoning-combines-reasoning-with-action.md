---
title: "ReAct reasoning combines reasoning with action"
type: evergreen
date: 2026-04-06
tags: [evergreen]
aliases: ["react-reasoning-combines-reasoning-with-action"]
---

# ReAct reasoning combines reasoning with action

> **一句话定义**: ReAct (Reasoning + Action) 是一种prompt策略，将推理过程与行动调用结合，使Agent能够边思考边执行。

## 📝 详细解释

### 是什么？
ReAct是Yao等人在2022年提出的推理框架，其核心思想是在每个推理步骤中同时生成思考(Thought)、行动(Action)和观察(Observation)。与传统Chain of Thought不同，ReAct允许Agent在推理过程中主动调用工具。例如：Thought（思考）→Action（调用搜索API）→Observation（获得搜索结果）→基于结果继续推理。这种'边想边做'的模式使Agent能够处理需要外部信息的多步骤任务，避免了纯推理模型可能出现的'幻觉'问题。ReAct在复杂推理任务上表现优于纯CoT或仅使用搜索的方法。

### 为什么重要？
ReAct是实现Agent推理能力的重要技术选择，尤其适用于需要调用工具获取外部信息的任务。

## 🔗 关联概念
- [[Chain of Thought]]
- [[ai-agents-perception-reasoning-action-loop]]
- [[Function Calling]]

## 📚 来源与扩展阅读
- [[2026-04-06_test-ai-article_深度解读]]
