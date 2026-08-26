# OVP2

Rust workspace (`crates/`) + React portal (`console-ui/`) + Tauri desktop shell
(`apps/desktop/`). Pipeline: capture → intake → reader packs → crystal claims →
portal. The vault is data, not code — it lives outside this repo.

Only gotchas below. Anything derivable by reading the code is deliberately absent.

---

# 改完之后要做什么

**先查这张表,再去测。** 装了 OVP2.app 的机器上跑着四份互相独立的产物,改了 A 却
只更新了 B,现象是"改动完全没生效",而代码、测试、构建全是绿的——这是本仓库最贵的
时间浪费来源。

| 你改了什么 | 要做的动作 | 重启 app? |
|---|---|---|
| `crates/*`(**除** `ovp-server`)<br>daily / intake / scheduler / reader / CLI | `INSTALL_APP=/Applications/OVP2.app scripts/build-desktop-sidecar.sh` | 否 |
| `crates/ovp-server` | 重建整个 desktop app(见下) | **是** |
| `apps/desktop/src-tauri/*`<br>scheduler 间隔 / boot / 窗口 / 菜单 | 重建整个 desktop app | **是** |
| `console-ui/*` | `scripts/deploy-portal.sh <vault>`(见下) | 否,硬刷新页面 |
| `.ovp/schedule.json` | 无——下次 tick 自动读 | 否 |
| `.ovp/providers.toml` | 无——每次调用重读 | 否 |

Windows 上同一张表,换脚本:`scripts/build-desktop-sidecar.ps1 -InstallApp
"$env:LOCALAPPDATA\OVP2 Desktop"` 和 `scripts/deploy-portal.ps1 <vault>`。
sidecar 叫 `ovp2.exe`,和**壳 `ovp2-desktop.exe`** 并排放在安装目录里(不是
`Contents/MacOS`)。**壳不叫 `OVP2.exe`**:tauri.conf.json 没设 `mainBinaryName`,
壳保留 Cargo bin 名;而且 Windows 文件名大小写不敏感,`OVP2.exe` 和 `ovp2.exe`
根本无法共存于同一目录。权威是 `scripts/build-desktop-sidecar.ps1` 里的检查。

## Windows 只有 CI 能验

没人有 Windows 机器。`cfg(windows)` 分支、NSIS 打包、MSVC 链接**在 mac 上一个都验不到**,
所以 `.github/workflows/ci-windows.yml` 是这个项目唯一的 Windows 环境——红了就当本地
编译失败处理,别当成"CI 抽风"。本地能做的最强预检是交叉编译:

```bash
rustup target add x86_64-pc-windows-gnu && brew install mingw-w64
cargo check --workspace --exclude ovp2-desktop --all-targets --target x86_64-pc-windows-gnu
```

`cfg(windows)` 在 gnu 和 msvc 下一样成立,所以这能抓住绝大多数编译错;抓不住的是链接、
Tauri 打包和一切运行期行为。已实现范围、**故意保留的限制**、以及还没在真机上验过的清单
见 `docs/windows-port.md`——动 Windows 相关代码前先读那份,别重复推导。

## 为什么 `ovp-server` 不在 sidecar 那一行

`apps/desktop/src-tauri/Cargo.toml` **直接依赖 `ovp-server`**,它被编译进 `ovp2-desktop`
并在进程内运行。改了 server 去重建 sidecar 是**完全无效**的——sidecar 是 CLI(`ovp2 serve`
才用它那份),和门户 API 走的不是同一份代码。

## 定时任务跑的是 app sidecar,不是 `target/release/ovp2`

Desktop 的 scheduler exec `resolve_ovp2_bin()`(`apps/desktop/src-tauri/src/lib.rs:309`):
`OVP2_BIN` → app 内 sidecar → dev fallback。**`cargo build --release` 到不了定时任务。**

不用重启 app——每次 tick 都重新 spawn 进程,读的是磁盘上当前那个文件。验收方式是比对
sidecar 的 mtime/哈希与目标提交,**不是看 `cargo build` 成功**。dev fallback 尤其危险:
它会命中一个可能没编 live features 的 `target/release/ovp2`,然后把
`--features web-fetch-live` 这种构建期错误抛给 GUI 用户。

## 前端有两份,vault 那份优先

两份都能独立工作,`read_app_file`(`ovp-server/src/lib.rs:4557`)**逐文件**决定用哪份:

| 位置 | 角色 | 什么时候更新 |
|---|---|---|
| `<vault>/.ovp/console/app/` | **优先**。存在就赢 | 手动部署(`deploy-portal.sh`) |
| app 包 `Contents/Resources/console-ui/dist` | 兜底(经 `--viz-dir`) | 重新打包 app 时 |

最终用户的 vault 里**没有**第一份,门户就吃 app 包那份——这是正常路径。第一份是给
"不重新打包就要看到前端改动"用的,也就是开发迭代。

这个优先级是 `2987cebb` 有意定的:已部署副本让 vault 自成一体(`ovp2 serve` 时代的需求),
`--viz-dir` 让 dev checkout 能服务任意 vault。**两者是不同场景,不是替代关系。**

坑在于开发机上两份都在:**重新打包 desktop app 不会改变门户显示的东西。** vault 那份
`index.html` 赢,它引用旧的 asset 哈希,那些 asset 也从同一份旧副本解析出来——整页都是旧
构建,而构建日志全绿。不想要这个优先级就删掉 vault 那份,门户立刻回到 app 包那份。

所以改完前端要跑:

```bash
scripts/deploy-portal.sh <vault-root>
```

它构建、部署、然后**去跑着的门户上取实际 asset 哈希做比对**。验收看的是这个比对,
**不是 `npm run build` 的退出码**——后者正是让这个错误活过一整轮调试的东西。

完整背景和手工步骤见 `docs/operator-runbook.md` 的 "Portal SPA deploy"。

## 重建整个 desktop app

```bash
npm --prefix apps/desktop run tauri build
```

产物要装回 `/Applications/OVP2.app` 才算数,并且**必须退出再重开**——`ovp2-desktop` 是
常驻进程,替换磁盘上的文件不影响已经跑着的那个。

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
