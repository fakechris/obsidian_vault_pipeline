# Windows port — status, deliberate limits, verification

Updated: 2026-08-05

Everything in "Implemented" compiles for `x86_64-pc-windows-msvc` and passes the
offline test gauntlet **on a real Windows runner** — see "What CI has actually
proven" below. What has NOT happened is anyone installing the result and using
it. That distinction is the whole point of this document: below the fold is the
list of things only a Windows desktop can answer, and the order to answer them
in.

## What CI has actually proven

`ci-windows` is green as of `fb0cda78`
([run 31015660415](https://github.com/fakechris/obsidian_vault_pipeline/actions/runs/31015660415)).
That run executed, on `windows-latest`:

- the full offline test gauntlet — 1300+ tests, every `cfg(windows)` branch that
  has a test, including `probe_pid`, the schtasks flavor and the direct-spawn
  job runner;
- `cargo clippy` (advisory);
- an **MSVC link** of the live feature set (`rustls`/`ring`, `reqwest`, the
  enrich clients) — the thing no cross-compile can check;
- `npm ci && npm test && npm run build` in `console-ui`;
- `scripts/build-desktop-sidecar.ps1`, producing a 17 MB
  `ovp2-x86_64-pc-windows-msvc.exe`;
- `npm run tauri -- build --bundles nsis`, producing an 18.6 MB
  `OVP2_2.0.1_x64-setup.exe` — so `tauri.windows.conf.json`, the icon set, the
  NSIS languages and the `externalBin` sidecar naming are all confirmed to
  bundle. The installer is uploaded as a run artifact.

Three bugs only that runner could have found, all fixed in this PR:

1. **`ovp2.exe` overflowed its stack before `main` ran.** Windows bakes a 1 MB
   main-thread stack into the PE header; Unix gives 8. `ovp2`'s clap tree does
   not fit in 1 MB in a debug build. Every e2e test that spawned the CLI got an
   empty stdout and a non-zero exit — indistinguishable from a command that ran
   and did nothing. `crates/ovp-cli/build.rs` now reserves 16 MB.
   Unit tests all passed throughout, because libtest runs each test on a spawned
   thread sized independently of the main thread's.
2. **Vault-relative paths were being written with `\`.** `50-Inbox\01-Raw\a.md`
   in the ledgers and index, and `attachments\pic.jpg` as the target of rewritten
   Markdown image links. A Windows-written vault would have been unreadable by
   macOS/Linux and by the portal, surfacing as empty lookups rather than errors.
   The convention now has one owner: `ovp_domain::vault_rel`.
3. **`log_path` joined `".ovp/logs"` as one segment**, so the generated
   scheduler `.cmd` handed `mkdir` a mixed-separator path that cmd.exe rejects —
   behind a `2>nul` that made the failure invisible.

## Compatibility policy

- macOS and Linux remain first-class. Windows code is `cfg`-gated; no platform
  loses behavior to make another work.
- Where Windows genuinely cannot do what Unix does, the difference is
  **surfaced** (a printed warning, a different status line), never silently
  degraded. Silent degradation is this project's most expensive failure mode:
  a scheduler that stops firing looks identical to one with nothing to do.

## Implemented

**Core / CLI**

- `ovp-server/build.rs` stamps the build time in-process instead of shelling
  out to `date` (Windows has no `date` binary — every build stamped "unknown").
- `ovp_intake::probe_pid` is now the single process-liveness primitive for the
  whole workspace (run lock, daily heartbeat, source-work queue, agent
  transcript). Unix keeps `kill -0`; Windows uses
  `OpenProcess`/`GetExitCodeProcess`, distinguishing "no such PID" from "access
  denied" so an unreachable process is never mistaken for a dead one. Before
  this, all four sites answered "alive" on Windows unconditionally — a crashed
  run would have stranded `.ovp/run.lock` forever and reported itself as still
  running.
- `ovp2 schedule` grows a **Windows Task Scheduler** flavor: a task named
  `OVP2 Scheduler` firing every 10 minutes, running a generated
  `%LOCALAPPDATA%\OVP2\ovp2-scheduler-tick.cmd`. `install` / `uninstall` /
  `status` all work; `status` reads `schtasks /Query` for registration and last
  result. The wrapper `.cmd` carries the same `ovp2:` metadata comments the
  plist and systemd units do, so `status` recovers the vault path from it.
- The job runner spawns the pinned binary **directly** on Windows
  (`job_direct_command`) instead of routing an sh-quoted string through a shell
  that parses differently. `--date` is stamped from the local clock in Rust
  rather than `$(date +%F)`.
- Embedding model cache moves to `%LOCALAPPDATA%\ovp\models` (there is no
  `HOME` and no `~/.cache` convention on Windows).
- Every subprocess spawned from inside the GUI (`schedule init`/`tick`, the
  portal's "Run now", `publish`'s `git`) gets `CREATE_NO_WINDOW`, so a console
  does not flash over the operator's screen every 10 minutes.

**Desktop app**

- The sidecar is resolved as `ovp2.exe`. Getting this wrong is silent: the
  scheduler reports "no ovp2 binary found", the portal keeps working, and
  nothing looks broken until a day of ticks has gone missing.
- `tauri.windows.conf.json`: NSIS, **per-user** install (no UAC prompt), the
  WebView2 download bootstrapper, English + Simplified Chinese installer
  languages. `icons/icon.ico` added to the bundle icon set.

**Build / release**

- `scripts/build-desktop-sidecar.ps1` and `scripts/deploy-portal.ps1` mirror
  their `.sh` counterparts, including `deploy-portal`'s live verify step (the
  step that exists because "npm run build succeeded" is exactly the evidence
  that lets a stale-portal bug survive a debugging session).
- `.github/workflows/ci-windows.yml` — the only Windows environment this
  project has. Runs the offline gauntlet, clippy, the live-feature `cargo
  check`, the portal build, and an NSIS bundle, and uploads the installer for
  manual smoke-testing.
- `release-desktop.yml` builds a Windows x64 NSIS installer alongside the two
  DMGs, in ONE tag-triggered workflow with a single aggregating release job —
  two workflows would race on the same GitHub Release and clobber each other's
  `SHA256SUMS.txt`.
- `dist-workspace.toml` adds `x86_64-pc-windows-msvc` and the PowerShell
  installer to the CLI release.
- `.gitattributes` forces LF on checkout. Without it a Windows clone turns
  every fixture comparison into a failure that looks like a logic bug.

## Deliberate limits (not bugs)

- **No env-file sourcing.** macOS/Linux dispatch each job through `/bin/sh -c`
  and source `<vault>/.ovp/daily.env` when `.ovp/providers.toml` is absent;
  that path still works and is not going away. Windows has no shell to source
  with, so credentials there MUST be in `.ovp/providers.toml`. A Windows vault
  carrying only `daily.env` gets a printed warning naming the file — it will
  not silently run live jobs unauthenticated.
- **No permission hardening on written files.** `chmod 600` is a no-op on
  Windows; restricting the ACL would mean a real dependency for a file that
  already inherits a per-user profile ACL. Credentials belong in
  `providers.toml`; treat the vault directory's own permissions as the boundary.
- **Unsigned installer.** SmartScreen will warn on first run, and some
  enterprise policies will block outright. Code-signing (or a Microsoft Store
  submission) is the fix and is out of scope here.
- **x64 only.** ARM64 Windows is absent because nothing has ever been run
  there; an untested arch in a release is worse than an absent one.
- **No toast notifications.** `desktop_notify` is a no-op on Windows (macOS
  uses `osascript`, Linux `notify-send`). The portal's in-app surfaces still
  show queue results.
- **`is_due` still only looks at the clock**, not at whether the last run
  succeeded — same as every other platform. A failed 09:05 tick waits a full
  cadence. This is pre-existing and intentional; it is repeated here so nobody
  reads a stalled Windows scheduler as a Windows bug.
- **`.ovp/publish.toml` needs escaped backslashes.** TOML *basic* strings treat
  `\` as an escape introducer, so `out = "C:\Users\me\site"` is a parse error
  (`\U` is not a valid escape), not a path. Write either
  `out = 'C:\Users\me\site'` (a TOML *literal* string, no escapes) or
  `out = "C:\\Users\\me\\site"`. This is TOML behaving correctly, so it is a
  documented trap rather than something to fix — and the failure is loud:
  `POST /api/publish` returns 400 with a body naming the file and quoting the
  parse error. Two Unix-shaped assumptions in the same file: a `..` in `out` is
  rejected by the run guard, and a relative `out` resolves against the vault
  root.

## Verification checklist — run these on a real Windows 11 x64 box

Steps 1–3 are **done** — `ci-windows` runs them on every PR (see "What CI has
actually proven"). Everything from 4 down needs a human or an agent on a
desktop, and none of it has been done. Record failures **in this file** until
they are fixed.

1. ~~`cargo test --workspace --exclude ovp2-desktop`~~ — CI, green.
2. ~~`cargo clippy --workspace --exclude ovp2-desktop --all-targets`~~ — CI,
   advisory (the workspace carries pre-existing lint debt from newer clippy
   releases that this port neither caused nor should be blocked on).
3. ~~`npm ci && npm test && npm run build` in `console-ui`;
   `./scripts/build-desktop-sidecar.ps1`; `npm run tauri -- build --bundles
   nsis`~~ — CI, green; the installer is a run artifact.
4. **Install + first launch.** Run the NSIS `-setup.exe` on a machine with no
   WebView2 (a fresh Windows 10 image is the honest test) and confirm the
   bootstrapper installs it. Pick a vault when prompted; the portal must render.
5. **Sidecar resolution.** In the installed app directory, confirm `ovp2.exe`
   sits next to `OVP2.exe`. Then confirm the scheduler found it: the app logs
   `no ovp2 binary found` to stderr when it did not, and stderr is swallowed
   when launched from Explorer — so check for a `.ovp/schedule-state.json`
   that actually advances instead.
6. **A tick fires, with no console flash.** Wait out one 10-minute interval (or
   temporarily shorten the cadence). Nothing should blink on screen.
7. **`ovp2 schedule install`** from a terminal, then:
   `schtasks /Query /TN "OVP2 Scheduler" /FO LIST /V` — registered, next run
   set. Check `%LOCALAPPDATA%\OVP2\ovp2-scheduler-tick.cmd` renders correctly,
   then `ovp2 schedule status` and `ovp2 schedule uninstall` (the task must be
   GONE, not just the wrapper).
8. **A vault path with a space and a `%`** (e.g. `C:\Users\Some One\100% notes`)
   through the whole of 7. This is what `bat_quote` exists for and it is
   unit-tested, but the batch parser is the real judge.
9. **`ovp2 daily --vault-root <v> --client live`** end to end against a real
   vault, with credentials in `.ovp/providers.toml`. Confirm the run appears in
   the ledger and the portal.
10. **Stale-lock recovery.** Kill `ovp2.exe` mid-run (Task Manager → End Task),
    then start another run. It must reclaim `.ovp/run.lock` and print
    "reclaiming stale run lock", not refuse forever. `probe_pid`'s Windows path
    had NO test coverage at all until this PR — both stale-lock tests were
    `cfg(unix)`-gated on `Command::new("true")`. They are cross-platform now
    (`cmd /C exit 0`), plus a direct
    `probe_pid_answers_definitively_for_live_dead_and_zero`, so CI covers
    live / reaped / pid-0. What it still does NOT cover is a **hard kill** —
    `TerminateProcess` via Task Manager, and a machine where
    `PROCESS_QUERY_LIMITED_INFORMATION` is denied by policy (the `None` →
    "assume alive" branch). That needs the desktop.
11. **Portal deploy loop.** `pwsh scripts/deploy-portal.ps1 <vault>` and confirm
    the verify step actually matches the running app's asset hash.
12. **Long paths.** A vault nested past 260 characters. Windows long-path
    support depends on a machine policy; if this fails, the fix is a documented
    limit, not a code change.

## Known gaps to look at next

- Task Scheduler is configured through `schtasks` CLI flags, which cannot
  express "run as soon as possible after a missed start". A machine asleep at
  the daily slot skips that day. The XML task definition can express it; that
  is the upgrade path if operators hit it.
- The portal's System page still cannot show scheduler state on any platform.
- No Windows CI job builds with `embed`, though the CLI release does. If ort's
  Windows ONNX Runtime breaks, the first signal will be a failed release.
- **`ovp2-desktop.exe` gets no stack bump.** `crates/ovp-cli/build.rs` raises
  the reserve for `ovp2.exe` only. The GUI shell does not link the clap tree —
  it embeds `ovp-server` and its own Tauri `main` — so it has no known reason to
  need one, and adding an unverified link arg to the bundled binary was not
  worth risking the NSIS build for. If the installed app dies instantly on
  launch with nothing in the logs, this is the first thing to suspect.
- Two pre-existing copies of the vault-relative separator fix-up remain in
  `ovp-review/src/lib.rs` and `ovp-stores/src/vault_scan.rs`. They are correct,
  just not routed through `ovp_domain::vault_rel` yet; folding them in is a
  cleanup for another PR.
