#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
OVP_PACKAGE_SPEC="${OVP_PACKAGE_SPEC:-obsidian-vault-pipeline}"
OVP_DISTRIBUTION_NAME="${OVP_DISTRIBUTION_NAME:-obsidian-vault-pipeline}"

if ! RESOLVED_PYTHON_BIN="$(command -v "$PYTHON_BIN")"; then
  echo "Could not find Python interpreter: $PYTHON_BIN" >&2
  exit 1
fi
PYTHON_BIN="$RESOLVED_PYTHON_BIN"

install_package() {
  if "$PYTHON_BIN" -m pip install --user "$1"; then
    return 0
  fi
  if [ "${OVP_ALLOW_BREAK_SYSTEM_PACKAGES:-0}" = "1" ]; then
    "$PYTHON_BIN" -m pip install --user --break-system-packages "$1"
    return 0
  fi
  cat >&2 <<EOF
User-scoped pip install failed for $1.

Recommended options:
  1. Use pipx:
     pipx install $1
  2. Re-run this installer with explicit opt-in:
     OVP_ALLOW_BREAK_SYSTEM_PACKAGES=1 ./scripts/install-user.sh

This script does not force --break-system-packages by default.
EOF
  return 1
}

install_package "$OVP_PACKAGE_SPEC"

installer_args=(
  -m
  openclaw_pipeline.installer
  --distribution
  "$OVP_DISTRIBUTION_NAME"
  --python-executable
  "$PYTHON_BIN"
)

if [ -n "${OVP_BIN_DIR:-}" ]; then
  installer_args+=(--bin-dir "$OVP_BIN_DIR")
fi

"$PYTHON_BIN" "${installer_args[@]}"
