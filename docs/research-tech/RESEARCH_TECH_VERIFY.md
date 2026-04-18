# Research-Tech Verify

Use this checklist when validating the primary `research-tech` pack.

## Quick Runtime Checks

```bash
ovp-packs --json
ovp-doctor --pack research-tech --json
ovp-truth objects --vault-dir /path/to/vault
ovp-ui --vault-dir /path/to/vault --port 8787
ovp --help
ovp-autopilot --help
```

Expected:

- `research-tech` is `role=primary`
- `default-knowledge` is `role=compatibility`
- default workflow pack is `research-tech`
- `ovp-truth` can read object rows directly from `knowledge.db`
- `ovp-ui` starts a local DB-backed browser surface

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
  - `Where To Start`
  - `Orientation Brief`
  - `entry_sections`
- shared shell pages keep the requested pack scope in links/forms
- assembly contract card is visible on each page
- governance contract card is visible on runtime/operator pages
- `/briefing` behaves as an orientation product rather than a raw operator snapshot
- object/topic/event/contradiction pages expose compiled sections for:
  - current state
  - why it matters
  - evidence traceability
  - open tensions
  - where to go next
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
  - item-level `recommended_action.resolver_rule_name`
  - item-level `recommended_action.governance_provider_*`
- `/api/signals` includes:
  - `governance_contract`
  - item-level `recommended_action.resolver_rule_name`
  - item-level `recommended_action.governance_provider_*`
- `/api/actions` includes:
  - `governance_contract`
  - item-level `resolver_rule_name`
  - item-level `governance_provider_*`
