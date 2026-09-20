# Product-State Layout (M31)

Where the Rust daily workflow keeps state, what is authoritative, and what is
derived. Everything lives **in the vault** (never in the repo, never `.run/`).

```text
<vault>/
├── Clippings/                      capture: Obsidian Web Clipper drops (swept by intake)
├── 50-Inbox/
│   ├── 00-Capture/                 capture: manual drops (swept)
│   ├── 02-Pinboard/                capture: pinboard-sync materializes bookmark notes here (swept)
│   ├── 01-Raw/<YYYY-MM>/           QUEUE: normalized sources awaiting a reader run
│   ├── 03-Processed/<YYYY-MM>/     lifecycle: sources that produced a pack
│   └── 03-Processed/duplicates/<YYYY-MM>/   parked duplicate captures (moved, never deleted)
├── 40-Resources/Reader/<YYYY-MM-DD>_<title>-<hash8>/
│   ├── reader.html / reader.md     the human reading surface (provenance intact)
│   ├── cards.json / units.accepted.json / run-status.json
│   └── model-reply.*.txt           raw model replies (audit)
├── 60-Logs/pipeline.jsonl          vault-wide write log (OVP_RULES; shared with legacy events)
└── .ovp/
    ├── daily-runs.jsonl            LEDGER: one record per reader attempt   [authoritative]
    ├── intake.jsonl                LEDGER: one record per capture disposition [authoritative]
    ├── pinboard-sync.jsonl         LEDGER: one record per materialized bookmark [authoritative]
    ├── reports/<run_id>.json       per-run report (append-only, collision-suffixed)
    ├── crystal/                    durable Crystal store (ledger.jsonl + crystal.md + review.json)
    ├── cassettes/daily/            recorded model replies (replayable; deletable at re-record cost)
    ├── index/index.json            DERIVED read model (rebuild: `ovp2 index`)
    └── console/index.html          DERIVED console     (rebuild: `ovp2 console`)
```

## Rules

- **Authoritative**: the ledgers, the reader packs, the crystal store, and the
  notes themselves. All ledgers are append-only; a malformed line is a hard
  error (never silently skipped).
- **Derived**: `index/` and `console/` are overwritten projections — deleting
  them loses nothing; a full rebuild is the migration story (no schema
  migrations needed; `ovp.index/v1` is regenerated, never edited).
- **Identity**: a source = sha256 of its bytes; URL is a secondary dedup key
  at the capture boundary. Renames/moves never reprocess; edits re-queue.
- **Self-description**: every artifact names its own source, so nothing has to
  be reconstructed from a join that cannot be checked.
  - `SourceRow.capture_path` — the intake `from`, i.e. where the content was
    FIRST seen, before the sweep normalized it into `01-Raw`. Distinct from
    `rel_path`, which is the current location; neither derives from the other.
    First intake record wins (a re-ingest is not a new origin).
  - `run-status.json` — `source_url`, `source_sha256`, `source_rel_path`
    alongside the existing `source` title. Packs outlive index rebuilds and
    are the artifact people open, so they carry the answer themselves. Absent
    (null) when genuinely unknown, e.g. `read-source --render-only`, never
    guessed.
  - `ovp2 doctor`'s `pack-provenance` compares the pack's own `source_sha256`
    with the sha `index.json` joined to that `pack_dir`. Before this there was
    one link from a claim back to its source and no second opinion to check it
    against.
- **Audit ordering**: write → `pipeline.jsonl` event → ledger record. A
  recorded success therefore always has its write-log entry; a crash can at
  worst duplicate an event, never lose one.
- **Never delete / never overwrite**: lifecycle transitions are `rename` with
  collision suffixes (` -2`, ` -3`, …); duplicates are parked, not removed.
- **Schemas**: `ovp.daily/v1`, `ovp.intake/v1`, `ovp.pinboard/v1`,
  `ovp.daily.run-report/v1`, `ovp.index/v1` — additive evolution only
  (serde-default new fields), version bump on breaking change. The SQLite
  shadow (`read-model.sqlite`) is NOT covered by that rule: it is derived and
  disposable, so any DDL change bumps its `schema_version` and the next
  `ovp2 index` rebuilds it (currently v7).
