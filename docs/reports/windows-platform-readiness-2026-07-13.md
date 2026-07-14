# OVP Next Windows 与跨平台就绪度调研

日期：2026-07-13

## 执行结论

OVP Next 的 Rust 分层、核心依赖和浏览器 Portal 具备良好的跨平台基础，但当前产品还不能称为 Windows-ready。Rust 能让大部分纯业务代码天然复用；它不会自动解决操作系统任务调度、shell、进程锁、文件名/路径语义、安装包和原生 CI。

当前状态可以概括为：

| 范围 | 结论 | 主要依据 |
|---|---|---|
| `ovp-core` / `ovp-domain` / run、query、lint、rag 等业务层 | 高可移植性，但尚未在 Windows 验证 | 主要使用 `std::path`、serde、chrono、reqwest/rustls 等跨平台接口 |
| 日常 CLI 主流程 | 有条件可移植 | 依赖的路径记录、锁恢复、CRLF 和文件名规则存在 Windows 缺口 |
| `ovp2 schedule` | 明确不支持 Windows | 只实现 launchd/systemd，并硬编码 `/bin/sh` |
| 浏览器 Portal | 前端高度可移植，后端继承上述缺口 | React/Vite 和 loopback HTTP 本身不依赖桌面系统 |
| TUI | 尚未实现 | workspace 无 Ratatui/Crossterm 适配层 |
| Tauri 桌面 GUI | 有未提交方案与初始脚手架，尚非可运行产品 | 当前计划先做 macOS DMG，把 Windows/Linux 放在后续阶段 |
| 发布与持续集成 | 未支持 Windows | cargo-dist 只构建 Apple Silicon macOS 和 x86_64 Linux，且没有 Windows PR 矩阵 |

因此，Windows 移植不是 Rust 重写项目，而是一个需要明确立项的“平台边界适配 + 原生验证 + 分发”里程碑。

## 已验证的基础

在审计开始时的已跟踪 Rust workspace baseline 上执行并通过（其后工作区出现并行中的未提交 Desktop GUI 变更，本报告没有用这些中间态重新验收）：

- `cargo metadata --no-deps --format-version 1`
- `cargo test --workspace`
- `cargo clippy --workspace --all-targets -- -D warnings`
- `bash scripts/check_architecture.sh`
- `cargo check -p ovp-cli --all-features`
- `console-ui`: `npm test`（34 tests）和 `npm run build`

`cargo tree --target x86_64-pc-windows-msvc` 可以完成依赖解析；release 中的 `ort-sys` 版本也列有 Windows x86_64/aarch64 ONNX Runtime 产物。这说明依赖选择没有显而易见的 Windows 编译死路，但它不能代替 Windows runner 上的真实编译、链接、启动和首次模型下载测试。

Rust 官方把 `x86_64-pc-windows-msvc` 和 `aarch64-pc-windows-msvc` 列为 Tier 1（完整标准库）。语言和标准库层面的目标是受支持的，产品层面的差距来自仓库自己的 OS 边界。

## 已确认的 Windows 阻塞点

### 1. 调度器是 Unix 专用实现

`crates/ovp-cli/src/commands/schedule.rs` 的平台分支只接受 macOS launchd 和 Linux systemd；其他系统直接返回不支持。`crates/ovp-cli/src/commands/scheduler.rs` 通过 `/bin/sh -c` 运行 job，`crates/ovp-scheduler/src/lib.rs` 还生成 shell source 和 `$(date +%F)`。

建议把调度执行协议改成结构化数据：`program + argv + env + date`，由 `std::process::Command` 直接执行，不生成 shell 字符串。Windows CLI 后台运行用 Task Scheduler 适配器（`schtasks.exe`、XML 或 Windows API）。

现有桌面方案用“应用内 tick + sidecar”可以绕开 launchd/systemd，但不能绕开 `/bin/sh` 的 `ShellRunner`。此外，应用关闭后 tick 也停止；需要在产品语义上选择：

- 只承诺“桌面应用运行时调度”；或
- 托盘常驻并开机启动；或
- 仍用 Windows Task Scheduler，保证 GUI 关闭后任务继续运行。

第三种和 CLI 的行为最一致。

### 2. 崩溃后的锁无法在 Windows 自动恢复

`crates/ovp-intake/src/vaultops.rs` 的 stale-lock 判断在 Unix 上执行 `kill -0`，`cfg(not(unix))` 永远返回 false。Windows 上一旦进程崩溃遗留 `.ovp/run.lock` 或 `.ovp/scheduler.lock`，后续运行只能人工删锁。

建议改用操作系统持有的 advisory/exclusive file lock；进程退出或崩溃时由内核释放。PID 文件只保留为诊断信息，不再承担互斥正确性。

### 3. 持久化路径格式在 Windows 会自相矛盾

`rel_to` 直接对 `Path` 调用 `to_string_lossy()`，Windows 会写入反斜杠；而 `ovp-server::is_plain_relative` 明确拒绝反斜杠，生命周期回退也按 `/` 分割。这会导致 Windows 生成的 ledger/index 路径无法被 Portal 的 source-details 或部分 lifecycle 逻辑读取。

建议定义唯一数据契约：所有 ledger、index、manifest 和 API 中的 vault-relative path 始终使用 `/`；只有在文件系统边界才转换为当前平台的 `PathBuf`。读取端应兼容并归一化历史 `\` 数据。

### 4. Windows 文件名、大小写和长路径规则未覆盖

当前 `sanitize_filename` 会替换九个常见非法字符，但没有覆盖：

- ASCII 控制字符；
- DOS 设备名，如 `CON`、`PRN`、`AUX`、`NUL`、`COM1`、`LPT1` 等（即使带扩展名也保留）；
- 末尾的空格或句点；
- 默认大小写不敏感文件系统上的冲突；
- 整个 vault 的路径长度预算。

建议扩展跨平台文件名策略，为 canonical slug 和所有生成路径增加 case-fold collision 检查，并增加 `ovp2 doctor --windows-compat` 一类只读预检，扫描现有 vault 中不可迁移的名称和过长路径。已有文件的改名应生成迁移清单，不能静默执行。

Windows 长路径不仅需要 OS 设置，还需要应用 manifest 声明 `longPathAware`。即使启用，也建议为 Obsidian/同步盘互操作保留保守路径预算。

### 5. Windows 生成的 CRLF frontmatter 可能解析失败

`markdown_inbox::split_frontmatter` 只识别 `---\n` 和 `\n---\n`。由 Windows 编辑器或剪藏工具生成的 `\r\n` 文件可能被当成无 frontmatter。

建议同时接受 LF/CRLF，并为 BOM + CRLF、无末尾换行增加 fixtures。

### 6. 原子替换和文件占用语义需要实机测试

scheduler、daily heartbeat 和 theme 写入存在“写临时文件后 `std::fs::rename` 覆盖目标”的实现。Rust 的 `rename` 在不同平台调用不同 OS 能力；Windows 上目标文件存在、被 Obsidian/杀毒软件/同步程序占用时的行为必须验证。

建议集中成一个跨平台 atomic-write 模块，并在 Windows 上覆盖：目标已存在、目标被打开、崩溃恢复、NTFS 和 OneDrive/同步盘人工验收。

## 发布与 CI 缺口

`dist-workspace.toml` 当前只有：

- `aarch64-apple-darwin`
- `x86_64-unknown-linux-gnu`

安装器只有 shell 和 Homebrew；没有 PowerShell、MSI/NSIS，也没有 Windows PR/push runner。cargo-dist 本身支持 Windows MSVC target、PowerShell installer 和 MSI，所以这是配置与验证工作，不是换分发系统。

Windows 成为“受支持平台”的最低发布门槛应包括：

1. `windows-latest` 原生 CI：workspace tests、clippy、all-features build；
2. fixture smoke：intake、daily/replay、index、serve 和 scheduler engine；
3. `x86_64-pc-windows-msvc` release artifact；
4. PowerShell installer，桌面应用再选 NSIS/MSI；
5. `ovp2 --version`、Portal 启动、ONNX 首次下载/缓存的安装后测试；
6. 对外发布前准备 Authenticode 签名，否则 SmartScreen 体验会很差。

不建议以 macOS 交叉编译 MSVC target 作为验收标准；Windows runner 才是文件系统、进程和安装行为的权威环境。

## GUI 与 TUI 路线

### 最省成本：先把现有 Portal 作为 Windows GUI

现有 React SPA + `ovp-server` 已经是一套 GUI。先让 `ovp2 serve` 和核心工作流通过 Windows 验收，就能在浏览器中使用，不需要为 Windows 重写界面。

### 桌面 GUI：Tauri 是当前代码库最合适的壳

未提交的 `docs/design/desktop-gui-plan.md` 选择 Tauri 2，复用 Portal 并在进程内启动 `ovp-server`，这是比 egui 重写前端更低风险的路线。Windows 版本还需补：

- 用 Tauri `app_data_dir` 等平台 API，不硬编码 `~/Library/Application Support`；
- `.exe` sidecar 命名/查找和结构化 JobRunner；
- WebView2 与 MSVC 构建前置条件；
- NSIS/MSI、应用图标、版本信息和 Authenticode；
- 明确后台调度在应用关闭后的行为。

如果目标是“尽快有 Windows GUI”，顺序应是 Portal → Tauri 壳，而不是另写原生 UI。

### TUI：适合作为独立适配层，不是 Windows 移植前置条件

可新增薄的 `ovp-tui` crate，使用 Ratatui + Crossterm，直接依赖 `ovp-run`、index/read model 和 scheduler API，不通过 shell 调用 `ovp2`。Crossterm 后端覆盖 Windows、macOS、Linux。

Windows TUI 还需验证：raw-mode/异常退出恢复、Ctrl-C、窗口缩放、PowerShell/Windows Terminal、CJK/emoji 宽度、日志与 TUI 绘制分流。TUI 可以复用核心逻辑，但仍是一个新产品界面，不能把它计入当前跨平台能力。

## 推荐实施顺序

### P0：先把 Windows 变成可测目标

1. 加 `windows-latest` CI，先允许失败，记录真实编译/测试清单；
2. 修复 CRLF、portable relative path、Windows 文件名和大小写冲突；
3. 换跨平台进程锁；
4. 抽出并测试统一 atomic-write；
5. 增加 Windows vault 兼容预检。

### P1：补齐运行与发布

1. 把 JobRunner 从 shell 字符串改成结构化 `Command`；
2. 增加 Windows Task Scheduler adapter；
3. cargo-dist 增加 Windows x64 + PowerShell installer；
4. 在原生 Windows 上跑 all-features、Portal、ONNX 和安装后 smoke。

### P2：再选一个终端产品形态

- 偏“马上可用”：直接支持浏览器 Portal；
- 偏终端用户：做 `ovp-tui`；
- 偏桌面分发：完成 Tauri 壳和 Windows installer。

不建议同时首发 TUI 和 Tauri。先共享稳定的 Rust service/facade API，再让两个 UI 都成为薄适配层，避免 UI 逻辑进入 `ovp-core` 或 CLI subprocess 变成架构接口。

## 参考资料（官方/一手）

- Rust Windows MSVC 平台支持：<https://doc.rust-lang.org/stable/rustc/platform-support/windows-msvc.html>
- Microsoft Windows 文件命名规则：<https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file>
- Microsoft Windows 长路径：<https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation>
- Microsoft `schtasks /create`：<https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/schtasks-create>
- Rust `std::fs::rename`：<https://doc.rust-lang.org/std/fs/fn.rename.html>
- cargo-dist 配置参考：<https://axodotdev.github.io/cargo-dist/book/reference/config.html>
- cargo-dist MSI：<https://axodotdev.github.io/cargo-dist/book/installers/msi.html>
- Tauri Windows 前置条件：<https://v2.tauri.app/start/prerequisites/>
- Tauri Windows installer：<https://v2.tauri.app/distribute/windows-installer/>
- Ratatui crate 文档：<https://docs.rs/ratatui/latest/ratatui/>
- eframe/egui 平台说明：<https://docs.rs/crate/eframe/latest>
- `ort` releases：<https://github.com/pykeio/ort/releases>
