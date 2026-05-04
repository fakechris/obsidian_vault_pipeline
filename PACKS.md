# OVP Packs

> Architecture index: [README](./README.md) | [ARCHITECTURE](./ARCHITECTURE.md) | [RUNTIME](./RUNTIME.md) | **PACKS** | [PRODUCT_SURFACES](./PRODUCT_SURFACES.md) | [GLOSSARY](./GLOSSARY.md)
>
> **This file explains:** the Core / Domain Pack / Workflow Profile distinction, how packs are discovered, and what a pack owns.
> **This file does not explain:** the state model (see [ARCHITECTURE](./ARCHITECTURE.md)) or how stages run (see [RUNTIME](./RUNTIME.md)).

---

## The three roles

| Role | Owns | Examples |
| --- | --- | --- |
| **Core** | the Canonical State / Projection / Access Surface / Governance contracts; runtime stage definitions; identity model; evidence + audit primitives | every module under `src/ovp_pipeline/` that is not under `packs/` |
| **Domain Pack** | semantics for one knowledge domain — object kinds, relation types, extraction prompts, projection recipes | `packs/research_tech/`, `packs/default_knowledge/` |
| **Workflow Profile** | which subset of stages and which pack participate in a given run | `--profile full`, `--profile autopilot`, `--profile incremental` |

The first standard built-in domain pack is `research-tech`; `default-knowledge` is retained as a compatibility pack for vaults that haven't migrated.

## Pack contract

A Pack is anything that:

1. Registers itself via the `ovp.packs` Python entry-point group, **or** is listed in `OVP_PACK_MANIFESTS`.
2. Implements the `BaseDomainPack` interface (object kinds, relation types, extraction profile, projection recipes).
3. Cannot bypass the Core boundary — Pack-defined extraction emits Candidates, not Canonical State writes.

```python
# pyproject.toml of a third-party pack
[project.entry-points."ovp.packs"]
my-domain = "my_pack:get_pack"
```

```bash
export OVP_PACK_MANIFESTS=/path/to/manifest.toml
ovp-packs                # list discovered packs
ovp --pack my-domain --profile full
```

## Pack vs Profile

A Pack defines **what** the domain knows; a Profile selects **how much of the runtime** to use against that domain.

| Profile | Stages run | Typical use |
| --- | --- | --- |
| `full` | Ingest → Interpret → Absorb → Refine → Normalize → Derive | scheduled batch / first-time vault |
| `incremental` | Ingest → Interpret → Absorb → Derive | recent inbox |
| `autopilot` | Absorb → Normalize → Derive (live) | daemon |

A Profile cannot define new pack semantics. It can only restrict which stages run.

## What a Domain Pack does NOT own

- ❌ Canonical State (Core owns the trust boundary).
- ❌ Governance subaxes (Promotion / Review / Audit / Repair belong to Core).
- ❌ Stage definitions (the six runtime stages are Core).
- ❌ The pack registry contract itself.

Domain Packs are read by Core; they configure but do not replace it. A Pack adding a new object kind never lets that kind escape Governance — promotion still goes through `Core.promotion_policy`.

## Adding a new pack

See `docs/pack-api/` for the developer guide. Quick checklist:

1. Implement `BaseDomainPack.object_kinds()` returning your kinds.
2. Implement `BaseDomainPack.relation_types()` with source/target kind constraints.
3. Implement `BaseDomainPack.extraction_profile()` for your domain's LLM prompts.
4. Register via `ovp.packs` entry point.
5. `ovp-doctor --pack <your-pack> --json` should pass.

If your pack wants to write to Canonical State, it must do so through Governance. Bypassing Governance is not a pack — it is a bug.

## Default pack

`default-knowledge` exists as a compatibility pack so older OVP vaults continue to work. New vaults should select an explicit standard pack like `research-tech` (or a third-party domain pack). Over time, `default-knowledge` will narrow to a thin compatibility shim and the explicit pack model becomes the default expectation.
