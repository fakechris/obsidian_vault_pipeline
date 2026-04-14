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
  "$PYTHON_BIN" -m pip install --user --break-system-packages "$1"
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
