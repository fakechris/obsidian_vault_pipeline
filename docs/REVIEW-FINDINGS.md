# PR #74–#96 Review Findings — 待讨论事项

> 本文档记录 PR #74–#96 全量 Review 中识别的核心 **User Story / Product Design** 和 **Architecture Design** 问题。
> Bug 修复完成后，本文档是讨论优先事项的基础。
>
> 生成时间: 2026-04-30

---

## 一、User Story / Product Design 问题

### 1.1 读者旅程缺乏端到端验证（Critical）

**现状**: UI Shell 从 `/` 入口开始提供了丰富的浏览面板（objects、atlas、clusters、evolution、signals、briefing），但缺少一条端到端的"Reader Happy Path"验证——即一个新用户打开 dashboard → 发现感兴趣的主题 → 深入阅读 → 获得 insight 这条完整链路没有被任何测试或 dogfood 流程覆盖。

**风险**: 功能可能在"展示面板数据"层面 OK，但"发现→深入→收获"的体验断裂会导致产品价值无法兑现。

**建议**:
- 定义 3-5 条关键 Reader Scenario（如 "atlas 探索 → concept 深入 → evidence 追溯"）
- 每个 Scenario 用 Playwright/browser-test 或 manual dogfood checklist 覆盖
- 将 Scenario 通过率作为 Milestone 的 gate 条件

### 1.2 Operator 与 Reader 的混合呈现（Major）

**现状**: 同一个 UI Shell 同时服务两类截然不同的用户：
- **Reader**（消费知识的人）：关心 concept、evidence、reading route
- **Operator**（维护 pipeline 的人）：关心 action queue、repair markers、signal ledger

两类功能在同一个 dashboard 上平铺，导致 Reader 看到大量不相关的运维面板。

**建议**:
- 引入 `?mode=reader` / `?mode=operator` 参数或双入口
- Reader mode 隐藏 action queue、repair markers、signal ledger
- Operator mode 保留全部面板

### 1.3 产品文档膨胀（Major）

**现状**: `BACKLOG.md` 已从结构化任务追踪退化为一个 run-on paragraph：
- BL-XXX 和 KSR-XXX 条目混杂
- 已完成和未完成没有清晰分界
- 缺少按 Milestone 分组的视图

**建议**:
- 拆分为 `BACKLOG-ACTIVE.md`（当前 Sprint 目标）和 `BACKLOG-DONE.md`（已完成归档）
- 每个条目标注关联 Milestone（M0-M7）
- 考虑迁移到 GitHub Issues/Projects 以获得自动状态跟踪

### 1.4 "质量" 对用户不可见（Minor）

**现状**: 6 维度质量评分（Definition、Explanation、Details、Structure、Actionable、Linking）仅在 pipeline 内部使用，Reader 无法在 UI 上看到一篇深度解读的质量评级。

**建议**:
- 在 object page 上以 badge/stars 展示质量评分
- 低于 3.0 分的条目标注 "Draft" 警告
- 提供质量维度的 drill-down 视图

---

## 二、Architecture Design 问题

### 2.1 四层架构边界模糊（Critical）

**现状**: `ARCHITECTURE.md` 定义了四层架构：
1. **Layer 1 — Canonical Knowledge**: registry + vault markdown
2. **Layer 2 — Derived Indexes/Views**: knowledge.db, projections
3. **Layer 3 — Context Assembly/Access**: working-memory, prime, assembly recipes
4. **Layer 4 — Governance/Control Plane**: action queue, signal ledger, review queues

但实际代码中层间调用常跨层：
- `truth_api.py`（L2）直接操作 action queue（L4）
- `ui_server.py`（L3 surface）直接调用 L4 mutation 端点
- `doctor.py`（L4 诊断）直接读取 L2 的 knowledge.db

**风险**: 任何 L2 变更（如 knowledge.db schema 升级）会影响 L4 代码，反之亦然。变更成本随时间累积。

**建议**:
- 每层通过显式的 API 模块暴露接口（如 `truth_api.py` 只暴露 L2 接口，L4 操作移到 `governance_api.py`）
- 在 CI 中加入 import-graph lint（如 `import_linter`），强制层间依赖方向
- 当前 `truth_api.py` (6099 行) 和 `ui_server.py` (6035 行) 是违反最严重的两个文件

### 2.2 JSONL 日志无 Rotation 机制（Critical）

**现状**: `pipeline.jsonl`、`reuse-events.jsonl`、`projection-repair.jsonl`、`actions.jsonl` 均为 append-only 无上限。`build_runtime_state()` 和 `_feedback_payload()` 等函数在每次调用时全文扫描这些文件。

**风险**: 日志线性增长 → 读取耗时线性增长 → `ovp doctor` 和 UI dashboard 越来越慢 → 最终超时或 OOM。

**建议**:
- 引入 JSONL rotation：每达到 N 行或 M MB 时，将旧数据 snapshot 到 `*.YYYYMMDD.jsonl` 并重置主文件
- `_summarize_event_log()` 和 `_summarize_reuse_event_log()` 改为只读最近 N 行的 tail-read 策略
- 对只需要聚合统计的场景，在 rotation 时将统计写入 sidecar JSON（如 `pipeline-stats.json`）

### 2.3 巨型文件的拆分计划（Critical）

**现状**:
| 文件 | 行数 | 职责 |
|------|------|------|
| `ui_server.py` | 6035 | HTTP routing + HTML rendering + mutation handlers |
| `truth_api.py` | 6099 | Truth query + Governance mutations + Search |
| `doctor.py` | 1485 | Pack 诊断 + 健康检查 |

**建议**:
- `ui_server.py` → 拆分为 `ui_routes.py`（路由分发）、`ui_renderers.py`（HTML 渲染）、`ui_mutations.py`（POST 处理）
- `truth_api.py` → 拆分为 `truth_queries.py`（L2 读取）、`governance_api.py`（L4 操作）、`search_api.py`（FTS 查询）
- 每个文件控制在 1000 行以内

### 2.4 时间工具函数重复（Major）

**现状**: 以下函数在多个模块中重复实现：
- `_utc_now()` → `projection_lifecycle.py`、`runtime_state.py`
- `_parse_dt()` → `projection_lifecycle.py`、`runtime_state.py`
- `_format_dt()` → `projection_lifecycle.py`、`runtime_state.py`

`runtime.py` 中有 `format_utc_timestamp()` 但格式与上述函数不完全一致（strftime vs isoformat）。

**建议**:
- 在 `runtime.py` 中统一定义 `utc_now()`、`parse_utc_timestamp()`、`format_utc_timestamp()`
- 其他模块统一 import，消除重复

### 2.5 Architectural Fitness 测试形同虚设（Major）

**现状**: `tests/test_architecture_fitness.py` 存在但其断言过于粗放：
- 只检查 import 是否成功
- 不验证层间依赖方向
- 不验证模块职责边界

**建议**:
- 加入 import-graph 依赖方向测试（L1 不能 import L4）
- 加入文件行数上限测试（触发拆分时自动 fail）
- 加入 public API surface 测试（防止内部函数被外部使用）

### 2.6 安全架构缺失（Major）

**现状**: UI server（`ui_server.py`）运行在 `0.0.0.0:9111`（默认配置），存在：
- 无认证/鉴权
- 无 CSRF 保护
- `next` 参数 open redirect
- 无 Content Security Policy

**建议**:
- 默认绑定 `127.0.0.1`（仅本地访问）
- POST 表单加入 CSRF token
- `next` 参数白名单（仅允许相对路径）
- 添加基本的 CSP header

---

## 三、修复后讨论议程

1. **Reader Happy Path**: 是否需要在 M7 前定义并验证 Reader Scenario？
2. **Reader vs Operator 分离**: 是否引入 mode 参数？还是彻底拆分两个端口？
3. **JSONL Rotation 策略**: threshold 设多大？snapshot 格式是 JSONL 还是聚合 JSON？
4. **巨型文件拆分优先级**: 先拆 `ui_server.py` 还是 `truth_api.py`？
5. **四层架构 Lint**: 是否引入 `import_linter` 或自定义 AST 检查？
6. **BACKLOG 管理**: 留在 Markdown 还是迁移到 GitHub Issues？
