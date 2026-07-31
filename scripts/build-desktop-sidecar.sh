#!/usr/bin/env bash
# Build the ovp2 CLI sidecar the desktop app's in-app scheduler execs.
#
# Product schedule.json always passes --web-fetch-live / --github-live /
# --pinboard-live. A plain `cargo build -p ovp-cli` omits those features and
# bricks every daily run the moment any needs-content item is pending
# (2026-07-31 last-run regression). Mirror release-desktop.yml features.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TRIPLE="${1:-$(rustc -vV | awk '/^host:/{print $2}')}"
FEATURES="${OVP2_SIDECAR_FEATURES:-anthropic,pinboard-live,web-fetch-live,github-live}"
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
