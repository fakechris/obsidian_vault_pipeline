#!/usr/bin/env bash
# Architecture invariant gate for OVP Next.
# Exit 0 if all invariants hold, non-zero with diagnostics otherwise.
# See docs/invariants.md for the source of truth — and for which invariants
# this script *cannot* enforce (those are documented but not gated).

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

fail=0

# Grep for an extended-regex pattern across paths; FAIL if anything matches.
# Args: <label> <ERE pattern> <path1> [<path2> ...]
check() {
    local label="$1"; shift
    local pattern="$1"; shift
    local hits
    hits=$(grep -rEn --include='*.rs' --include='*.toml' "$pattern" "$@" 2>/dev/null || true)
    if [[ -n "$hits" ]]; then
        echo "FAIL  [$label]"
        echo "$hits"
        fail=1
    else
        echo "ok    [$label]"
    fi
}

# === Invariant #2: no serde_json::Value in core public API ===
check "no serde_json::Value in ovp-core/src" \
    'serde_json::Value' \
    "crates/ovp-core/src"

# === Invariant #3: no HashMap<String,_> payloads in Record/WriteOp shapes ===
# Only enforced on the data-shape files. Internal runner state (graph.rs,
# manifest.rs) is allowed to use HashMap as a working data structure.
check "no HashMap<String,_> in record.rs / plan.rs" \
    'HashMap<String' \
    "crates/ovp-core/src/record.rs" \
    "crates/ovp-core/src/plan.rs"

# === Invariant #4: no subprocess shell-out to python/ovp or general shells ===
check 'no Command::new("python...")' \
    'Command::new\("python' \
    "crates"
check 'no Command::new("ovp...")' \
    'Command::new\("ovp' \
    "crates"
check 'no Command::new("bash|sh|zsh|uv|pipenv|poetry")' \
    'Command::new\("(bash|sh|zsh|uv|pipenv|poetry)' \
    "crates"

# === Invariant #5: no pyo3, no embedded Python ===
# Scope deliberately includes the workspace Cargo.toml so workspace.dependencies
# can't smuggle a runtime in.
check "no pyo3 dep (workspace + crates)" \
    'pyo3' \
    "crates" "Cargo.toml"

# === Invariant #6: no async/futures in v0.1 ===
# Catches both dep names AND actual usage (async fn, .await, tokio::, futures::).
check "no tokio/async-std deps (workspace + crates)" \
    '(^|[^a-z_])(tokio|async_std|async-std)([^a-z_]|$)' \
    "crates" "Cargo.toml"
check "no async fn / .await / futures:: in source" \
    '(async fn |\.await\b|tokio::|futures::|async_trait)' \
    "crates"

# === Invariant #7: no legacy ovp_pipeline import/reference ===
check "no ovp_pipeline / from ovp" \
    '(ovp_pipeline|from ovp)' \
    "crates"

# === Invariant #1: ovp-core has no deps on higher-level crates ===
# Inspects ovp-core/Cargo.toml directly. The forbidden list catches anything
# that would smuggle CLI / domain / LLM concerns into core.
echo -n "ok    [ovp-core deps clean] ... "
core_toml="crates/ovp-core/Cargo.toml"
if [[ ! -f "$core_toml" ]]; then
    echo "FAIL — $core_toml missing"
    fail=1
else
    bad_deps=$(grep -E '^(ovp-cli|ovp-domain|ovp-llm|ovp-app|ovp-filters|ovp-stores) *=' "$core_toml" 2>/dev/null || true)
    if [[ -n "$bad_deps" ]]; then
        echo "FAIL"
        echo "$bad_deps"
        fail=1
    else
        echo "passed"
    fi
fi

# === Invariant #9: no Transform impl holds an effect client ===
# Heuristic: any file declaring `impl Transform<...> for <T>` and ALSO
# containing `Box<dyn (.*Client|.*Store|.*Fetcher)>` is using the wrong
# trait — must implement `EffectfulTransform` instead.
echo -n "ok    [no Transform impl holds an effect client] ... "
violators=""
# `\bimpl Transform<` so we don't match EffectfulTransform.
while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    if grep -E 'Box<dyn[^>]*(Client|Store|Fetcher)>' "$f" >/dev/null 2>&1; then
        violators="${violators}${f}"$'\n'
    fi
done < <(grep -rlE '^impl Transform<' crates --include='*.rs' 2>/dev/null || true)
if [[ -n "$violators" ]]; then
    echo "FAIL"
    printf '%s' "$violators"
    fail=1
else
    echo "passed"
fi

if [[ $fail -ne 0 ]]; then
    echo
    echo "Architecture check FAILED. See docs/invariants.md."
    exit 1
fi

echo
echo "Architecture check passed."
echo
echo "Note: invariants #8 (explicit topology), #10 (writes via WritePlan),"
echo "      #11 (derived state rebuildable), and #12 (EventLog append-only)"
echo "      are semantic and cannot be grep-enforced. They are gated by code"
echo "      review. #9 (Transform purity) is partially gated by the"
echo "      effect-client heuristic above."
