#!/usr/bin/env bash
set -u; cd "$(dirname "$0")/.."
set -a; . ./.env.live; set +a
export OVP_LLM_MAX_TOKENS=24000 OVP_LLM_TIMEOUT_SECS=300
for i in $(seq -w 1 12); do
  slug="s$i"; units=".run/m15/ovp/$slug/units.accepted.json"; out=".run/m16_1/ovp/$slug"
  [ -f "$units" ] || { echo "$slug: no units"; continue; }
  python3 scripts/m15_card_synthesis.py --units "$units" --out "$out" --prompt docs/m16/card-synthesis-prompt.v3.md 2>&1 | tail -1
done
echo "v3 synth done."
