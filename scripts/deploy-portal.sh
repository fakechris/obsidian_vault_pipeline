#!/usr/bin/env bash
# Build the portal SPA and deploy it into a vault, then PROVE it took effect.
#
# Why this exists: `serve` resolves each file from the vault's deployed
# `.ovp/console/app/` BEFORE the `--viz-dir` overlay / app bundle
# (`read_app_file`, ovp-server/src/lib.rs). So while that directory exists,
# rebuilding the binary — or repackaging the desktop app — changes nothing
# about what the portal serves. The stale `index.html` wins, it names stale
# asset hashes, and those resolve from the same stale copy: the whole page is
# the old build, with a green build log to match.
#
# The verify step is the point. "npm run build succeeded" is exactly the
# evidence that lets this mistake survive a debugging session.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VAULT="${1:-${OVP2_VAULT:-}}"
if [[ -z "$VAULT" ]]; then
  echo "usage: $0 <vault-root>    (or set OVP2_VAULT)" >&2
  exit 2
fi
if [[ ! -d "$VAULT/.ovp" ]]; then
  echo "not a vault (no .ovp/): $VAULT" >&2
  exit 2
fi

APP_DIR="$VAULT/.ovp/console/app"
DIST="$ROOT/console-ui/dist"

echo "==> building console-ui"
npm --prefix "$ROOT/console-ui" run build

echo "==> deploying to $APP_DIR"
# Replace wholesale: a merge would leave orphaned old asset hashes behind, and
# those are exactly what a stale index.html would keep pointing at.
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
cp -R "$DIST"/. "$APP_DIR"/

BUILT_HASH="$(grep -o 'assets/[A-Za-z0-9._-]*\.js' "$DIST/index.html" | head -1)"
echo "==> built entry: $BUILT_HASH"

# Verify against a RUNNING portal when we can find one. The desktop app picks a
# fresh random port each launch and records it here; `ovp2 serve` defaults to
# 3141. A miss is not an error — the deploy is still done.
PORT="$(grep -o '127\.0\.0\.1:[0-9]*' "$VAULT/.ovp/desktop-portal.log" 2>/dev/null | tail -1 | cut -d: -f2 || true)"
PORT="${PORT:-3141}"
SERVED="$(curl -fsS --max-time 3 "http://127.0.0.1:$PORT/" 2>/dev/null \
  | grep -o 'assets/[A-Za-z0-9._-]*\.js' | head -1 || true)"

if [[ -z "$SERVED" ]]; then
  echo "==> no portal answering on 127.0.0.1:$PORT — deployed, not verified live"
  echo "    start one, then: curl -s http://127.0.0.1:<port>/ | grep -o 'assets/[^\"]*\.js'"
  exit 0
fi

if [[ "$SERVED" == "$BUILT_HASH" ]]; then
  echo "==> verified: portal on :$PORT serves $SERVED"
  echo "    hard-refresh the browser (Cmd+Shift+R) if the tab was already open"
else
  echo "!!! MISMATCH: portal on :$PORT serves $SERVED, expected $BUILT_HASH" >&2
  echo "    the running server is reading a different copy — check for another" >&2
  echo "    vault, or an OVP2_VIZ_DIR override on the running process." >&2
  exit 1
fi
