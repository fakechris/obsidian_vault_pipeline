# 2026-05-05 — Test Infra Retrospective

> **触发**:今天发现 `step_entity_extract` 已经静默失败了 ~3 天(2026-05-01 PR #99 落地以来),原因是 `step_absorb` 的 staged-tempdir 路径泄漏到 `processed_files`,tempdir 退出后路径失效,entity_extract 用 `if not fpath.exists(): continue` 静默跳过全部输入。
>
> **症状**:三天里每次 incremental 跑 entity_extract 都报 `✅ 成功 | Mentions: 0`,`entity-extractions.jsonl` 上次写入是 2026-05-02 16:25。pipeline report 看起来全绿。
>
> **关键问题**:这是 PATCH-1(silent-fallback bug 导致 entity_extract 退化到 7-day rglob)的兄弟问题。**PATCH-1 的回归测试(`test_absorb_to_entity_extract_chain`)就在仓库里跑着,本应抓住这一类**,但它没抓到。这次复盘的核心是问:**为什么我们已有的 e2e 测试没抓到这个 bug**。

---

## 1. 现有 e2e 测试做对了什么

`tests/test_e2e_acceptance.py` 是 PATCH-1 之后专门加的"防御 silent-fallback"基础设施。它做对了三件事:

1. **结构正确**:跑了 source → absorb → entity_extract → dedup → knowledge_index 整链路
2. **类型化契约**:assert 每步返回 typed StepResult,而不是 dict
3. **跨步骤数据流**:`pipeline.step_results["absorb"] = absorb_result` 模拟 dispatcher,然后让 entity_extract 从 typed contract 读 `processed_files`

---

## 2. 现有 e2e 测试漏了什么

**它 mock 了错误的层。**

具体地,`test_absorb_to_entity_extract_chain` 在 line 110-112 里:

```python
with patch(
    "ovp_pipeline.unified_pipeline_enhanced.run_absorb_workflow",
    return_value=_canned_absorb_payload(synthetic_deep_dive),
):
    absorb_result = pipeline.step_absorb(recent_days=7)
```

`run_absorb_workflow` 是 absorb 的**最内层** workflow function。这个 mock 直接返回 canned payload,payload 里 `result["file"]` 是 `str(synthetic_deep_dive)` —— 已经是 vault 路径。

所以 mock **完全跳过了 staging 这一层逻辑**:

```
step_absorb(qualified_files=[...])
  ├─ 创建 tempfile.TemporaryDirectory("absorb-qualified-XXX")     ← 这里被跳过
  ├─ 把每个 qualified file symlink 进 tempdir                     ← 这里被跳过
  ├─ 调用 _run_absorb_workflow_direct(directory=tempdir)         ← 这里被跳过
  │    └─ 调用 run_absorb_workflow(...)                          ← MOCK 在这一层
  ├─ 收集 results,构造 processed_files = [item["file"] for ...]  ← 这里被跳过(走的是 fake payload)
  └─ tempdir.cleanup() ← 这里被跳过(因为根本没有真 tempdir)
```

**Bug 就藏在 mock 跳过的两层之间的边界上**:
- `_run_absorb_workflow_direct` 内部用 staged 路径
- 出来时 `staged_sources` 没回填到 `item["file"]`
- `step_absorb` 把 staged 路径放进 `processed_files`
- tempdir 清理后路径失效

E2E 测试在最内层 mock,把 staged path 这一整层"擦掉"了。然后 `processed_files` 里塞的是一个永远存在的 vault 路径(因为是 mock 提供的),`p.exists()` 永远是 True。

> **教训**:**Mock 越深,测试覆盖越少**。E2E 测试 mock 的层应该尽量靠近 IO/network 边界(LLM API call、HTTP fetch),不该 mock 系统内部的 orchestration function。

---

## 3. 三个具体的测试基础设施缺口

### 缺口 A:E2E 测试没有 path-existence + path-locality invariant

每次 step 返回 `processed_files / qualified_files / output_files / *_files` 这种"文件路径列表"字段,都应该跑两条不变量检查:

```python
for p in result.processed_files:
    assert Path(p).exists(), f"Step exposed non-existent path {p}"
    assert Path(p).resolve().is_relative_to(vault_root), \
        f"Step exposed non-vault path {p} (tempdir leak?)"
```

这两条假设是**通用的**(不只 absorb / entity_extract),应该作为 step contract 的一部分**自动校验**,不靠每个 step 的测试单独写。

可以做成 `step_contracts.py` 里的 post-validation hook:某个字段名是 `*_files` / `*_paths` 时,自动跑这两条。

### 缺口 B:Pipeline 报告层对 "skipped" 和 "0 produced" 不区分

`pipeline-report-*.md` 里 `entity_extract ✅ 成功 | Mentions: 0` 看起来跟"正常完成但本轮无新内容"一样。**没有任何信号告诉用户"这步根本没读到输入"**。三天里每次 incremental 都这样,都没人发现。

修复(本 PR 已做):

```python
if result.get("success") and result.get("skipped"):
    status = "⚠ 跳过"
    detail = f"未执行 — {result.get('reason', '(no reason)')}"
```

但更根本的是:**不能让 success=True 同时没产出任何东西** —— 这要么是 bug 要么是真的 noop。step contract 应该要求 `(produced > 0) OR skipped=True OR (input_count == 0 AND explicitly_recorded)`。

### 缺口 C:没有"daily ledger drift"监控

`entity-extractions.jsonl` 上次写入是 2026-05-02。如果有一个 daily check 跑这种 query:

```sql
SELECT step, last_write_at, days_since
FROM step_ledgers
WHERE days_since > expected_max_days
```

或者更简单的 shell:

```bash
test "$(find $vault/60-Logs/entity-extractions.jsonl -mtime -2)" || alert "entity-extractions stale"
```

3 天里至少应该发一次告警。我们没有这种"sentinel"机制。OVP 的 `ovp-doctor` 可能可以扩成"step ledger freshness check"。

---

## 4. 为什么测试基础设施会出现这些缺口

诚实诊断,几个根因:

**1. PATCH-1 的成功掩盖了它的局限**  
PATCH-1 引入 `test_absorb_to_entity_extract_chain` 防御 silent-fallback,然后那个 bug 真的没再出现 —— 团队把这条测试当成了"覆盖了一类 bug"。但它**只覆盖了 typed contract 这一条假设**(absorb 必须暴露 processed_files),没覆盖"processed_files 里的内容必须是 valid vault path"。

**教训**:写测试时要清楚自己**到底在 assert 什么**。`assert str(deep_dive) in absorb_result.processed_files` 是在 assert "absorb 暴露了一个非空 processed_files",**不是**在 assert "processed_files 里的路径有效"。这两件事要分别写测试。

**2. Staging tempdir 的引入没有触发"测试需要更新"的信号**  
PR #99(2026-05-01)加了 staging。这个改动**改变了 absorb 内部的 path semantics**(item["file"] 从 vault path 变成 staging path)—— 但因为 mock 在更内层的 `run_absorb_workflow` 上,e2e 测试无感。开发时跑 `pytest`,绿。merge。

**教训**:**改动如果改变了"什么从函数出来"的语义,e2e 测试应该感知到**。如果不感知,要么是 mock 太深,要么是 invariant 没 assert,要么两者都有。PR 模板/CI checklist 里应该加一条"如果你改动了 step 的 return shape / file-path semantics,请确认 e2e 测试还在 assert 真实物**ical** 路径"。

**3. Pipeline 报告没有"该响而没响"机制**  
3 天里每次 `Mentions: 0` 都是绿色。报告显示 `entity_extract ✅ 成功`。**没有人 review 这个报告**(也不该让人天天 review)。但也没有"3 天 0 mentions 应该报警"。

**教训**:成功的 metric 应该带"预期范围"。每天 145 evergreens 产出却 0 mentions 是**预期外**,不是预期内。需要 anomaly detection 而不是 status check。

---

## 5. 提出的测试基础设施改进

按 P0/P1/P2 排:

### P0 — 已通过本 PR 修复

- [x] 添加 `test_absorb_processed_files_are_vault_paths_not_staging`,exercise 真实 staging code path,assert vault-relative + exists
- [x] Pipeline 报告层区分 ⚠ skipped 和 ✅ 成功
- [x] `_count_output_files` 把 `skipped + reason` 透传给报告

### P1 — 应在 BL-058 之前/同步做

- [ ] **Step contract 通用 path 校验**:任何字段名匹配 `*_files / *_paths` 都跑 `exists() + is_relative_to(vault_root)`。放进 `step_contracts.py:_validate()` 里,自动应用。
- [ ] **E2E 测试加 staging 真实路径**:不再用 `patch("run_absorb_workflow")`,改 `patch("LiteLLMClient.call")`(LLM API 边界),让 staging / IO / orchestration 都跑真实代码。
- [ ] **Daily ledger freshness check**:`ovp-doctor` 加 `--check-ledgers`,扫 `entity-extractions.jsonl / item-ledgers/*.jsonl / pipeline.jsonl` 的最新写入时间,超过 N 天报警。

### P2 — 长期

- [ ] **Anomaly detection on pipeline metrics**:每个 step 的 `produced` 数应该有"过去 7 天 baseline",当前值 < 50% baseline 触发 ⚠ 显示。
- [ ] **Mock policy doc**:写一份 `tests/CLAUDE.md` 规定 mock 边界 —— "只在 IO 边界 mock(LLM API、HTTP、subprocess、network),不 mock 系统内部 orchestration function"。
- [ ] **CI 跑一次 `--incremental --dry-run` 在 fixture vault 上**:让 CI 真的跑一遍主链路(不调真实 LLM,fixture vault 包含 deterministic LLM stub),确保所有 path 不变量在真实 dispatch 下都成立。

---

## 6. 写在前面的一条 invariant

```
Any pipeline step that returns a list of file paths MUST guarantee:
  1. Every path exists() at the moment the step returns
  2. Every path resolves under vault_dir
  3. No path is under a tempdir/staging dir / scratch dir
```

这条假设在 step_contracts 里**强制**,违反就 raise StepContractError。这样未来任何 staging-like 改动会立刻在测试里抛错,而不是静默腐烂。

---

## 7. 这次 incident 的 timeline

```
2026-05-01 15:07  PR #99 (scope incremental quality checks) 落地 — 引入 staging tempdir
                  Bug 进入 main。
2026-05-01 ~16:30  之后第一次 incremental 跑,entity_extract 静默 0 mentions。
                  没人察觉(报告只显示 ✅ 成功)。
2026-05-02 16:25  最后一次成功的 entity_extract 写入 entity-extractions.jsonl。
                  之后 3 天再没有写入(每次跑都被 staging path 漏掉)。
2026-05-04 22:57  本次 incremental 跑 19 篇 / 145 evergreens / 0 mentions。
                  Pipeline 报告依然显示 ✅ 成功。
2026-05-05 01:00  用户 review 报告,问 "为什么 entity_extract Mentions: 0"。
                  Bug 被注意到。
2026-05-05 01:30  根因定位、修复、回归测试、复盘。
2026-05-05 02:00  Backfill 跑起来,补回 20 篇错过的 deep-dives 的 entity 抽取。
```

**3 天 + 14 篇 deep-dive 的 entity layer 数据缺失,直到用户人工 review 才发现**。这是测试基础设施需要补强的最直接证据。

---

## 8. 这次 incident 跟 BL-058 的关系

BL-058(`extract_candidates`/`canonical_write` 拆分)已经在路上。它会重写整个 absorb 链路。但**就算 BL-058 落地,如果测试基础设施还是这种 mock 太深、不 assert path 不变量、不监控 ledger 新鲜度的状态,新的 staging-like bug 仍然会一次次地溜过去**。

所以本次 retro 的 P1 项目应该**跟 BL-058 同步做**,不是 BL-058 之后再做 —— 否则我们只是把同样的 bug 类换个名字重新写一遍。
