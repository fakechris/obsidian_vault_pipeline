---
title: "Function calling enables agents to execute external tools"
type: evergreen
date: 2026-04-06
tags: [evergreen]
aliases: ["function-calling-enables-agent-tool-execution"]
---

# Function calling enables agents to execute external tools

> **一句话定义**: Function Calling是Agent调用外部函数/工具的技术机制，使AI Agent能够突破模型本身的能力边界，执行实际操作。

## 📝 详细解释

### 是什么？
Function Calling允许LLM生成结构化的函数调用请求，而非仅生成文本。Agent可以调用搜索引擎、数据库API、代码执行环境、文件操作等外部工具。这种机制使得AI的能力从'回答问题'扩展到'解决问题'。典型的Function Calling流程包括：1) 定义工具签名（名称、参数schema）；2) LLM根据任务判断是否需要调用工具；3) 执行工具并返回结果；4) LLM基于结果生成最终响应。

### 为什么重要？
Function Calling是实现Agent自主行动能力的关键技术，是连接LLM推理能力与真实世界操作的桥梁。

## 🔗 关联概念
- [[ai-agents-perception-reasoning-action-loop]]
- [[Tool Use]]
- [[MCP Protocol]]

## 📚 来源与扩展阅读
- [[2026-04-06_test-ai-article_深度解读]]
