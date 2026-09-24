#!/usr/bin/env bash
# Run every TLA+ model listed in docs/tla/models.txt and check each result
# against its expectation. Green configs must pass; negative controls must
# report exactly the named invariant as violated.
set -euo pipefail

TLA_VERSION=v1.7.4
TLA_SHA256=936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88
# Run artifacts (jar cache, TLC state dirs, per-config logs) live under the
# gitignored .run/ so they survive reboots and stay auditable (AGENTS.md).
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RUN_DIR=$ROOT/.run/tla/$(date +%Y%m%d-%H%M%S)
mkdir -p "$RUN_DIR"
JAR=${TLA2TOOLS_JAR:-$ROOT/.run/tla/tla2tools-$TLA_VERSION.jar}
# macOS ships a /usr/bin/java stub that fails without a JDK; prefer Homebrew's.
if [[ -z ${JAVA:-} ]]; then
  JAVA=/opt/homebrew/opt/openjdk/bin/java
  [[ -x $JAVA ]] || JAVA=java
fi

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

if [[ ! -f $JAR ]]; then
  curl -fsSL -o "$JAR" \
    "https://github.com/tlaplus/tlaplus/releases/download/$TLA_VERSION/tla2tools.jar"
fi
[[ $(sha256 "$JAR") == "$TLA_SHA256" ]] || { echo "tla2tools.jar hash mismatch: $JAR" >&2; exit 1; }

cd "$ROOT/docs/tla"
fail=0
while read -r module cfg expect; do
  [[ -z ${module:-} || $module == \#* ]] && continue
  out=$("$JAVA" -XX:+UseParallelGC -cp "$JAR" tlc2.TLC -workers auto \
          -metadir "$RUN_DIR/${cfg%.cfg}.states" -config "$cfg" "$module.tla" 2>&1 </dev/null || true)
  printf '%s\n' "$out" > "$RUN_DIR/${cfg%.cfg}.log"
  if [[ $expect == ok ]]; then
    grep -q "Model checking completed. No error has been found." <<<"$out" && r=pass || r=FAIL
  else
    grep -qx "Error: Invariant $expect is violated." <<<"$out" && r=pass || r=FAIL
  fi
  printf '%-4s %-12s %-28s expect=%s\n' "$r" "$module" "$cfg" "$expect"
  if [[ $r == FAIL ]]; then fail=1; grep -E "^Error|violated|No error" <<<"$out" | head -5 >&2; fi
done < models.txt
echo "logs: $RUN_DIR"
exit $fail
