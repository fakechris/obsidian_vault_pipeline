# Prompt Registry

Versioned home for every LLM prompt the OVP pipeline runs.

## Layout

```
prompts/
├── README.md                    (this file)
└── <prompt_name>/
    ├── v1.md
    ├── v2.md
    └── v3-experimental.md       (optional)
```

`<prompt_name>` matches what `prompt_registry.get_prompt(name, version)`
asks for (e.g. `absorb`, `article-rewriter`, `entity-extract`).

## Adding a new version

1. Copy the closest existing version to a new file: e.g.
   `cp absorb/v2.md absorb/v3-experimental.md`.
2. Update the YAML frontmatter:
   - `version: v3-experimental` (must match filename stem)
   - `status: experimental` (becomes `stable` after rollout)
   - `schema_version: <int>` — bump only if the OUTPUT JSON shape
     changes.  Editing the prompt body without changing what fields
     the LLM emits doesn't require a schema bump.
   - `notes:` — explain what changed and why
3. Edit the prompt body (everything below the closing `---` fence).
4. Run the registry's frontmatter validation:
   ```
   pytest tests/test_prompt_registry.py
   ```
5. (Phase 2, not yet built) Configure A/B routing in
   `<vault>/.ovp/prompts.yaml` to send some percentage of traffic
   through the new version.

## Why versioned files instead of inline strings

* Diffs of prompt edits show up as readable text changes, not buried
  inside Python indentation noise.
* Frontmatter declares vocabularies / tunables / output schema so
  downstream tools (audit logs, fidelity replay, A/B comparison)
  can introspect without re-parsing the prompt body.
* A reviewer who isn't a Python engineer can edit prompts directly.
* `extraction_prompt_version` in evergreen frontmatter ties each
  output back to the exact prompt file that produced it, surviving
  arbitrary refactors of the calling code.

## Frontmatter schema

```yaml
---
prompt_name: absorb            # MUST match parent directory name
version: v2                    # MUST match filename stem
status: stable                 # stable | experimental | deprecated
schema_version: 2              # bump on breaking output schema change
created_at: 2026-05-05         # informational
created_by: chris              # informational
notes: |                       # informational
  multi-line description of what this version changes
output_schema:                 # optional — what shape the LLM emits
  wrapper_keys: [...]
  unit_keys: [...]
vocabulary:                    # optional — fixed-vocab fields
  unit_type: [fact, method, ...]
  ...
tunables:                      # optional — runtime parameters
  user_prompt_body_chars: 6000
  max_output_tokens: 8000
  ...
---

(prompt body here, fed verbatim to the LLM as system_prompt)
```

`prompt_name`, `version`, `status`, and `schema_version` are required;
the rest are advisory.

## Status semantics

- **`stable`** — the prompt callers default to in production.  At most
  one version of each prompt should be `stable` at a time.
- **`experimental`** — under evaluation; may be routed traffic via
  Phase 2's A/B framework when that lands, but never the production
  default.
- **`deprecated`** — kept on disk for replay / historical comparison
  but never loaded by production callers.  Callers that try to load a
  `deprecated` prompt get a runtime error to force the migration.

## Phase roadmap

This directory is **Phase 1** of the prompt-evolution architecture.
Phase 2-4 are deferred — see
`docs/plans/2026-05-05-prompt-ab-test-backlog.md` for the design
sketch and why we're not building them yet.
