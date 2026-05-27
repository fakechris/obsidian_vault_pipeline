#!/usr/bin/env bash
# Architecture invariant gate for OVP Next.
# Exit 0 if all invariants hold, non-zero with diagnostics otherwise.
# See docs/invariants.md for the source of truth.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

fail=0

check() {
    local label="$1"
    local pattern="$2"
    local scope="$3"
    if grep -rEn --include='*.rs' --include='*.toml' "$pattern" $scope >/dev/null 2>&1; then
        echo "FAIL  [$label]"
        grep -rEn --include='*.rs' --include='*.toml' "$pattern" $scope
        fail=1
    else
        echo "ok    [$label]"
    fi
}

# Invariant 2: no serde_json::Value in ovp-core public API
check "no serde_json::Value in ovp-core/src" \
    'serde_json::Value' \
    "crates/ovp-core/src"

# Invariant 4: no python/ovp subprocess
check "no Command::new(\"python\")" \
    'Command::new\("python' \
    "crates"
check "no Command::new(\"ovp\")" \
    'Command::new\("ovp' \
    "crates"

# Invariant 5: no pyo3
check "no pyo3 dep" \
    'pyo3' \
    "crates"

# Invariant 6: no async runtime in v0.1
check "no tokio/async-std" \
    '(^|[^a-z_])(tokio|async_std|async-std)([^a-z_]|$)' \
    "crates"

# Invariant 7: no legacy ovp_pipeline import/reference
check "no ovp_pipeline / from ovp" \
    '(ovp_pipeline|from ovp)' \
    "crates"

if [[ $fail -ne 0 ]]; then
    echo
    echo "Architecture check FAILED. See docs/invariants.md."
    exit 1
fi

echo
echo "Architecture check passed."
