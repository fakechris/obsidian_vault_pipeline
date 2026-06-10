# Stage M31 — Mainline Capability Closure: Capture → Daily Workflow → Index → Console

**Type:** Multi-phase implementation epic (the Level-2 push after M29's audit and M30's first slice).
**Commits:** `770dbd6` (implementation) + `754011e` (adversarial-review fixes).
**Companions:** [`operator-runbook.md`](./operator-runbook.md) ·
[`product-state-layout.md`](./product-state-layout.md) ·
[`mainline-return-matrix.md`](./mainline-return-matrix.md) (updated).

---

## What shipped

One operator command now runs the whole daily workflow on the real vault,
without legacy Python:

```text
pinboard capture (trait-gated)        ovp-next daily --vault-root $VAULT --client live
        │
Clippings / 00-Capture / 02-Pinboard
        │  intake sweep: normalize names, URL+sha256 dedup, park duplicates,
        ▼  flag thin/broken files (per content hash, in place)
50-Inbox/01-Raw/<YYYY-MM>/
        │  plan: hash dedup vs ledger; 3 failures ⇒ blocked; --max-sources cap
        ▼
grounded reader trunk (M17–M20, unchanged) ──▶ 40-Resources/Reader/<pack>/
        │  Succeeded record durable FIRST, then lifecycle move
        ▼
50-Inbox/03-Processed/<YYYY-MM>/  +  .ovp/reports/<run_id>.json
        │
        ▼
.ovp/index/index.json (read model) ──▶ .ovp/console/index.html (bilingual console)
                                  └──▶ ovp-next find (list/search/filter)
```

New crates: **ovp-intake** (capture boundary + shared vault-state primitives),
**ovp-index** (deterministic JSON projection), **ovp-console** (Rust bilingual
console — the M28 Python console's product successor). **ovp-daily** extended
(retry cap, lifecycle, run report, audit ordering). CLI gained
`intake` / `pinboard-sync` / `index` / `find` / `console`, and every command is
labeled **PRODUCT** / **DIAGNOSTIC** / **DEMOTED** in `--help`.

## Key decisions

1. **Blessed path settled**: reader/crystal IS the daily output surface
   (M29's needs-decision #3). No M7–M13 revival; the arch gate now also bans
   demoted-substrate imports (incl. root re-exports) in the product crates.
2. **Read index = JSON projection, not SQLite** (M29 needs-decision #5).
   Full rebuild is milliseconds at vault scale, diffable, greppable, cannot
   become a hidden truth source, and rebuilding IS the migration story.
   `read_index` is the boundary where SQLite/FTS would slot in **if** daily
   query pain ever proves the need — not because legacy had `knowledge.db`.
3. **Audit ordering** (operator-grade durability):
   write → `pipeline.jsonl` event (key `event_type`, legacy-compatible) →
   ledger record; and the **Succeeded record becomes durable before the
   lifecycle move** — a crash can duplicate an audit event but can never lose
   a record or orphan a source. Proven by fault-injection tests.
4. **Failure semantics**: failed sources retry automatically; 3 failures ⇒
   `blocked` (surfaced on the console + `find`, exit-message guidance,
   `--retry-blocked` override). Per-source failures never abort a run; only
   config/audit-IO errors do. `.ovp/run.lock` makes mutating commands
   single-instance.
5. **Pinboard adapter boundary**: `PinboardFetch` trait; JSON-export fixture
   impl always compiled; live HTTP behind `pinboard-live` feature
   (`PINBOARD_TOKEN` env — the legacy variable — never logged/persisted,
   reqwest errors stripped `without_url`). Bare bookmarks materialize as
   notes and surface as `needs_content` until enriched — no hidden web
   fetcher.
6. **Crystal product home**: `<vault>/.ovp/crystal` (gates unchanged).
   The console/index read `ledger.jsonl` (append-only truth) + `review.json`
   (derived latest-state render, like `crystal.md`).

## Verification

| Gate | Result |
|---|---|
| `cargo test --workspace` | **603 passed / 0 failed** (M30 baseline: 573) |
| `cargo clippy --workspace --all-targets -- -D warnings` | clean |
| `bash scripts/check_architecture.sh` | all checks pass (incl. 2 new M31 gates) |
| Binary e2e (`crates/ovp-cli/tests/m31_e2e.rs`) | full vertical: pinboard fixture → intake → daily (replay over pre-seeded cassettes) → lifecycle → report → index → console → `crystal-write` into the vault store → `find`; idempotent rerun; failure → retry ×3 → blocked → `--retry-blocked` |
| Real-vault dry-run | 29 clippings ready to ingest, 0 mangled, nothing written |

Adversarial review: 5-lens workflow (correctness / architecture / safety /
coverage / operator-UX) over the implementation commit; 2 high + 9 medium
findings confirmed and fixed in `754011e` (see commit message); safety lens
re-verified inline (token paths, delete/overwrite audit, path sanitization).

## Known limitations (deliberate)

- No web-page fetching for bare bookmarks; no GitHub/arXiv enrichment; no
  daemon (cron the `daily` command); no embeddings/FTS (substring `find`).
- `review.json` is a latest-state render: caveated claims are rebuildable
  from the candidate+verdict inputs, not from an append-only vault ledger.
  Future: fold caveated outcomes into the crystal ledger as events.
- An intake crash between move and ledger append loses that one URL's dedup
  memory (content-hash dedup still holds); a sweep backfill could heal it.
- Shared vault-state primitives (`vaultops`) live in `ovp-intake` and are
  consumed by ovp-daily/index — a deliberate layering shortcut, documented
  here; promote to a leaf crate if a cycle ever threatens.
- `pipeline.jsonl` written by Rust uses `event_type` (legacy key); the brief
  M30 window used `event` — no real-vault events were written in that window.

## Level-2 scorecard (M29 P0s)

| M29 P0 | M31 status |
|---|---|
| Real intake (clippings + pinboard + normalize + URL dedup) | **Shipped** (web-clipper dir + manual capture + pinboard adapter) |
| Source lifecycle file movement | **Shipped** (capture → raw → processed/duplicates, never delete/overwrite) |
| Blessed daily write path | **Shipped + decided** (reader/crystal as output surface) |
| Durable run ledger / audit | **Shipped** (3 append-only ledgers + reports + write log + ordering proofs) |
| Persistent read index | **Shipped + decided** (JSON projection; SQLite consciously deferred) |

Remaining before **Level 2** (Rust as default development line): 1–2 weeks of
real daily dogfood on the operator vault with `--client live` (the 29 queued
clippings are the first feed), and triage of what surfaces. Remaining for
**Level 3** (delete Python) unchanged: knowledge.db-equivalent projections for
ops/doctor, Reuse surfaces (`ovp-ask`/digest/working-memory), web-UI/MCP
decisions, migration sign-off — see the matrix.
