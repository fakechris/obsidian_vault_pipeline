# Windows port — status, deliberate limits, verification

Updated: 2026-08-05

Everything in "Implemented" compiles for `x86_64-pc-windows-msvc` and passes the
offline test gauntlet. **Nothing in it has been run on a real Windows machine.**
That distinction is the whole point of this document: below the fold is the list
of things only a Windows box can answer, and the order to answer them in.

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

- **No env-file sourcing.** There is no portable `. daily.env`. Windows uses
  `.ovp/providers.toml`, which supersedes `daily.env` on every platform anyway.
  A Windows vault that still has only `daily.env` gets a printed warning naming
  the file — it will not silently run live jobs unauthenticated.
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

## Verification checklist — run these on a real Windows 11 x64 box

CI covers 1–3. Everything from 4 down needs a human or an agent on a desktop.
Record failures **in this file** until they are fixed.

1. `cargo test --workspace --exclude ovp2-desktop`
2. `cargo clippy --workspace --exclude ovp2-desktop --all-targets -- -D warnings`
3. `npm ci && npm test && npm run build` in `console-ui`;
   `./scripts/build-desktop-sidecar.ps1`; `npm run tauri -- build --bundles nsis`
   in `apps/desktop`
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
    "reclaiming stale run lock", not refuse forever. This is `probe_pid`'s
    Windows path — the single least-verified piece of this port.
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
