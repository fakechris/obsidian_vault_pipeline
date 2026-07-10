# Stage M30 — The Blessed Daily Loop (`ovp-next daily`)

**Type:** First daily-workflow product slice toward Level 2 (mainline-default).
**Builds on:** M29 audit (`stage-m29-mainline-return-audit.md`) — Level 2 was blocked on
*daily product coverage*, not code health.

---

## What shipped

One command makes the validated reader trunk usable on the real vault every day:

```
ovp-next daily --vault-root ~/Documents/ovp-vault --client live
```

Per run:

1. **Plan** — recursively scan the inbox (`50-Inbox/01-Raw` by convention,
   `--inbox` to override), sha256 every `.md`, and skip content that has EVER
   succeeded (plus same-run duplicate content). Dedup identity is the file
   *bytes*, so renames/moves never reprocess and editing a source re-queues it.
2. **Run** — each new source goes through the unchanged M17–M20 reader trunk
   (Grounded Units v5 → Critic Repair v1 → Reader Cards v3 → Reader Pack), now
   shared as `ovp_domain::reader::pipeline::run_reader_pipeline` between
   `read-source` and the daily loop. Truth-layer gates are identical.
3. **Write (vault-local product surface)** — packs land at
   `40-Resources/Reader/<YYYY-MM-DD>_<title>-<hash8>/` *inside the vault*
   (date-stamped per `OVP_RULES.md`; hash-suffixed so same-title sources never
   collide). `.run` remains diagnostic-only.
4. **Record (durable, append-only)** —
   - `.ovp/daily-runs.jsonl`: one `ovp.daily/v1` record per ATTEMPT
     (succeeded/failed, source path + sha256, pack dir, unit/card counts,
     failure reason). This is the dedup authority and the audit trail; a
     malformed line is a hard error, never skipped.
   - `60-Logs/pipeline.jsonl`: one write-log event per pack write — the
     `OVP_RULES.md` "log every write operation" contract, honored by Rust for
     the first time.
5. **Fail loud, continue** — a per-source failure is recorded (and retried next
   run because only `succeeded` hashes dedup); the run exits non-zero via the
   gate error. Config errors (unreadable ledger, missing inbox, client factory)
   abort immediately.

Safety rails: `--dry-run` plans without writing; `--max-sources` (default 10,
`0` = unlimited) is the OVP_RULES rate limit on LLM loops; replay is the default
client, `--client live` is the explicit real-API opt-in; cassettes default to
`<vault>/.ovp/cassettes/daily` (vault-local, never in the repo). The loop never
moves/deletes inbox files — content-hash dedup makes moving unnecessary.

## Why this slice

Of the five M29 Level-2 P0s it covers four in one reviewable change:

| M29 P0 | M30 status |
|---|---|
| Blessed daily write path (needs-decision) | **Decided + shipped**: reader/crystal trunk *is* the output surface — no M7–M13 revival |
| Stable vault-local product output | **Shipped**: `40-Resources/Reader/` |
| Durable run ledger / audit | **Shipped for the blessed path**: append-only JSONL ×2 (in-memory `EventLog` untouched elsewhere) |
| Source lifecycle / dedup | **Seeded**: content-hash dedup, no file movement (L0→L4 moves deliberately deferred) |
| Real intake (Pinboard/clippings) | Not this slice — `daily` consumes whatever capture drops in `50-Inbox/01-Raw` |

## Architecture

- New crate `ovp-daily` (L6, like `ovp-auto`): scan + ledger + loop. Depends on
  `ovp-domain` + `ovp-llm` only; imports the reader trunk and **none** of the
  demoted substrate (referents/canonical/moc/evergreen/knowledge_index).
  Added to the `check_architecture.sh` eval fence.
- `ovp_domain::reader::pipeline` extracted from the `read-source` command so
  the fail-loud sequence cannot drift between callers; `read-source` is now a
  thin shim with byte-identical behavior/messages.
- `VaultLayout` (the single path-conventions authority) gained
  `reader_pack_dir` / `daily_ledger` / `pipeline_log` / `daily_cassette_dir`.
- `ovp-cli` gained one thin command file; no logic in `main.rs`.

## Non-goals (unchanged)

No Referent/graph/RAG revival; no SQLite/FTS/embeddings (the ledger is JSONL —
revisit only if daily query pain proves otherwise); no KMEM inspection; no
legacy command-name parity; no inbox file movement; no daemon.

## Next

- Real intake (Pinboard/clippings → `50-Inbox/01-Raw`) so `daily` has a feed.
- Optional: Crystal candidate authoring over accumulated daily packs
  (`packs_dir` already matches `crystal-lint`'s expectations via
  `units.accepted.json`).
- Console refresh reading the daily ledger instead of `.run` artifacts.
