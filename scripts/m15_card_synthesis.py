#!/usr/bin/env python3
"""M15 OVP view layer: compile repaired grounded Units -> cited memory cards.

Uses the FROZEN prompt docs/m15/card-synthesis-prompt.v1.md (card_synth/v1).
Live model from .env.live (MiniMax). Deterministic post-check: every card's
cited_unit_ids must exist in the accepted units; uncited/invalid-citation cards
are dropped and counted. Writes cards.json + cards.md + synth-report.json.

Usage:
  python3 scripts/m15_card_synthesis.py --units .run/m15/ovp/<case>/units.accepted.json \
      --out .run/m15/ovp/<case>
"""
import argparse, json, os, re, sys, urllib.request, urllib.error

PROMPT_PATH = "docs/m15/card-synthesis-prompt.v1.md"
PROMPT_VERSION = "card_synth/v1"


def load_env_live():
    env = {}
    for line in open(".env.live"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k] = v.strip().strip('"').strip("'")
    return env


def extract_obj(text):
    t = text.strip()
    for fence in ("```json", "```"):
        if t.startswith(fence):
            t = t[len(fence):].lstrip("\n").rstrip("`").strip()
    i = t.find("{")
    if i < 0:
        return None
    depth = 0; in_str = False; esc = False
    for j in range(i, len(t)):
        c = t[j]
        if in_str:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': in_str = False
            continue
        if c == '"': in_str = True
        elif c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return t[i:j + 1]
    return None


def call_model(env, system, user, max_tokens=20000):
    body = json.dumps({"model": env.get("OVP_LLM_MODEL"), "max_tokens": max_tokens,
                       "system": system, "messages": [{"role": "user", "content": user}]}).encode()
    req = urllib.request.Request(env["ANTHROPIC_BASE_URL"], data=body, headers={
        "content-type": "application/json", "x-api-key": env["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01"})
    resp = urllib.request.urlopen(req, timeout=300)
    out = json.loads(resp.read())
    text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prompt", default=PROMPT_PATH)
    args = ap.parse_args()

    env = load_env_live()
    units = json.load(open(args.units, encoding="utf-8"))
    valid_ids = {u["id"] for u in units}
    template = open(args.prompt, encoding="utf-8").read()
    # system = the frozen prompt (everything before the JSON output block is guidance;
    # we send the whole asset as system, and the units as the user message).
    system = template
    lines = [f'{u["id"]} | {u["kind"]}/{u.get("subtype") or "-"} | text="{u["text"]}" | quote="{u["evidence"]["quote"]}"'
             for u in units]
    user = "## Accepted Units\n\n" + "\n".join(lines) + "\n\nCompile into 5-8 cited memory cards. JSON only."

    raw = call_model(env, system, user)
    open(os.path.join(args.out, "card-reply.txt"), "w", encoding="utf-8").write(raw)
    obj = extract_obj(raw)
    parsed = json.loads(obj) if obj else {"cards": []}
    cards = parsed.get("cards", []) or []

    # Deterministic citation post-check: keep only cards citing >=1 real unit id.
    kept, dropped = [], []
    for c in cards:
        cites = [cid for cid in (c.get("cited_unit_ids") or []) if cid in valid_ids]
        # tolerate truncated ids (u-000 -> u-000-xxxx) by prefix
        if not cites:
            for cid in (c.get("cited_unit_ids") or []):
                cites += [v for v in valid_ids if v == cid or v.startswith(cid + "-")]
        cites = sorted(set(cites))
        if cites:
            c["cited_unit_ids"] = cites
            kept.append(c)
        else:
            dropped.append({"title": c.get("title"), "reason": "no valid unit citation"})

    report = {
        "prompt_version": PROMPT_VERSION,
        "units_in": len(units),
        "cards_returned": len(cards),
        "cards_kept": len(kept),
        "cards_dropped_uncited": len(dropped),
        "dropped": dropped,
        "parse_ok": obj is not None,
    }
    os.makedirs(args.out, exist_ok=True)
    json.dump(kept, open(os.path.join(args.out, "cards.json"), "w"), ensure_ascii=False, indent=2)
    json.dump(report, open(os.path.join(args.out, "synth-report.json"), "w"), ensure_ascii=False, indent=2)
    md = [f"# OVP memory cards ({PROMPT_VERSION})", ""]
    for i, c in enumerate(kept, 1):
        md += [f"## {i}. {c.get('title','')}", f"*{c.get('unit_type','')}* — cites: {', '.join(ci[:12] for ci in c['cited_unit_ids'])}",
               "", c.get("content", ""), ""]
    open(os.path.join(args.out, "cards.md"), "w", encoding="utf-8").write("\n".join(md))
    print(f"{os.path.basename(args.out)}: units={len(units)} cards={len(kept)} dropped={len(dropped)} parse_ok={obj is not None}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
