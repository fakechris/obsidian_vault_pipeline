# OVP2

Rust workspace (`crates/`) + React portal (`console-ui/`) + Tauri desktop shell
(`apps/desktop/`). Pipeline: capture → intake → reader packs → crystal claims →
portal. The vault is data, not code — it lives outside this repo.

Only gotchas below. Anything derivable by reading the code is deliberately absent.

---

## 定时任务跑的是 app sidecar,不是 `target/release/ovp2`

Desktop 的 scheduler exec `resolve_ovp2_bin()`(`apps/desktop/src-tauri/src/lib.rs:309`):
`OVP2_BIN` → app 内 sidecar → dev fallback。**`cargo build --release` 到不了定时任务。**

```bash
INSTALL_APP=/Applications/OVP2.app scripts/build-desktop-sidecar.sh
```

不用重启 app——每次 tick 都重新 spawn 进程。验收方式是比对 sidecar 的 mtime/哈希与目标提交,
不是看 `cargo build` 成功。dev fallback 尤其危险:它会命中一个可能没编 live features 的
`target/release/ovp2`,然后把 `--features web-fetch-live` 这种构建期错误抛给 GUI 用户。

## cadence 语法比二进制新 = 整个 tick 停摆

registry 在**加载期**校验 cadence,一个不认识的语法会让 `schedule list` / `tick` 直接
非零退出——**注册表里的每个 job 都不跑**,不只是那一个。而 desktop 把子进程的 stderr 只写进
`eprintln`(从 Finder 启动时被吞掉),所以在 GUI 里表现为"什么都没发生"。

**改 `.ovp/schedule.json` 的 cadence 前,必须先装上认得新语法的 sidecar。**
顺序反了 = 定时任务全停。(`plan_tick` 里还有一层 `skipped_not_due` 的防御分支,
但那条路径正常走不到,别指望它。)

## `is_due` 只看时间,不看上次成败

`crates/ovp-scheduler/src/lib.rs:175` 只比较 `last_run` 与最近调度点。所以**失败或被 kill 的 job
不会重试**,要空等一整个 cadence。一次 09:05 的失败会让 daily 静默停到次日 09:00。
UI 上必须显式说明这一点,别让 "Next schedule window" 读起来像会自愈。

## daily 的吞吐天花板是 `--max-sources × cadence`

默认 `--max-sources 10`(`crates/ovp-daily/src/lib.rs:231`,`0`=无限)。进度条的分母也按它算,
所以 "10/10" 是"本次打算处理的都做完了",不是"积压清空了"——差额在心跳的 `capped` / `queued_after`。
`providers.toml` 的 `[budget] daily_token_budget` **不能当限流器**:它是 soft 的,只由
`ovp2 usage` 打印一行,不拦截任何调用。

## `run_id` 是按日期生成的

`format!("daily-{date}")`(`crates/ovp-cli/src/main.rs:1556`)。同一天多次运行**共用一个 run_id**,
`last-run.json` 会被覆盖。加高频 cadence 时要考虑这点。

## console-ui:默认没有 node_modules,测试跑在 node env

`npm ci` 后才有依赖。vitest 配的是 `environment: 'node'`——**没有 DOM,不能渲染组件**。
所以组件里承载判断的逻辑必须抽成 `src/lib/derive.ts` 里的纯函数才测得了
(例:`shouldClearRetry`)。i18n 模板的测试方式是自己 mirror `{name}` 插值,见
`RunBanner.progress.test.ts`。

## 落地流程

feature 分支 → PR → coderabbitai + gemini 机器人评审(只在 PR 上,没有 push CI)→ 修 → 合。
`codex` 是推送前的本地闸门(P1 = fail)。**不要 `git add -A`**——这个仓库经常有多个 agent
同时在工作区里改东西,`-A` 会把别人的半成品一起提交。`IMPLEMENTATION_PLAN.md` 永不提交。
