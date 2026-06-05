# Stage D — PlanApplier v1

## Context

After C v1 + R2 + v1.1, the pipeline produces trustworthy `WritePlan`s but the system still stops at "proof of what we'd write." Stage D closes the dry-run loop: take a `WritePlan` and actually mutate a filesystem vault. This validates the load-bearing invariants — all real side effects go through `WritePlan`, are auditable, refusable, and replayable — that have been declared but never exercised.

PlanApplier comes before C9/C10 (live Anthropic + cassette capture) and before v1.2 (paper). Live LLM calls without an applier just produce more dry-run plans; paper without an applier just covers more dry-run shapes.

## Locked scope (from the conversation this turn)

**In scope:**
- Filesystem vault applier only.
- `VaultCreate` and `VaultUpdate` op kinds.
- `VaultCreate`:
  - Auto-create parent directories.
  - If target exists with the same content (SHA-256 matches `after_hash`) → idempotent success.
  - If target exists with different content → fail.
- `VaultUpdate`:
  - Target must exist.
  - Current content's SHA-256 must equal `before_hash`; otherwise reject.
  - On match, overwrite with new body.
- Path safety: every `VaultPath` is interpreted relative to a vault root. `..` segments, absolute paths, and components that escape the root are rejected before any I/O happens.
- `ApplyReport` records per-op outcome (Applied / Skipped / Failed / Unsupported) with a `reason` string.
- CLI: `ovp-next apply-plan --plan <json> --vault-root <dir> [--dry-run]`.
- Integration tests use `tempfile::tempdir`. Real vaults are never touched by CI.

**Out of scope (deferred):**
- `CanonicalUpsert` / `EventAppend` op kinds — return `Unsupported` explicitly.
- Canonical store, event log store (separate crates / stages later).
- Multi-op atomicity, rollback, transactions.
- Watch mode / continuous apply.
- Conflict resolution beyond "match or refuse."
- File locking — single-process assumed for v1.

## Architecture changes

### `ovp-core`

New module `ovp-core::applier` with **types only**:

```rust
pub trait PlanApplier {
    fn apply(&mut self, plan: &WritePlan, mode: ApplyMode) -> ApplyReport;
}

pub enum ApplyMode {
    Apply,
    DryRun,
}

pub struct ApplyReport {
    pub run_id: RunId,
    pub outcomes: Vec<OpOutcome>,
}

pub struct OpOutcome {
    pub op_id: OpId,
    pub kind: OpKind,
    pub result: OpResult,
}

pub enum OpKind {
    VaultCreate,
    VaultUpdate,
    CanonicalUpsert,
    EventAppend,
}

pub enum OpResult {
    Applied,
    Skipped { reason: String },     // dry-run, idempotent, etc.
    Failed { reason: String },
    Unsupported,
}
```

No I/O in core. `PlanApplier` is to writes what `ModelClient` is to LLM calls — a sync trait whose impls live elsewhere.

### `ovp-stores` (new crate)

Mirrors the `ovp-llm` shape: effect-boundary crate that owns the actual I/O.

```
crates/ovp-stores/
├── Cargo.toml         # deps: ovp-core, sha2; dev: tempfile
└── src/
    ├── lib.rs
    └── vault_fs.rs    # VaultFsPlanApplier
```

`VaultFsPlanApplier { vault_root: PathBuf }`. Implements `PlanApplier`. Self-contained; the runner doesn't know it exists.

### `ovp-cli`

New subcommand `apply-plan`:

```
ovp-next apply-plan
  --plan <path.json>                # serialized WritePlan from interpret-article
  --vault-root <dir>                # where to apply
  [--dry-run]                       # mode=DryRun
  [--report <path.json>]            # optional ApplyReport dump
```

Default mode: `Apply`. `--dry-run` flips to `DryRun` (every op records `Skipped { reason: "dry-run" }`).

## Invariant updates

- **Invariant #10** (writes only through WritePlan) is now enforceable end to end. Update the doc to mention `PlanApplier` as the single thing allowed to mutate `VaultStore`.
- **New invariant #13** (proposed): `PlanApplier` impls must validate paths before any I/O. Vault paths are relative; absolute paths and `..`-traversals are rejected with `Failed { reason: "path_escape" }`.
- Arch gate: no new grep can enforce path safety meaningfully — gate by review + the integration test.

## Implementation order

1. **D1** — this doc.
2. **D2** — `ovp-core::applier` types + trait. No impl, no I/O. Just compiles and re-exports.
3. **D3** — `ovp-stores` crate with `VaultFsPlanApplier`. Unit tests inside the crate cover path safety, hash matching, idempotence.
4. **D4** — CLI `apply-plan` subcommand. Loads the plan JSON, constructs the applier, prints the report.
5. **D5** — integration tests under `crates/ovp-stores/tests/`:
   - `create_new_file`
   - `create_idempotent_on_same_hash`
   - `create_fails_on_existing_different_content`
   - `update_succeeds_when_before_hash_matches`
   - `update_rejected_on_hash_mismatch`
   - `path_escape_rejected` (e.g. `../etc/passwd`)
   - `dry_run_writes_nothing`
   - `unsupported_op_kind_recorded`
6. **D6** — end-to-end acceptance under `crates/ovp-stores/tests/`:
   - Run the v1.1 article pipeline (via the same setup as `article_clean.rs`).
   - Take the `WritePlan`.
   - Apply to a tempdir.
   - Read the written file back, parse frontmatter, assert title / source / canonical_concepts / concept_candidates match what the pipeline produced.

## Verification gauntlet (post-D6)

```sh
cd ~/Documents/ovp-next
cargo test                                                  # all green
cargo clippy --all-targets --workspace -- -D warnings       # clean
bash scripts/check_architecture.sh                          # invariants hold

# Demo: interpret then apply.
cargo run -p ovp-cli -- interpret-article \
  --input fixtures/article_clean/input.md \
  --out .run/article \
  --cache-dir crates/ovp-domain/tests/cassettes
cargo run -p ovp-cli -- apply-plan \
  --plan .run/article/plans/demo-article.json \
  --vault-root .run/vault
# → .run/vault/20-Areas/AI-Research/Topics/2026-05/<...>.md exists
ls .run/vault/20-Areas/AI-Research/Topics/2026-05/
```

## What this does NOT do

- Write to a canonical store (`CanonicalUpsert` is `Unsupported`).
- Persist events to disk (`EventAppend` is `Unsupported`). Events still serialize via the CLI's existing `.run/events/*.jsonl` dump, but `PlanApplier` doesn't consume them.
- Roll back. A mid-plan failure leaves successful ops applied; subsequent ops are skipped with a "previous op failed" reason. No transactional atomicity.
- Lock the vault. Concurrent runs against the same vault root are undefined behavior in v1.
- Compute or refresh derived indexes. The vault writer doesn't know about MOC files, search indexes, or knowledge.db.

## What this unblocks

- C9 + C10 (live Anthropic + cassette capture): once an article is actually written to disk, "run on a fresh URL" becomes a real workflow.
- v1.2 (paper) and v1.3 (github): both produce WritePlans that need somewhere to land.
- Canonical store work: once vault writes are real, the next stage is making `CanonicalUpsert` real.
- Audit / replay: applying the same plan twice should be observably idempotent — a real test for the "all writes through WritePlan" invariant.

## Estimated cost

D2-D6 together: ~2-3 days at one-person + Agent pace. Most of the time is on D3 (path safety + hash matching edge cases) and D5 (covering them). D6 is a thin wrapper around the existing acceptance setup.
