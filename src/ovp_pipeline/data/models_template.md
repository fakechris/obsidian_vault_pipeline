---
type: documentation
schema_version: 1
---

# Models — operator notes

<!-- Plain-English notes on **when to use which profile** in
     `.ovp/llm_profiles.yaml`.  This file is documentation only —
     never parsed by OVP.  Edit when your model lineup changes. -->

## When I reach for each profile

### Fast

Use when latency and cost matter more than reasoning depth.

- Background extraction (the absorb pipeline routes here by default)
- Single-fact lookups
- "Did you cover X?" sanity checks while drafting

### Balanced

Default for anchored inquiry (`ovp-ask`).  The middle of the
quality/cost curve.

- Reader-side chat from `/note` and `/object`
- Daily digest synthesis
- Anything where I want to see citations + nuance but don't want
  to wait 30s

### Deep

Reach for it explicitly.

- Architecture / design conversations
- Multi-page synthesis where the prompt is dense
- Anything I'd otherwise hand to Opus directly

## When I add a custom profile

If I need a new provider, model, or cost tier, I add the entry to
`.ovp/llm_profiles.yaml` and refer to it via `ovp-ask --profile
<name>` on the CLI.  The Reader UI dropdown stays Fast / Balanced /
Deep — custom profiles are operator-only.

## Switching providers

Editing `.ovp/llm_profiles.yaml` is enough; no restart needed.
The loader's mtime cache picks the new file up on the next call.
