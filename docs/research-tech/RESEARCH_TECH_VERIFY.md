# Research-Tech Verify

Use this checklist when validating the primary `research-tech` pack.

## Quick Runtime Checks

```bash
ovp-packs --json
ovp-doctor --pack research-tech --json
ovp-truth objects --vault-dir /path/to/vault
python3 -m openclaw_pipeline.commands.watch_progress --vault-dir /path/to/vault --once
ovp --help
ovp-autopilot --help
```

Start the UI server in a separate terminal before browser or `/api/runtime` checks:

```bash
ovp-ui --vault-dir /path/to/vault --port 8787
```

Expected:

- `research-tech` is `role=primary`
- `default-knowledge` is `role=compatibility`
- default workflow pack is `research-tech`
- `ovp-truth` can read object rows directly from `knowledge.db`
- `ovp-ui` starts a local DB-backed browser surface
- `watch_progress --once` reports one canonical runtime view instead of forcing operator inference from `ps` + JSONL

## Test Checks

```bash
PYTHONPATH=src python3.13 -m pytest -q tests/test_pack_e2e.py tests/test_pack_runtime_e2e.py tests/test_autopilot_contracts.py
PYTHONPATH=src python3.13 -m pytest -q
python3.13 -m compileall src/openclaw_pipeline
```

Expected:

- pack-level e2e stays green
- orchestrated runtime e2e stays green
- full suite stays green under Python 3.13

## Incremental Runtime Checks

Run the real daily entrypoint:

```bash
ovp --incremental --pack research-tech --vault-dir /path/to/vault
```

While it is running, inspect the same run through all three operator readers:

```bash
# Terminal A, keep running:
ovp-ui --vault-dir /path/to/vault --port 8787

# Terminal B:
python3 -m openclaw_pipeline.commands.watch_progress --vault-dir /path/to/vault --once
curl -s http://127.0.0.1:8787/api/runtime | jq
open http://127.0.0.1:8787/
```

`curl /api/runtime` and the root page require `ovp-ui` to be running in Terminal A.

Expected:

- `ovp --incremental` includes:
  - `pinboard`
  - `pinboard_process`
  - `clippings`
  - `articles`
  - `quality`
  - `fix_links`
  - `absorb`
  - `registry_sync`
  - `moc`
  - `knowledge_index`
- `watch_progress`, `/api/runtime`, and `/` agree on the same active run id
- `watch_progress`, `/api/runtime`, and `/` agree on the same:
  - current step
  - current item
  - progress summary
- counted stages expose a real denominator rather than a phase-count guess
- stale runs are reported separately from the active run
- `/` shows a `Current Workflow` card with the same progress summary as `/api/runtime`
- `pinboard_process` shows counted file progress
- `absorb` shows counted deep-dive progress with a live current item

## Vault Checks

```bash
ovp-doctor --pack research-tech --vault-dir /path/to/vault --json
```

Inspect:

- inbox counts
- processed counts
- whether `knowledge.db` exists
- operator docs/recipes are present

## Export Checks

```bash
ovp-export --pack research-tech --vault-dir /path/to/vault --target topic-overview --output-path /tmp/topic.md
ovp-export --pack research-tech --vault-dir /path/to/vault --target orientation-brief --output-path /tmp/orientation.json
```

Expected:

- both exports complete
- both output files exist
- `topic-overview` is a compiled markdown artifact, not a raw database dump
- `orientation-brief` is a compiled JSON artifact, not a raw observation snapshot
- orientation JSON includes:
  - `assembly_contract`
  - stable `compiled_sections`
  - `section_nav`
- export metadata includes both:
  - the resolved assembly recipe name/provider pack
  - the resolved source contract name/provider pack

## UI Contract Checks

```bash
ovp-ui --pack default-knowledge --vault-dir /path/to/vault --port 8787
```

Inspect:

- `/?pack=default-knowledge`
- `/object?id=<object-id>&pack=default-knowledge`
- `/topic?id=<object-id>&pack=default-knowledge`
- `/events?pack=default-knowledge`
- `/contradictions?pack=default-knowledge`
- `/briefing?pack=default-knowledge`
- `/signals?pack=default-knowledge`
- `/actions?pack=default-knowledge`

Expected:

- `/` renders a workbench entry surface with:
  - `Workflow Map`
  - `Orient`
  - `Inspect`
  - `Review`
  - `Trace`
  - `Explore`
  - `Where To Start`
  - `Orientation Brief`
  - `entry_sections`
- shared shell pages keep the requested pack scope in links/forms
- key operator pages render `Next Actions`
- assembly contract card is visible on each page
- governance contract card is visible on runtime/operator pages
- `/briefing` behaves as an orientation product rather than a raw operator snapshot
- `/briefing` includes a leading `Signal Loop` compiled section
- `/briefing` also includes an `Inbound Capture` compiled section when recent signals carry note-level capture audit
- note/object/briefing/production pages lead with:
  - a lead compiled section
  - then `Next Actions`
  - then deeper evidence/review sections
- note pages expose an `Inbound Capture` compiled section when pipeline/refine audit exists for the note
- `/signals` exposes:
  - impact counts
  - item-level `Impact`
  - item-level `Inbound capture`
  - the same lifecycle language as `/actions`
- `/actions` exposes:
  - impact counts
  - item-level `Impact`
  - queue/result lifecycle phrased the same way as `/signals`
- object/topic/event/contradiction pages expose compiled sections for:
  - current state
  - why it matters
  - evidence traceability
  - production chain
  - open tensions
  - where to go next
- note/object/topic/production pages make chain state visible without CLI/DB inspection:
  - `chain_status`
  - `missing_stages`
  - `chain_summary`
- `/events` explains:
  - grouping kind
  - anchor kind counts
  - why grouped rows are events instead of only dated-note projections
- `/contradictions` explains:
  - polarity semantics
  - evidence semantics
  - per-row tension summary
- compatibility-pack pages show assembly recipe inheritance from `research-tech`
- compatibility-pack pages also show when the source contract provider resolves to `default-knowledge`
- compatibility-pack runtime pages show governance inheritance from `research-tech`
- recommended actions and queued actions expose:
  - resolver rule name
  - governance provider pack/name
- the card exposes the resolved source contract:
  - `object/page`
  - `overview/topic`
  - `event/dossier`
  - `truth/contradictions`
  - `briefing` observation surface
- the governance card exposes the resolved maintenance contract:
  - review queues
  - signal rules
  - resolver rules

## Doctor Contract Checks

```bash
ovp-doctor --pack default-knowledge --json
```

Inspect:

- effective `assembly_recipes`
- shell `governance_contract`

Expected:

- `provider_pack` can stay `research-tech`
- `source_provider_pack` can differ and resolve to `default-knowledge`
- `orientation_brief` keeps both recipe provider and source provider on `research-tech`
- effective `governance_specs` stay inherited from `research-tech` for `default-knowledge`
- shell governance summary also resolves as inherited from `research-tech`

## API Contract Checks

```bash
curl 'http://127.0.0.1:8787/api/briefing?pack=default-knowledge'
curl 'http://127.0.0.1:8787/api/signals?pack=default-knowledge'
curl 'http://127.0.0.1:8787/api/actions?pack=default-knowledge'
```

Expected:

- `/api/briefing` includes:
  - `assembly_contract`
  - `governance_contract`
  - stable `compiled_sections`
  - `section_nav`
  - `loop_summary`
  - an `inbound_capture` compiled section when recent signals carry capture audit
  - item-level `recommended_action.resolver_rule_name`
  - item-level `recommended_action.governance_provider_*`
- `/api/signals` includes:
  - `governance_contract`
  - `impact_counts`
  - item-level `impact_summary`
  - item-level `capture_summary`
  - item-level `recommended_action.resolver_rule_name`
  - item-level `recommended_action.governance_provider_*`
- `/api/actions` includes:
  - `governance_contract`
  - `impact_counts`
  - item-level `impact_summary`
  - item-level `resolver_rule_name`
  - item-level `governance_provider_*`
