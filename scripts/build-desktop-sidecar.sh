#!/usr/bin/env bash
# Build the ovp2 CLI sidecar the desktop app's in-app scheduler execs.
#
# Product schedule.json always passes --web-fetch-live / --github-live /
# --pinboard-live. A plain `cargo build -p ovp-cli` omits those features and
# bricks every daily run the moment any needs-content item is pending
# (2026-07-31 last-run regression). Mirror release-desktop.yml features.
#
# This is THE correct local build path — `npx tauri build` alone is NOT enough:
# the sidecar at apps/desktop/src-tauri/binaries/ovp2-<triple> is a HAND-STAGED
# static file (CI rebuilds it each cut; local `tauri build` packs whatever is
# there). If you `cargo build` without --features and stage that binary, every
# daily run fails on the first needs-content source with:
#   "live web fetch requires a build with `--features web-fetch-live`"
#
# == LOCAL REBUILD WORKFLOW ==
# 1) (this script)  Build the sidecar with all live features → stage into
#    apps/desktop/src-tauri/binaries/ovp2-<triple>.
# 2) Full DMG/app bundle rebuild for distribution:
#        cd apps/desktop && npm run tauri build            # → target/release/bundle/
# 3) Hot-swap the sidecar into an already-running app (NO full re-bundle,
#    ~50s vs minutes): pass INSTALL_APP=/path/to/OVP2.app. The script copies
#    the fresh binary next to the app executable and ad-hoc re-signs it so
#    macOS will exec the replaced binary:
#        INSTALL_APP=/Applications/OVP2.app ./scripts/build-desktop-sidecar.sh
#
# == VERIFY (do NOT trust `strings` grep — brotli/false negatives) ==
# Functional check: run the bundled CLI directly and hit the portal:
#   /Applications/OVP2.app/Contents/MacOS/ovp2 serve --vault-root <vault> &
#   curl -s http://127.0.0.1:<port>/api/ask/status
# Or just launch the app and trigger a daily run from the System page.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TRIPLE="${1:-$(rustc -vV | awk '/^host:/{print $2}')}"
# Per-target default for LOCAL staging: `embed` pulls in `ort`, which has no
# clean Intel-mac build — x64 keeps the lean set, arm64 (every dev/operator
# machine) gets the embedder so a locally staged sidecar can run
# crystal-themes (2026-08-06: a lean sidecar left themes.json a month stale).
# RELEASES pin their own lean set via OVP2_SIDECAR_FEATURES in
# release-desktop.yml — this default never changes shipped DMGs.
if [ -n "${OVP2_SIDECAR_FEATURES:-}" ]; then
  FEATURES="$OVP2_SIDECAR_FEATURES"
elif [ "$TRIPLE" = "x86_64-apple-darwin" ]; then
  FEATURES="anthropic,pinboard-live,web-fetch-live,github-live"
else
  FEATURES="anthropic,pinboard-live,web-fetch-live,github-live,embed"
fi
OUT_DIR="apps/desktop/src-tauri/binaries"
OUT="$OUT_DIR/ovp2-${TRIPLE}"

echo "building ovp2 sidecar ($TRIPLE) features=$FEATURES"
cargo build --release -p ovp-cli --target "$TRIPLE" --features "$FEATURES"
mkdir -p "$OUT_DIR"
cp "target/${TRIPLE}/release/ovp2" "$OUT"
chmod +x "$OUT"
echo "wrote $OUT ($(wc -c <"$OUT" | tr -d ' ') bytes)"

# Optional: install into a running OVP2.app so the in-app scheduler picks it up
# without a full DMG rebuild. Usage: INSTALL_APP=/Applications/OVP2.app ./scripts/build-desktop-sidecar.sh
if [[ -n "${INSTALL_APP:-}" ]]; then
  DEST="$INSTALL_APP/Contents/MacOS/ovp2"
  if [[ ! -d "$INSTALL_APP/Contents/MacOS" ]]; then
    echo "INSTALL_APP=$INSTALL_APP has no Contents/MacOS" >&2
    exit 1
  fi
  cp "$OUT" "$DEST"
  chmod +x "$DEST"
  # ad-hoc re-sign so macOS will exec the replaced binary
  if command -v codesign >/dev/null 2>&1; then
    codesign --force --sign - "$DEST" 2>/dev/null || true
  fi
  echo "installed $DEST"
fi
