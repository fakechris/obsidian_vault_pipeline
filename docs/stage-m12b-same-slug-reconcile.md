# Stage M12b — Same-Slug Existing-Evergreen Reconcile

> **Status: landed (minimal).** Closes the one mainline risk M12a introduced: a
> concept slug surfaced by a *second* article must enrich its evergreen note,
> not fail the run. This is the narrow "don't break the main flow on a repeat
> concept" closed loop — not full absorb (no semantic dedup, no policy lanes, no
> crystals; those stay future).

## The problem M12a left open

M12a renders a **grounded, per-document** evergreen body (definition + claims +
source link + related). Two distinct articles surfacing the same slug therefore
render *different* bodies. The vault applier is fail-loud by design: a
`VaultCreate` whose target already exists with a different hash is
`OpResult::Failed`, which halts a `CompositePlanApplier` cycle. Repeat concepts
(`rag`, `ai-agent`, `evaluation`, …) are common in a real inbox, so M12a alone
would frequently halt a batch / `auto-run` on the second document — turning
"cross-document merge not yet implemented" into an active mainline failure.

## What M12b does

A **reconcile step in `RunCycle::execute`, before the main apply**, rewrites the
freshly-emitted plan against the current on-disk state **and the ops already
folded earlier in the same plan**. It owns no domain knowledge of the note
format — that lives in `ovp_domain::reconcile_evergreen_write` (a pure
function); the run layer only reads disk, threads in-plan state, and routes ops.

The in-plan state matters because a single run's `WritePlan` can already carry
two `VaultCreate`s for the same new slug — if a multi-document source (e.g.
`InboxScanSource`) ever feeds the evergreen-minting topology, or one document
surfaces a duplicate candidate. The reconcile tracks, per evergreen path, the
body that will be on disk after the ops emitted so far, and reconciles the next
same-path op against that (first mints, second folds to an `EnrichExisting`
`VaultUpdate`); a `CanonicalUpsert` whose key was already registered earlier in
the plan is likewise dropped. So the collision is handled whether the first
write was in a prior run **or earlier in this one**.

Per minted evergreen `VaultCreate` (path under `10-Knowledge/Evergreen/`):

| On disk | Decision |
|---|---|
| absent | **MintNew** — keep the `VaultCreate`. |
| present, same hash | keep the `VaultCreate` (the applier idempotent-skips). |
| present, different | **EnrichExisting** — parse both notes, union claims / sources / related (keep the first note's definition), emit a `VaultUpdate` guarded by the on-disk `before_hash`. |
| present, nothing new to add | **skip** (drop the op) — idempotent enrich. |
| present, not our format | **skip** — never clobber an unknown note. |

Per `CanonicalUpsert`: if the store already holds that key with a **different**
payload (a different document re-registering the same identity), the upsert is
**dropped** (first-writer-wins — the original provenance is preserved). A new
identity, or an identical re-register, passes through unchanged.

Everything else (the article/paper note `VaultCreate`, new identities) passes
through, so the same-input idempotent re-run is byte-for-byte preserved.

## Why this placement / these boundaries

- **Merge is domain logic, in `ovp-domain`.** `EvergreenNote` is the single home
  of the note format: `from_concept` → `render` (byte-identical to M12a's
  `render_rich`), `parse` (its inverse for our own output), `merge` (a
  deterministic, order-stable, capped union). `reconcile_evergreen_write` returns
  the `WriteOp` to apply (or `None` to skip), computing the `before_hash` /
  `after_hash` itself.
- **Reconcile is orchestration, in `ovp-run` (L4).** It reads the vault note +
  canonical store — the same state L4 already reads for derived rebuilds — and
  routes ops. It adds **no** domain knowledge beyond "an evergreen path lives
  under `VaultLayout::evergreen_dir()`".
- **`ovp-stores` stays domain-blind.** No applier changed. The reconcile sits in
  *front* of the appliers; the raw applier's fail-loud-on-conflict is unchanged
  as the vault backstop.
- **Fail-closed on the canonical read.** A `CanonicalUpsert` carries
  `before_hash: None`, so the canonical applier has no concurrency guard — the
  reconcile *drop* is the only thing protecting an existing identity's
  provenance. If the canonical store can't be read (I/O / non-UTF-8 corruption),
  the reconcile returns `Err` and the run-cycle applies **nothing** and reports
  loudly, rather than fall back to an empty view and blind-overwrite — the same
  fail-closed posture the derived rebuild uses for that read.
- **Idempotent.** The body is a pure function of the merged structure, and a
  re-merge of an already-merged note adds nothing → skip. Three runs over the
  same inputs converge after the second.

## What is still NOT done (deliberately)

- **Concept-specific definitions** — the definition is still the article-level
  `one_liner`; first-writer-wins keeps the first.
- **Semantic dedup** — claims are unioned by exact string, not by meaning;
  near-duplicate claims from different articles can coexist (capped).
- **Policy lanes** (mint / enrich / escalate / reject) — minting is still
  AUTO-all.
- **Canonical provenance *merge*** — the canonical record keeps a single
  (first) provenance; multi-provenance lives only in the note body's `## Source`.
- **Crystal materialization.**

These are M12b+/M13, and should land before RAG v1.1 (the semantic ranker).

## Tests

- `ovp-domain` (`evergreen_note.rs`): `parse`↔`render` round-trip; `merge`
  unions + first-writer-wins definition + idempotent-on-subset; the four
  `reconcile_evergreen_write` branches (mint / keep-identical / enrich / skip)
  plus unparseable-skip.
- `ovp-stores` (`evergreen_e2e.rs`): reconcile enriches the same slug across two
  documents (MintNew → EnrichExisting `VaultUpdate` → idempotent skip), both
  sources + claims present; the raw applier still rejects a directly-conflicting
  `VaultCreate` (the backstop).
- `ovp-run` (`run_cycle_e2e.rs`): a run-cycle over a pre-existing
  different-grounding note for the same slug **succeeds and enriches** (pre-M12b
  it would `Failed`/halt), and a third run is idempotent.
- `ovp-run` (`reconcile_same_slug` unit tests): two same-slug documents folded
  within one plan (mint → in-plan enrich `VaultUpdate`, duplicate canonical key
  dropped); a conflicting on-disk `CanonicalUpsert` dropped while a new identity
  is kept; and a corrupt canonical store **fail-closes** the reconcile.
