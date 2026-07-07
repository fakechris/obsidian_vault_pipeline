# OVP Operator Runbook — the Rust daily workflow (M31)

How to run OVP Next day to day on the real vault, without legacy Python.
Product-state layout: [`product-state-layout.md`](./product-state-layout.md).

---

## 0. One-time setup

```bash
cd ~/Documents/obsidian-vault-pipeline
cargo build --release -p ovp-cli            # offline build (replay-only)
# live LLM reads (required for new content):
cargo build --release -p ovp-cli --features anthropic
# + live pinboard (optional):
cargo build --release -p ovp-cli --features anthropic,pinboard-live
alias ovp2=~/Documents/obsidian-vault-pipeline/target/release/ovp2
export VAULT=~/Documents/ovp-vault
```

Environment for live runs (put in your shell profile or `.env`, NEVER in the repo):

| Var | Used by | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` (+ optional `ANTHROPIC_BASE_URL`, `OVP_LLM_MODEL`, `OVP_LLM_MAX_TOKENS`, `OVP_LLM_NO_PROXY=1`) | `--client live` | see `docs/live-capture.md`; this sandbox/provider setup needs `OVP_LLM_NO_PROXY=1` |
| `PINBOARD_TOKEN` (`username:TOKEN`) | `--pinboard-live` / `pinboard-sync --live` | same env var the legacy processor used; never logged, never persisted |

## 1. The daily loop

```bash
ovp2 daily --vault-root "$VAULT" --client live
```

What one run does, in order:

1. **Pinboard capture** (only with `--pinboard-live` or `--pinboard-fixture <export.json>`):
   new bookmarks → notes in `50-Inbox/02-Pinboard/`, URL-deduped against
   `.ovp/pinboard-sync.jsonl` + the intake ledger.
2. **Intake sweep**: everything in `Clippings/`, `50-Inbox/00-Capture/`,
   `50-Inbox/02-Pinboard/` is normalized into
   `50-Inbox/01-Raw/<YYYY-MM>/<date>_<title>-<hash8>.md`.
   Duplicates (by content sha256 or URL) are parked under
   `50-Inbox/03-Processed/duplicates/` — moved, never deleted. Files too thin
   to read (< 200 chars body, e.g. bare bookmarks) are flagged
   `needs-content` and left where they are; enriching the file re-queues it
   automatically (the flag is per content-hash).
3. **Reader runs**: up to `--max-sources` (default 10) NEW sources go through
   the grounded reader trunk → packs in `40-Resources/Reader/`.
4. **Lifecycle**: each succeeded source moves to
   `50-Inbox/03-Processed/<YYYY-MM>/`.
5. **Audit + report**: every attempt appends to `.ovp/daily-runs.jsonl`;
   every write is logged to `60-Logs/pipeline.jsonl` *before* its success
   record; a per-run report lands in `.ovp/reports/<run_id>.json`.
6. **Refresh**: read model (`.ovp/index/index.json`) + console
   (`.ovp/console/index.html`) are rebuilt.

Open the console: `open "$VAULT/.ovp/console/index.html"`.

Useful variants:

```bash
ovp2 daily --vault-root "$VAULT" --dry-run            # plan only, writes nothing
ovp2 daily --vault-root "$VAULT" --client live --max-sources 3
ovp2 daily --vault-root "$VAULT" --client live --retry-blocked
ovp2 daily --vault-root "$VAULT" --no-intake          # reader phase only
ovp2 daily --vault-root "$VAULT" --no-lifecycle       # leave sources in 01-Raw
```

Exit codes: `0` clean; non-zero when any source failed (failures are in the
ledger and will be retried next run — nothing is lost).

## 2. Failures, retries, blocked sources

- A failed source (bad model JSON, truth-layer gate, transport) is recorded
  `failed` and **retried automatically** on the next run.
- After **3 failures** it becomes **blocked**: skipped, listed in the console
  Attention feed and in `find --kind sources --status blocked`. Fix the file
  (or the provider), then `--retry-blocked` on the next daily run.
- Lifecycle move failures are warnings, not failures: the pack is the
  product; the leftover raw file is harmless (dedup skips it forever).
- The ledgers are append-only. A malformed ledger line is a hard error by
  design — do not hand-edit; if you must intervene, archive the ledger file
  into `70-Archive/` and accept re-processing cost.

## 3. Pieces, run separately

```bash
ovp2 intake --vault-root "$VAULT" [--dry-run]          # capture sweep only
ovp2 pinboard-sync --vault-root "$VAULT" --fixture export.json [--dry-run]
ovp2 pinboard-sync --vault-root "$VAULT" --live        # needs pinboard-live build
ovp2 index --vault-root "$VAULT"                       # rebuild read model
ovp2 console --vault-root "$VAULT"                     # rebuild console (+index)
ovp2 find --vault-root "$VAULT" <term>                 # search everything
ovp2 find --vault-root "$VAULT" --kind sources --status needs_content
ovp2 find --vault-root "$VAULT" --kind claims --status durable
ovp2 find --vault-root "$VAULT" --kind cards "agent memory"
ovp2 find --vault-root "$VAULT" --kind units "verbatim quote"
ovp2 find --vault-root "$VAULT" --kind runs --date 2026-06
ovp2 ask --vault-root "$VAULT" --client live "What does the vault say about agent memory?"
ovp2 ask --vault-root "$VAULT" --client live --strict-ask "What evidence supports that?"
```

Pinboard without live credentials: export from <https://pinboard.in/export/>
(JSON) and use `--fixture`. The note format and dedup are identical to live.

`ask` uses the rebuilt evidence sidecar (`.ovp/index/evidence.json`) plus the
Crystal claim rows. It prints a verification summary such as
`verified citations: 2/2` after the answer. `--strict-ask` exits non-zero when
the answer has no citations or cites ids that were not supplied as evidence.
This is deterministic citation verification, not a semantic proof of every
sentence in the answer.

## 4. Crystal (durable claims) on the vault store

The vault-local store is `"$VAULT"/.ovp/crystal`. The gates are unchanged
(M22/M23); only the store location is a product convention now:

```bash
ovp2 crystal-lint  --candidate cand.json --packs-dir "$VAULT/40-Resources/Reader" \
    --strength verdicts.json --out /tmp/lint.json
ovp2 crystal-write --candidate cand.json --packs-dir "$VAULT/40-Resources/Reader" \
    --strength verdicts.json --store "$VAULT/.ovp/crystal" --title "…" --scope "…"
ovp2 console --vault-root "$VAULT"     # claims appear under Crystal · 结晶主张
```

`case_id` in candidate citations = the pack directory name under
`40-Resources/Reader/`. Daily packs already write `units.accepted.json` in
exactly the layout the gates expect.

Prepare a bounded review session for caveated claims:

```bash
ovp2 crystal-review-session \
  --vault-root "$VAULT" \
  --batch 20 \
  --out .run/review-session-$(date +%Y%m%d)
```

This writes `review-sheet.md`, `decisions.template.json`, and
`selected-claim-ids.txt`. The command does not decide durability and does not
write the Crystal ledger. Rewrites/splits must still carry full citations and
re-enter the normal strength gate + `crystal-write` path. `crystal-write`
preserves unprocessed `review.json` entries when new caveated claims are
written, so a small review batch no longer erases the rest of the queue.

## 5. Recovery / rebuild

Everything under `.ovp/index/` and `.ovp/console/` is a derived projection:
delete freely, rebuild with `ovp2 index` / `ovp2 console`.
Authoritative state = the ledgers (`.ovp/*.jsonl`), the reader packs, the
crystal store, and the vault notes themselves. Reports are append-only
snapshots. Cassettes (`.ovp/cassettes/`) are replayable model replies —
deleting them only costs re-recording.

## 6. What this does NOT do (yet / by design)

- No web-page fetching for bare bookmarks — enrich `needs-content` notes by
  hand (or with the clipper) and the next run picks them up.
- No GitHub/arXiv-specific enrichment; papers flow as ordinary articles.
- No daemon — run `daily` from cron/launchd if you want scheduling.
- No embeddings/SQLite/graph; `find` is substring search over the read model.
- Legacy `knowledge.db`, `/ops` UI, `ovp-ask`/digest remain Python-only.
