#!/usr/bin/env bash
# M15 Phase 3B — OVP arm over the 12-article sample manifest.
# Per article (slug s01..s12, aligned with m15_capture_kmem.py):
#   1) extract-units --client live      -> record the frozen v5 base cassette
#   2) extract-units --repair --client live -> base replays + critic live (M14a.8)
#   3) m15_card_synthesis.py            -> cited memory cards (frozen card_synth/v1)
# Outputs under .run/m15/ovp/<slug>/ (gitignored). Continues past per-article errors.
set -u
cd "$(dirname "$0")/.."
set -a; . ./.env.live; set +a
export OVP_LLM_MAX_TOKENS=24000 OVP_LLM_TIMEOUT_SECS=300
CACHE=.run/m15/cassettes
mkdir -p "$CACHE"

# Build once so each `cargo run` just executes.
cargo build -q -p ovp-cli --features anthropic 2>&1 | tail -2

# slug<TAB>path lines from the manifest (stable order == KMEM slugs).
python3 - <<'PY' > .run/m15/ovp_cases.tsv
import json
m=json.load(open("docs/m15/sample-manifest.json"))
for i,p in enumerate(m["sample"],1):
    print(f"s{i:02d}\t{p}")
PY

while IFS=$'\t' read -r slug path; do
  out=".run/m15/ovp/$slug"
  mkdir -p "$out"
  echo "=== $slug : $(basename "$path") ==="
  cargo run -q -p ovp-cli --features anthropic -- extract-units \
    --client live --cache-dir "$CACHE" --input "$path" --out "$out/base" 2>&1 | tail -3 || echo "  base FAILED"
  cargo run -q -p ovp-cli --features anthropic -- extract-units --repair \
    --client live --cache-dir "$CACHE" --critic-cache-dir "$CACHE" \
    --input "$path" --out "$out" 2>&1 | tail -3 || echo "  repair FAILED"
  if [ -f "$out/units.accepted.json" ]; then
    python3 scripts/m15_card_synthesis.py --units "$out/units.accepted.json" --out "$out" 2>&1 | tail -2 || echo "  synth FAILED"
  else
    echo "  no units.accepted.json — skipping synth"
  fi
done < .run/m15/ovp_cases.tsv
echo "OVP arm done."
