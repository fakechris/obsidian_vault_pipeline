# OVP2 Desktop GUI (Tauri) + DMG release — plan

> Reference: `~/source/lumen-asr` (`apps/desktop/` Tauri 2 shell + `.github/workflows/release-macos.yml`).
> Operator decisions (2026-07-13): **ad-hoc signing** (like lumen-asr; manual first-run approval); **in-process `ovp-server` + `ovp2` sidecar** integration (reuse the whole portal, no frontend rewrite).

## Why this is cheap for us

We already have every piece a desktop app needs, so the Tauri shell is thin glue, NOT a rewrite:

| Piece | Have it | Role in the GUI |
|---|---|---|
| React SPA | `console-ui/` (Vite) | The whole UI, unchanged |
| HTTP server | `ovp-server` (`tiny_http`, serves `console-ui/dist` + `/api/*`) | Runs IN-PROCESS inside the Tauri app; the window points at it |
| Scheduler engine | `ovp-scheduler` crate (cadence/registry/`plan_tick`/`JobRunner`) | Runs on an in-app timer; **replaces launchd/systemd** |
| CLI | `ovp2` (cargo-dist) | Bundled as a Tauri **sidecar**; the in-app scheduler exec's it for `daily`/`crystallize` (exactly what `ShellRunner` already does) |

**This is the whole reason the launchd/systemd migration layer was a throwaway bridge** ([[feedback-gate-over-hardening]]): the desktop app is the real scheduler host.

## Architecture

```
┌─ OVP2.app (Tauri) ─────────────────────────────────────────────┐
│  Rust backend (apps/desktop/src-tauri, ~200 lines glue):        │
│   • resolve vault (config, or first-run folder pick)            │
│   • start ovp-server IN-PROCESS on 127.0.0.1:<port> (bg thread) │
│   • open main WebviewWindow → http://127.0.0.1:<port>/          │
│   • scheduler tick loop (timer) → plan_tick → ShellRunner       │
│         exec's the bundled `ovp2` sidecar for daily/crystallize │
│   • native commands: pick_vault (dialog), open_in_finder        │
│                                                                 │
│  Webview → the EXISTING console-ui portal (same-origin API) ────┤
│  Sidecar: `ovp2-<target-triple>` (the CLI, per-arch)            │
└────────────────────────────────────────────────────────────────┘
```

- **Window → in-process server URL** (not `frontendDist`): the backend starts `ovp-server` on a loopback port and builds the main window pointing at `http://127.0.0.1:<port>/` in `setup()`. `console-ui` stays 100% unchanged and same-origin with `/api/*` (no CORS, no `invoke` rewrite). `frontendDist` is a tiny splash (`"starting…"`) shown until the server is up.
- **Scheduler with NO OS units**: the Tauri backend owns the tick. On an interval it runs `plan_tick(registry, state, now)` and, for each due job, exec's the sidecar with the registry's argv (`{vault}` resolved) via a `ShellRunner` whose `ovp2_path` = the resolved sidecar path. Same engine, same registry file, same `.ovp/scheduler.lock` + `.ovp/run.lock` guards. `launchd`/`systemd` are never touched by the app.
- **Schedule control over HTTP**: add `GET/POST /api/schedule` to `ovp-server` (list jobs, enable/disable, run-now, tick status) backed by `ovp-scheduler`; a "计划" panel lands in the System page. No new `invoke` surface for scheduling.
- **Native-only bits** (the few things HTTP can't do): first-run **vault folder picker** (`tauri-plugin-dialog`), **open vault / logs in Finder** (`tauri-plugin-opener`), app menu + optional tray, dock icon.

## Layout

```
apps/desktop/
  src-tauri/
    Cargo.toml            # tauri 2, tauri-plugin-{dialog,opener}; deps: ovp-server, ovp-scheduler, ovp-domain
    tauri.conf.json       # identifier com.ovp2.desktop; bundle.targets ["dmg"]; externalBin ["binaries/ovp2"]; macOS.signingIdentity "-"
    build.rs
    src/lib.rs            # run(): start server + window + scheduler loop; #[command] pick_vault/open_in_finder
    binaries/             # ovp2-<triple> sidecars (populated by CI; gitignored)
  src/                    # tiny splash frontend (index.html + main.ts) — the real UI is served by ovp-server
  package.json           # @tauri-apps/cli 2, scripts: dev/build/tauri
  vite.config.ts
```
`console-ui` is NOT moved — `ovp-server` already embeds/serves its `dist`. New workspace member `apps/desktop/src-tauri` (bin crate `ovp2-desktop`), added to root `Cargo.toml` members.

## Stages

### G0 — Spike: reuse proof (½–1 day)
Tauri shell that starts `ovp-server` in-process and opens a window to it, showing the existing portal against a hardcoded vault. No scheduler, no onboarding.
**Done when:** `cargo tauri dev` opens a native window rendering the real portal + `/api/*` working.

### G1 — In-app scheduler (1 day)
Tick loop (tokio interval, e.g. every 60s) → `plan_tick` → `ShellRunner` exec'ing the `ovp2` sidecar for `daily`/`crystallize`; `/api/schedule` endpoints in `ovp-server`; a Schedule panel in console-ui's System page (job list, on/off, run-now, last run / next due). **No launchd/systemd.**
**Done when:** toggling a job in the UI persists to `.ovp/schedule.json`; a due job runs on the timer and its state updates; run-now works.

### G2 — Onboarding + native (1 day)
First-run: vault folder picker → persist `~/Library/Application Support/com.ovp2.desktop/config.json` (vault path + env-file pointer). "No vault yet" state. Open vault/logs in Finder. App menu, optional tray, icon/branding.
**Done when:** fresh launch (no config) walks to a working vault; relaunch remembers it.

### G3 — DMG release pipeline (½ day)
`.github/workflows/release-desktop.yml`, mirroring lumen-asr:
```yaml
on: { push: { tags: ["v*"] } }
jobs:
  build:
    strategy: { matrix: { include: [
      { runner: macos-15,       target: aarch64-apple-darwin, arch: arm64 },
      { runner: macos-15-intel, target: x86_64-apple-darwin,  arch: x64 } ] } }
    steps:
      - checkout
      - node 22 + rust stable (target ${{ matrix.target }})
      - run scripts/release/set-desktop-version.mjs "$GITHUB_REF_NAME"   # stamp tauri.conf.json
      - cargo build --release -p ovp-cli --target ${{ matrix.target }}   # the sidecar
      - cp target/${{ matrix.target }}/release/ovp2 apps/desktop/src-tauri/binaries/ovp2-${{ matrix.target }}
      - (cd console-ui && npm ci && npm run build)                       # embedded by ovp-server
      - (cd apps/desktop && npm ci && npm run tauri -- build --target ${{ matrix.target }} --bundles dmg)  # ad-hoc signed
      - rename → OVP2-${TAG}-${arch}.dmg, sha256 >> SHA256SUMS.txt
  release:
    - gh release create/upload the two DMGs + SHA256SUMS.txt (draft → publish), --generate-notes + first-run-approval note
```
- **Ad-hoc signing** (`signingIdentity: "-"`); release notes carry the "首次运行右键放行" caveat (Gatekeeper), same as lumen-asr.
- **Coexistence with the CLI release** (`release.yml`, cargo-dist, also on `v*`): both fire on the same tag and target the SAME GitHub Release — CLI installers + brew from cargo-dist, `.dmg`s appended by this workflow (`gh release upload --clobber`, tolerant of the release existing). Coordination note: desktop job `needs`-waits or uses `--clobber`; verify no race on release creation (decide during G3).

### G4 — Polish (optional, later)
Auto-update (`tauri-plugin-updater` + `latest.json`), Windows/Linux targets, Developer-ID notarization (if we later ship externally — swap `signingIdentity` + add Apple secrets; the workflow gains a notarize step).

## Open coordination points (resolve in-stage, not blocking)
1. Loopback port: fixed (e.g. 8788) with fallback-scan, window built programmatically with the resolved URL.
2. Two `v*` workflows on one release — confirm cargo-dist creates the release first or make the desktop job idempotent (`--clobber`).
3. Sidecar size: the `ovp2` binary bundles the ONNX embed stack (`embed` feature) — ~large; decide whether the desktop sidecar ships with or without `embed` (themes need it; could lazy-download the model instead).
