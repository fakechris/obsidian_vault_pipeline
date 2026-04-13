# Research-Tech Verify

Use this checklist when validating the primary `research-tech` pack.

## Quick Runtime Checks

```bash
ovp-packs --json
ovp-doctor --pack research-tech --json
ovp --help
ovp-autopilot --help
```

Expected:

- `research-tech` is `role=primary`
- `default-knowledge` is `role=compatibility`
- default workflow pack is `research-tech`

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
```

Expected:

- export completes
- output file exists
- content is a compiled markdown artifact, not a raw database dump
