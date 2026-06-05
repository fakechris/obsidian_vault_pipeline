# Stage: L4 Operational Workflow Layer (`run-cycle`)

> Status: design + first implementation. Adds the `ovp-run` crate and a
> `run-cycle` CLI command that drives one full ingest→apply→rebuild-derived
> cycle from a single command, idempotent on re-run.

## The problem

Every piece of the pipeline now exists — assembly (L2), the domain nodes (L1),
the appliers (L3) — but nothing wires them into **one operation**. To take an
inbox file all the way to a coherent vault, a caller today has to: read a
manifest, build wiring, assemble, run, apply the plan through a composite
applier, then separately read the canonical store, rebuild the MOC, scan
backlinks, rebuild the knowledge index, and apply each — exactly the sequence
duplicated across the `*_e2e` tests. That orchestration is the operational
workflow, and it has no home. Until it does, autopilot/query/lint would each
reinvent it.

## The concept: `RunCycle`

One public noun. A `RunCycle` executes the full cycle and returns a
`RunCycleReport`. Supporting types: `RunCycleInputs` (what to run),
`RunCycleReport` (what happened), `RunCycleError` (what stopped it before a
report could exist), and `DerivedRebuild` (a one-artifact rebuild summary). No
others.

## Crate placement (decision)

**New crate `ovp-run` = L4.** Not folded into `ovp-app` (L2). Rationale:

- It keeps the **crate↔layer mapping 1:1** (the north star in `architecture.md`):
  `ovp-app` stays L2 = *assembly only* and never depends on `ovp-stores` (L3); the
  operational workflow is precisely the thing that wires L2 + L3 together.
- Clean, acyclic dependency graph: `ovp-cli → ovp-run → {ovp-app, ovp-stores,
  ovp-domain, ovp-core}`. `ovp-run` does **not** need `ovp-llm` (the live
  `ModelClient` is built by the CLI and injected via `AppWiring`).

This is the offered fallback over the "put it in ovp-app" recommendation; chosen
to preserve the documented L2 purity boundary. Trivially reversible if we'd
rather merge it.

## CLI verb (decision)

**`run-cycle`**, not `process-inbox`. The command does more than process the
inbox: it also rebuilds derived state (MOC + knowledge index) — a full *cycle*.
`process-inbox` would undersell the derived-rebuild half. `ovp-cli` stays a thin
shell: it parses args, builds the `ModelClient` + `ConceptRegistry` + paths +
`AppWiring`, calls `RunCycle::execute`, prints the report, and (only if asked)
writes the report JSON.

## Flow

```
run-cycle
  ├─ DomainPipelineSpec::parse(manifest)        # CLI
  ├─ build ModelClient (replay|live)            # CLI
  ├─ build ConceptRegistry (load | default)     # CLI
  ├─ AppWiring{run_id, date, area, input, client="default_llm", registry="default"}  # CLI
  └─ RunCycle::execute(inputs)                   # ovp-run (L4):
        1. GraphAssembler::assemble(spec, wiring)         → runner   (no writes if this fails)
        2. runner.run()                                   → RunReport (no apply if this fails)
        3. CompositePlanApplier(Vault + Canonical).apply(plan)   → main ApplyReport
           └─ if any op FAILED: stop. Do not rebuild derived state.
        4. CanonicalFsStoreApplier::read_all → CanonicalConcept::try_parse_pairs (strict)
           └─ if parse fails: stop. Do not write MOC/index.
        5. MocBuilder.plan_rebuild(concepts, current_moc)        → VaultFsPlanApplier.apply
        6. walk_markdown(vault) − the MOC itself → backlinks; KnowledgeIndex::build;
           KnowledgeIndexBuilder.plan_rebuild(index, current)   → VaultFsPlanApplier.apply
        7. RunCycleReport { graph summary, main apply, moc, knowledge_index }
```

Backlink scanning **excludes the MOC file** — the MOC is a derived index that
wikilinks every concept; counting it as a backlink source would be noise and
would make the index depend on MOC-rebuild order. Excluding it keeps the index
correct and order-independent.

## Idempotence

Second run against the same vault + canonical roots produces **no semantic
changes**: the article note / evergreen stubs / canonical records already exist
with matching hashes → the main apply reports every op `Skipped`; the MOC and
knowledge index are unchanged → `plan_rebuild` returns empty plans. So
`report.apply.applied == 0`, `moc.applied == 0`, `knowledge_index.applied == 0`
on the second run.

## Failure behavior (loud, fail-closed)

| Failure | Result | Writes |
|---|---|---|
| assembly fails | `Err(RunCycleError::Assemble)` | none |
| graph run errors | `Err(RunCycleError::GraphRun)` | none (plan never applied) |
| main apply not clean — any **failed OR unsupported** op | `Ok(report)` with `derived_skipped_reason` set, `moc`/`index` = `None` | whatever the composite applied before a halt; **no derived state** |
| canonical strict parse fails | `Ok(report)` with `derived_skipped_reason` set, `moc`/`index` = `None` | main plan applied; no MOC/index written |
| vault backlink scan I/O error | `Ok(report)` with `derived_skipped_reason` set, `moc`/`index` = `None` | main plan applied; no MOC/index written |
| derived rebuild has a failed op | `Ok(report)` with `moc`/`index` carrying `failed > 0` | partial (derived only; rebuildable) |

**Unsupported is treated as failure.** A `WriteOp` no applier handled (e.g. an
`EventAppend` once a producer exists, with no event applier wired) is not a
silent success — it blocks the derived rebuild and fails `succeeded()`, exactly
like a `Failed` op. A "completed operational cycle" means *every* emitted op was
applied.

**All derived reads happen before any derived write.** The canonical read+parse
and the backlink scan complete, and *both* rebuild plans are built, before the
MOC or the index is applied — so a read failure leaves zero partial derived
state.

`RunCycleReport::succeeded()` is true only when nothing failed, nothing was left
unsupported, and nothing was skipped-for-failure. The CLI exits non-zero when
`!succeeded()` and prints the reason, so failures are loud.

## `--dry-run`

Uses `ApplyMode::DryRun` for every apply (main + MOC + index): nothing is
written; the report shows what *would* happen and sets `RunCycleReport.dry_run =
true`. **Semantics, pinned:** dry-run is a **preview, not a simulation**. Because
the main apply's canonical writes are not performed, the derived-rebuild previews
reflect the **current on-disk** canonical store + vault, NOT a speculative "as if
the main plan had applied" state. (On a fresh vault, a dry-run therefore previews
empty/placeholder derived artifacts even though a real run would populate them.)

## Boundaries held

- No direct file writes except through a `PlanApplier` — the sole exception is
  the CLI writing the final report JSON when `--report` is given (in `ovp-cli`,
  not `ovp-run`).
- `ovp-run` carries no `ovp-core` domain knowledge it shouldn't; it composes L1–L3.
- No subprocess to legacy Python; no async; default tests need no network/API key
  and never touch a real vault (tempdirs only).

## Acceptance tests (`ovp-run/tests`)

1. **article idempotence**: temp vault + temp canonical + `article_clean` → run-cycle
   writes the article note, 13 evergreen stubs, 13 canonical records, MOC, and
   knowledge index; strict canonical parse succeeds; a second run is idempotent
   (`applied == 0` everywhere).
2. **paper smoke**: run-cycle with `unified` manifest + `paper_arxiv` writes a
   paper note; derived artifacts stay consistent.
3. **dry-run writes nothing**: `--dry-run` over `article_clean` leaves both roots
   empty and reports `dry_run == true`.
4. **failure: bad manifest** (unknown kind) → `Err(Assemble)`, no writes.
5. **failure: corrupt canonical before rebuild** → derived rebuild is skipped
   loudly (`derived_skipped_reason` set, `succeeded() == false`) and the existing
   MOC/index are not overwritten.
6. **unit: unsupported = failure** — `main_apply_block` flags both `Failed` and
   `Unsupported` main-apply ops (so derived rebuild is skipped), and
   `succeeded()` returns false for an unsupported main op. (A full e2e producing
   an unsupported op awaits a `WriteOp` producer no applier handles — e.g. an
   `EventAppend` source — which does not exist yet.)
