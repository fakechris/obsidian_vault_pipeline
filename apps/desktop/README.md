# OVP2 Desktop

Tauri 2 app — thin Rust backend + the existing `console-ui` portal. Runs
`ovp-server` **in-process** on a loopback port and exec's the bundled `ovp2`
sidecar's `schedule tick` on an in-app timer, so **no launchd/systemd** is
needed: the app IS the clock.

## Local rebuild

The single most important thing to know: **`npm run tauri build` alone is not
enough.** The sidecar at `src-tauri/binaries/ovp2-<triple>` is a hand-staged
static file. `tauri build` packs whatever binary is sitting there — it does NOT
rebuild it. If that binary was produced by a plain `cargo build -p ovp-cli`
(without the live features), every daily run fails on the first needs-content
source with:

```
live web fetch requires a build with `--features web-fetch-live`
```

### Full build (sidecar + DMG)

```bash
# From repo root. Builds ovp2 with all live features and stages it into
# src-tauri/binaries/ovp2-<triple>, then bundles the .app + .dmg.
bash scripts/build-desktop-sidecar.sh
cd apps/desktop && npm run tauri build
# → target/release/bundle/dmg/OVP2_*.dmg  and  .../bundle/macos/OVP2.app
```

### Hot-swap the sidecar into a running app

When you only changed Rust code in the CLI crates (no frontend, no Tauri
backend, no `tauri.conf.json`), skip the full re-bundle — just rebuild the
sidecar and drop it into the live app. ~50s vs several minutes, and the script
ad-hoc re-signs the replaced binary so macOS will exec it:

```bash
INSTALL_APP=/Applications/OVP2.app bash scripts/build-desktop-sidecar.sh
```

Then restart the app for the in-process scheduler to pick up the new binary.

### Verify

Do **not** trust `strings | grep` on the bundle to check features — brotli
compression and symbol stripping give false negatives. Functional check:

```bash
# Direct: run the bundled CLI and hit the portal.
/Applications/OVP2.app/Contents/MacOS/ovp2 serve --vault-root <vault> &
curl -s http://127.0.0.1:<port>/api/ask/status

# Or end-to-end: launch the app, System page → "Pipeline run".
```

## Features

The sidecar MUST be built with these `ovp-cli` features (the default-OFF set),
because `schedule.json` passes the matching flags unconditionally:

| feature | CLI flag it enables |
|---|---|
| `anthropic` | live LLM (Ask, daily reader) |
| `pinboard-live` | `--pinboard-live` |
| `web-fetch-live` | `--web-fetch-live` (needs-content enrichment) |
| `github-live` | `--github-live` (repo URL enrichment) |

`embed` is intentionally OFF for distribution (ort has no clean
`x86_64-darwin` build; see `.github/workflows/release-desktop.yml`).

## Release

CI-driven: tag `desktop-v*` (its own namespace; `release.yml` is guarded to
skip these) and the `release-desktop.yml` workflow builds arm64 + x64 DMGs and
publishes them with SHA256SUMS.

```bash
git tag desktop-v2.0.2
git push origin desktop-v2.0.2
```

## Architecture notes

- **Backend** (`src-tauri/src/lib.rs`, ~250 lines): starts `ovp-server`
  in-process, exposes Tauri commands for vault management + ops (manual run,
  retry, ack, providers GUI), and runs the scheduler timer that exec's
  `ovp2 schedule tick` every ~10 min.
- **Sidecar resolution** (`resolve_ovp2_bin`): `OVP2_BIN` env → sidecar next
  to the app exe → dev fallback `target/{release,debug}/ovp2`. The scheduler
  no-ops (portal still runs) if no binary is found.
- **Capabilities/permissions**: Tauri v2 ACL — the loopback portal is a
  *remote* source to Tauri, so capabilities split local (splash) vs remote
  (portal) with explicit `permissions/*.toml`. `cargo check -p ovp2-desktop`
  validates them.
- **Signing**: ad-hoc (`signingIdentity "-"` in `tauri.conf.json`), not
  notarized — first launch needs `xattr -dr com.apple.quarantine` or a
  right-click → Open. Notarization is a follow-up.
