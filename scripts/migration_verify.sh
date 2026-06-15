#!/usr/bin/env bash
# Level 3 migration verification — confirms all hard gates for sign-off.
# Run from repo root: bash scripts/migration_verify.sh [--vault-dir <path>]
#
# Exit 0 = all gates green, 1 = at least one gate red.
# Operational gates (2-week no-fallback, knowledge.db sign-off) are checked
# by presence of marker files; if absent, those gates report NOT MET.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VAULT_DIR="${VAULT_DIR:-}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --vault-dir) VAULT_DIR="$2"; shift 2;;
        *) echo "Usage: $0 [--vault-dir <path>]"; exit 1;;
    esac
done

fail=0
section() { echo; echo "══════ $1 ══════"; }

section "Gate 1: cargo test --workspace"
if cargo test --workspace --quiet 2>&1; then
    echo "✓ All workspace tests pass."
else
    echo "✗ cargo test FAILED"
    fail=1
fi

section "Gate 2: cargo clippy (no warnings)"
if cargo clippy --workspace --all-targets -- -D warnings >/dev/null 2>&1; then
    echo "✓ Clippy clean (exit code 0, -D warnings)."
else
    echo "✗ Clippy has warnings/errors (exit code non-zero)"
    fail=1
fi

section "Gate 3: check_architecture.sh"
if bash scripts/check_architecture.sh; then
    echo "✓ Architecture invariants hold."
else
    echo "✗ Architecture check FAILED"
    fail=1
fi

section "Gate 4: 4 new crates present in arch gate"
for crate in ovp-enrich ovp-memory ovp-server ovp-mcp; do
    if [[ -d "crates/$crate" ]]; then
        echo "  ✓ $crate exists"
    else
        echo "  ✗ $crate NOT FOUND"
        fail=1
    fi
done

section "Gate 5: ovp-enrich reqwest is optional"
if grep -E '^reqwest.*optional = true' crates/ovp-enrich/Cargo.toml >/dev/null 2>&1; then
    echo "✓ reqwest is optional in ovp-enrich"
else
    echo "✗ reqwest is NOT optional in ovp-enrich"
    fail=1
fi

section "Gate 6: No Python in runtime path"
py_runtime=$(find crates -name '*.rs' -exec grep -l 'Command::new("python' {} \; 2>/dev/null || true)
if [[ -z "$py_runtime" ]]; then
    echo "✓ No Python subprocess calls in Rust crates."
else
    echo "✗ Python subprocess calls found:"
    echo "$py_runtime"
    fail=1
fi

section "Gate 7: Operational — 2-week no-fallback (marker check)"
marker=".ovp/signoff/no-fallback-confirmed"
if [[ -n "$VAULT_DIR" && -f "$VAULT_DIR/$marker" ]]; then
    echo "✓ Marker found: $VAULT_DIR/$marker"
else
    echo "⚠  NOT MET — marker '$marker' not found."
    echo "   Create it after 2+ weeks of Rust-only daily runs."
    fail=1
fi

section "Gate 8: Operational — knowledge.db sign-off (marker check)"
marker2=".ovp/signoff/knowledge-db-signoff"
if [[ -n "$VAULT_DIR" && -f "$VAULT_DIR/$marker2" ]]; then
    echo "✓ Marker found: $VAULT_DIR/$marker2"
else
    echo "⚠  NOT MET — marker '$marker2' not found."
    echo "   Create it after migration/explicit-abandon of knowledge.db."
    fail=1
fi

section "Summary"
if [[ $fail -ne 0 ]]; then
    echo "❌ Level 3 migration sign-off: NOT READY (some gates failed)"
    exit 1
else
    echo "✅ Level 3 migration sign-off: ALL GATES GREEN"
    exit 0
fi
