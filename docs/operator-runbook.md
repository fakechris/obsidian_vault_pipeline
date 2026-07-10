# OVP Operator Runbook — the Rust daily workflow (M31)

How to run OVP2 day to day on the real vault, without legacy Python.
Product-state layout: [`product-state-layout.md`](./product-state-layout.md).

---

## 0. One-time setup

Prebuilt binaries (curl installer or `brew install fakechris/ovp2/ovp2`, see
[`install.md`](./install.md)) ship with all live features compiled in — if
you installed one, skip the builds below and use `ovp2` directly. From a dev
checkout:

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
| `OVP_LLM_TIMEOUT_SECS=480` | `--client live` | REQUIRED for dogfood: the default 180s total-request timeout mis-kills slow provider responses and amplifies load with retries (2026-07-06 live hang was exactly this class) |
| `PINBOARD_TOKEN` (`username:TOKEN`) | `--pinboard-live` / `pinboard-sync --live` | same env var the legacy processor used; never logged, never persisted |

## 1. The daily loop

```bash
OVP_LLM_NO_PROXY=1 OVP_LLM_TIMEOUT_SECS=480 \
  ovp2 daily --vault-root "$VAULT" --client live --date "$(date +%F)"
```

Two traps this exact line avoids (both bit real dogfood runs on 2026-07-06):

- **`--date "$(date +%F)"` is not optional.** The internal default date is UTC:
  a morning run (before 08:00 UTC+8) stamps YESTERDAY, an evening run (after
  16:00 UTC+8) stamps TOMORROW — the ledger really did record `2026-07-07`
  entries on the local evening of 07-06. Always pass the local date; ledgers
  are append-only, mis-stamped entries are never rewritten (note them in
  `.run/dogfood/issues/` instead).
- **A plain `cargo build` binary is replay-only.** `--client live` needs the
  `--features anthropic` release build from §0; the replay-only binary fails
  live with a clear error, but only after you've waited on it.

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
7. **Portal**: open the web portal to review the day —

```bash
ovp2 serve --vault-root "$VAULT"        # default 127.0.0.1:3141
open http://127.0.0.1:3141/
```

The portal SPA is served from the vault's deployed `.ovp/console/app/`
directory when present. A dev checkout can serve ANY vault without deploying:
build once (`cd console-ui && npm run build`) and pass the overlay —
`ovp2 serve --vault-root "$VAULT" --viz-dir <repo>/console-ui/dist`.
The old generated console stays reachable at `/legacy-index.html`
(plus `/ops.html`, `/audit.html`, `/candidates.html` — also linked from the
portal's System page). After a `daily` run while the server is up, hit
`/api/refresh` (or restart) to reload the index.

Useful variants:

```bash
ovp2 daily --vault-root "$VAULT" --dry-run            # plan only, writes nothing
ovp2 daily --vault-root "$VAULT" --client live --date "$(date +%F)" --max-sources 3
ovp2 daily --vault-root "$VAULT" --client live --date "$(date +%F)" --retry-blocked
ovp2 daily --vault-root "$VAULT" --no-intake          # reader phase only
ovp2 daily --vault-root "$VAULT" --no-lifecycle       # leave sources in 01-Raw
```

### Scheduled runs (dogfood day 4+)

After 3 stable manual days, schedule the daily at a fixed local time (e.g.
09:30). macOS launchd example — `~/Library/LaunchAgents/com.ovp.daily.plist`
calling a small wrapper script so env + date stay in one place:

```bash
#!/bin/zsh
# ~/bin/ovp-daily.sh — the ONLY canonical scheduled invocation
set -euo pipefail
source ~/.ovp-live-env                # ANTHROPIC_API_KEY etc.; never in the repo
export OVP_LLM_NO_PROXY=1 OVP_LLM_TIMEOUT_SECS=480
mkdir -p ~/Documents/ovp-vault/.ovp
~/Documents/obsidian-vault-pipeline/target/release/ovp2 daily \
  --vault-root ~/Documents/ovp-vault \
  --client live \
  --date "$(date +%F)" \
  >> ~/Documents/ovp-vault/.ovp/dogfood-cron.log 2>&1
```

Notes: exit code is non-zero when ANY source fails (partial success included) —
the cron log, not the exit code, is the thing to read; check
`.ovp/daily-runs.jsonl` and run `ovp2 doctor` weekly. A stale `run.lock` from a
crashed run is reclaimed automatically (dead-PID probe); a lock held by a live
run makes the scheduled run exit with "another OVP run appears to be in
progress" — that is correct behavior, not a failure to fix.

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
  --suggest \
  --out .run/review-session-$(date +%Y%m%d)
```

`--suggest` additionally writes `backfill-candidates.md/json` — zero-LLM,
evidence-sidecar retrieval of corpus units from cases each entry does NOT yet
cite. A relevant candidate becomes a `narrow` decision whose citations are the
union of old + new (the R0 pattern that promoted 4 claims to durable in one
session). Deferred entries (unfired triggers) and parked source-insights are
skipped automatically.

Decision actions (M36 R1 typed vocabulary; the M25 verbs remain as aliases):

| action | revisions | meaning |
|---|---|---|
| `narrow` (= `rewrite`) | exactly 1 | one narrower claim, re-gated |
| `split_by_evidence` (= `split`) | ≥2 | evidence partitioned into narrower claims |
| `demote_to_source_insight` | 0 | true-but-narrow → parked insight, no re-gate, no deletion |
| `defer_until` + `defer:{trigger,n}` | 0 | park with a checkable trigger (`corpus_grows_by` / `new_sources_in_theme`); prepare skips it until fired |
| `reject_as_noise` (= `reject`) | 0 | permanent removal — the only destructive action |
| `keep_caveated` | 0 | explicit no-op (prefer `defer_until`, which stops re-presenting) |

Apply also warns (never blocks) on **triviality** (a revision that mostly
restates its own quotes — containment ≥ 0.8) and on **cross-source loss** (a
repair that dropped the parent's ≥2-source property).

This writes `review-sheet.md`, `decisions.template.json`, and
`selected-claim-ids.txt`. The command does not decide durability and does not
write the Crystal ledger.

Fill the template (`rewrite`/`split` decisions must carry full revised claims
with verbatim citations; `keep_caveated` leaves the entry queued; `reject`
retires it), then apply the whole chain in one command:

```bash
ovp2 crystal-review-session-apply \
  --vault-root "$VAULT" \
  --decisions .run/review-session-$(date +%Y%m%d)/decisions.json \
  --client live --refresh --date $(date +%F)
```

This runs decisions → revised claims → strength gate → durable write →
Crystal Notes + index + console refresh. Human decisions never bypass the
gate. Malformed decisions (a `rewrite`/`split` with missing revisions) and
revisions with defective citations fail LOUD before anything is mutated —
fix `decisions.json` and re-run. Revisions that pass grounding but fail the
strength gate route back into `review.json` with their rationale. Reviewed
entries retire from the queue; unprocessed entries are preserved, so a small
batch never erases the rest of the queue.

## 5. Recovery / rebuild

Everything under `.ovp/index/` and `.ovp/console/` is a derived projection:
delete freely, rebuild with `ovp2 index` / `ovp2 console`.
Authoritative state = the ledgers (`.ovp/*.jsonl`), the reader packs, the
crystal store, and the vault notes themselves. Reports are append-only
snapshots. Cassettes (`.ovp/cassettes/`) are replayable model replies —
deleting them only costs re-recording.

## 6. What this does NOT do (yet / by design)

- Web-page fetching for bare bookmarks needs the `web-fetch-live` build
  feature (prebuilt binaries include it; a plain `cargo build` does not) —
  it accepts html / plain / markdown responses; anything else stays
  `needs-content` for hand enrichment.
- No arXiv-specific enrichment; papers flow as ordinary articles (GitHub
  links get README enrichment behind `github-live`).
- No daemon — run `daily` from cron/launchd if you want scheduling.
- No embeddings/SQLite; `find` is substring search over the read model
  (semantic themes via embeddings are in flight).
- Legacy Python surfaces (`knowledge.db`, the 8787 `ovp-ui`) are retired, not
  ported — see `docs/ovp-to-ovp2.md` for the mapping (`ovp-ask` → `ovp2 ask`,
  `/digest` → `ovp2 digest`, `/ops` → the portal System page).
