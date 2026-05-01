---
schema_version: "1.0.0"
note_id: react-reasoning-combines-thought-and-action-8d4fe6c2
title: "ReAct Reasoning策略结合推理与行动"
type: evergreen
entity_type: framework
date: 2026-04-06
tags: [evergreen]
aliases: ["ReAct-reasoning-combines-thought-and-action"]
---

# ReAct Reasoning策略结合推理与行动

> **一句话定义**: ReAct (Reasoning + Acting) 是一种prompt策略，让Agent在推理过程中交替进行思考和行动调用，通过实际行动获取的反馈来增强推理准确性。

## 📝 详细解释

### 是什么？
ReAct策略由Google提出，核心思想是在每个推理步骤中，Agent不仅生成思考(thought)，还决定是否需要采取行动(action)。例如：思考「用户询问最新股价」→ 行动「调用股票API获取价格」→ 观察「返回数据」→ 思考「基于数据进行分析」。这种交织方式使Agent能够：1) 获取实时信息辅助推理；2) 纠正推理错误；3) 处理需要多步骤信息收集的复杂问题。与单纯的Chain of Thought相比，ReAct通过实际行动获取外部知识，减少幻觉。

### 为什么重要？
ReAct是Agent实现有效推理的核心策略之一，使Agent能够主动获取环境信息而不是仅依赖训练数据，是构建可靠Agent系统的关键技术。

## 🔗 关联概念
- [[Reasoning]]
- [[Chain-of-Thought]]
- [[AI-Agent]]
- [[Observation]]
- [[Tool-Use]]

## 📚 来源与扩展阅读
- [[2026-04-06_test-ai-article_深度解读]]

## 🔗 自动建议链接 (link-suggest)
<!-- link-suggest:backfill -->

- [[2026-04-06-test-ai-article-深度解读|2026 04 06 test ai article 深度解读]]
- [[ai-agent-has-perception-reasoning-action-capabilities|AI Agent具有感知、推理、行动三大核心能力]]
- [[ai-agent-performs-perception-reasoning-action-d533b9f1|AI Agent执行感知-推理-行动的反馈循环]]
- [[multi-modal-perception-e8af11cc|AI Agent感知模块支持多模态输入]]
- [[react-reasoning-strategy-80329943|📝 详细解释]]
