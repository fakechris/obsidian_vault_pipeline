#!/usr/bin/env bash
# M16 Phase: re-synthesize cards with FROZEN card_synth/v2 over the SAME M15
# repaired units (reuse truth layer; no re-extraction, no critic, no Referent).
set -u
cd "$(dirname "$0")/.."
set -a; . ./.env.live; set +a
export OVP_LLM_MAX_TOKENS=24000 OVP_LLM_TIMEOUT_SECS=300
PROMPT=docs/m16/card-synthesis-prompt.v2.md
for i in $(seq -w 1 12); do
  slug="s$i"
  units=".run/m15/ovp/$slug/units.accepted.json"
  out=".run/m16/ovp/$slug"
  [ -f "$units" ] || { echo "$slug: no units, skip"; continue; }
  mkdir -p "$out"
  python3 scripts/m15_card_synthesis.py --units "$units" --out "$out" --prompt "$PROMPT" 2>&1 | tail -1
done
echo "M16 v2 synth done."
