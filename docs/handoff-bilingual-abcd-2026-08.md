# Handoff: Bilingual source-work A–D + daily/Pinboard/console (2026-08)

**Audience:** next agent picking up dogfood vault work  
**Repo:** `obsidian-vault-pipeline` (Rust OVP Next trunk — see root `AGENTS.md`)  
**Operator vault:** `/Users/chris/Documents/ovp-vault`  
**Date:** 2026-08-03  
**Branch state:** `main` includes PR **#406** (feature) + **#407** (runs cap + theme zh UX). Local should be `main @ origin/main`.

---

## 1. Goal (product intent)

Bilingual enrichment without mutating English authority:

| Layer | English authority | Rebuildable zh projection |
|-------|-------------------|---------------------------|
| Source body | note / `original.md` | `40-Resources/Source-Work/<sha8>_*/{zh,summary,glossary}.md` |
| Crystal claims | `.ovp/crystal/ledger.jsonl` | `.ovp/crystal/claims_zh.json` |
| Reader cards | pack evidence | `.ovp/crystal/cards_zh.json` |
| Theme overview | `.ovp/crystal/theme_pages.json` | `.ovp/crystal/theme_pages_zh.json` |
| Theme **name** | crystal `label` | crystal `label_zh` (already on themes/pages; not claims_zh) |

**A–D (as implemented):**

- **A** — daily auto: enqueue source summarize + translate (config `.ovp/source-work.toml`)
- **B** — historical `source-work backfill` → queue worker
- **C** — durable claims → `claims_zh.json` via `ovp2 source-work claims-zh`
- **D** — cards + theme pages → `cards_zh` / `theme_pages_zh` via `ovp2 source-work memory-zh`

Config flags `auto_claim_zh` / `auto_memory_zh` only bias **those CLIs**; they do **not** auto-run C/D inside the source-work queue.

---

## 2. What is DONE (code on main)

### 2.1 Source-work pipeline (A/B)

- Crates: `ovp-memory` (`source_work.rs`, `source_work_queue.rs`, `source_work_auto.rs`, `source_work_config.rs`), CLI `source-work *`, server queue + worker, console Work Queue + Library tabs.
- Queue: `.ovp/source-work-queue.json`, single vault worker lock, priority (UI ≈100 > backfill ≈0/10).
- Translate: refined 信达雅 prompt, session glossary, multi-chunk, `MAX_TRANSLATE_BODY_CHARS=160k`, sanitize (glossary leak + CoT JSON unwrap).
- **Quality gate:** reject English-as-zh (prose-first CJK/Latin), near-copy check; invalidate poisoned `source_work/v2` cassettes; single-chunk live retry after invalidate. Failures do **not** stamp `translated_at` as success.
- Tests: `cargo test -p ovp-memory --lib source_work::`

### 2.2 Daily / Pinboard

- Live Pinboard: incremental sync uses API **`fromdt`** when `since` is set (full `posts/all` **500s** on large accounts; token/`posts/update` still OK).
- Pinboard **HTTP 5xx / transport → soft-skip** (warn + `report.warnings`); intake+reader continue. Flood-guard still hard-fails.
- Last-run UX: absolute times + **timeline** (phase fail, skipped reader, next due). Strings in `console-ui` activity/banner.

### 2.3 Console UX

- System → Runs: **default newest 30**, expand/collapse; disk reports unbounded.
- Theme page: content follows top-bar **EN/中**; shows `zh ready X/N` and missing-projection banner when `claims_zh` absent.
- Work queue: compact ETA + hover; library prev/next keeps content tab.

### 2.4 Merged PRs

| PR | Topic |
|----|--------|
| [#406](https://github.com/fakechris/obsidian_vault_pipeline/pull/406) | bilingual A–D code, queue, quality gate, pinboard soft-fail, timeline base |
| [#407](https://github.com/fakechris/obsidian_vault_pipeline/pull/407) | runs table cap 30; theme zh status copy |

---

## 3. What is NOT done (data / ops)

### 3.1 Crystal / claim / memory Chinese projections — **code yes, vault data no**

On dogfood vault **as of handoff**:

```text
.ovp/crystal/claims_zh.json      MISSING
.ovp/crystal/cards_zh.json       MISSING
.ovp/crystal/theme_pages_zh.json MISSING
.ovp/crystal/glossary.json       MISSING
```

Theme labels still show Chinese names (`label_zh` from theme labeling). Claim bodies and topic-overview **sections** stay English when UI is 中 (fallback).

**This is the main incomplete piece of A–D.** Prior session ran source backfill hard; never kicked full `claims-zh` / `memory-zh`.

### 3.2 Source-work queue still draining

Approximate snapshot (re-check live):

| Metric | ~Value |
|--------|--------|
| Source-Work dirs | ~1022 |
| with `zh.md` | ~899 |
| with `summary.md` | ~1020 |
| Queue | ~338 queued, ~108 done, ~3 failed, 0–1 running |

Failed/force-retry English zh set was partially requeued earlier; quality gate may leave some as hard fail after live retry.

### 3.3 Daily schedule ops issues (environment)

1. **Launchd `com.ovp2.daily`:** fires 09:00 but log is endless  
   `daily.env: Operation not permitted` (macOS TCC). Job argv **differs** from `schedule.json` (launchd historically without `--pinboard-live`; schedule.json has pinboard).
2. **In-app scheduler** only ticks while **OVP2 desktop app** is open (~10 min).
3. Last pinboard hard-fail (pre-fix) left operators thinking whole daily was broken; after soft-fail + fromdt, a later run advanced into reader then **aborted** mid-flight (`daily-2026-08-02`, heartbeat can show aborted if process died).

### 3.4 Portal SPA deploy path

Serve prefers **vault** `.ovp/console/app/` over repo `console-ui/dist`. After UI changes:

```bash
cd <repo>/console-ui && npm run build
rm -rf /Users/chris/Documents/ovp-vault/.ovp/console/app
mkdir -p /Users/chris/Documents/ovp-vault/.ovp/console/app
cp -R dist/* /Users/chris/Documents/ovp-vault/.ovp/console/app/
# hard-refresh browser (Cmd+Shift+R)
```

Binary rebuild alone does **not** refresh vault-copied SPA.

---

## 4. Key paths & commands

### Repo

```bash
cd /Users/chris/Documents/obsidian-vault-pipeline
# AGENTS.md = Rust trunk rules; never reintroduce Python pipeline
cargo test -p ovp-memory --lib source_work::
cargo test -p ovp-intake --lib pinboard
cd console-ui && npm test -- --run src/lib/derive.test.ts
```

### Vault

```text
/Users/chris/Documents/ovp-vault/
  .ovp/source-work.toml
  .ovp/source-work-queue.json
  .ovp/last-run.json
  .ovp/schedule.json + schedule-state.json
  .ovp/reports/*.json          # append-only daily reports (UI shows 30)
  .ovp/cassettes/ask/source_work/v2/
  .ovp/cassettes/bilingual/    # claims-zh / memory-zh Record cache
  .ovp/logs/daily-launchd.log
  40-Resources/Source-Work/<sha8>_*/
```

### Source-work (A/B)

```bash
ovp2 source-work show-config --vault-root /Users/chris/Documents/ovp-vault
ovp2 source-work backfill --vault-root … --translate --summarize [--force] [--max N]
# Worker: ovp2 serve --vault-root … --host 127.0.0.1 --port 8787
```

### Claims / memory zh (C/D) — **next agent should run**

```bash
# Smoke (~30 durable claims)
ovp2 source-work claims-zh \
  --vault-root /Users/chris/Documents/ovp-vault \
  --client live --max 30

# Full active durable set (~1k LLM calls — budget!)
ovp2 source-work claims-zh --vault-root … --client live

# Theme overview sections (few pages, cheap)
ovp2 source-work memory-zh --vault-root … --client live --theme-pages

# Cards (large) or both default
ovp2 source-work memory-zh --vault-root … --client live --max N
```

Accept: portal Theme page `zh ready > 0`, sample durable claim body Chinese when UI is 中; files appear under `.ovp/crystal/*_zh.json`.

---

## 5. Architecture notes (don’t regress)

1. **Ledger EN authority** — never rewrite claim English with MT; only projections.
2. **Cassette namespaces** — translate uses `source_work/v2`; bad English replies must `invalidate`, not silent done.
3. **Queue** — one worker per vault; UI priority > backfill; don’t reintroduce `recover_interrupted` on every disk reload mid-claim (prior bug).
4. **Pinboard** — soft-fail remote errors in `daily`; use `fromdt` for incremental; full-history `posts/all` is unsafe for large accounts.
5. **Runs table** — UI cap only; do not delete `.ovp/reports` without operator request.

---

## 6. Suggested next steps (priority)

1. **C smoke:** `claims-zh --max 30` + hard-refresh theme page (e.g. Agent Memory Systems).
2. **C full** in batches (`--max 100` loops) if quality OK.
3. **D theme-pages** first (cheap), then cards as budget allows.
4. **Source queue:** drain remaining translate fails; requeue only gate-failing low-CJK zh if still present.
5. **Schedule ops:** fix launchd TCC / align launchd argv with `schedule.json`, or document “desktop app = clock”.
6. **SPA:** always redeploy `console-ui/dist` → vault `.ovp/console/app` after UI work.

---

## 7. Quick diagnosis cheatsheet

| Symptom | Likely cause |
|---------|----------------|
| Theme 中 still English claims | No `claims_zh.json` — run C |
| Last run FAILED pinboard 500, nothing processed | Old hard-fail path; fixed soft-skip on main — rerun daily with new binary |
| Launchd daily silent fail | `.ovp/logs/daily-launchd.log` → `daily.env: Operation not permitted` |
| UI changes not visible | Stale `.ovp/console/app` — redeploy dist |
| zh.md English but “done” | Pre-gate data; force retranslate after quality gate (or delete zh + requeue) |
| Work queue stuck dual serve | Two `ovp2 serve` — single worker lock |

---

## 8. Open questions for operator (not decided)

- Budget for ~1k claim translations + all cards?
- Prefer claim zh only for durable themes of interest vs whole ledger?
- Keep launchd, desktop-only schedule, or both after TCC fix?

---

## 9. Contact points in code

| Concern | Location |
|---------|----------|
| Translate + gate + sanitize | `crates/ovp-memory/src/source_work.rs` |
| Queue priority / claim | `crates/ovp-memory/src/source_work_queue.rs` |
| Claims/cards/pages zh | `crates/ovp-memory/src/bilingual.rs`, `crates/ovp-cli/src/commands/source_work_cmd.rs` |
| Daily pinboard soft-skip | `crates/ovp-cli/src/commands/daily.rs` |
| Pinboard fromdt | `crates/ovp-intake/src/pinboard.rs` |
| Theme UI lang + zh missing banner | `console-ui/src/pages/ThemeDetailPage.tsx` |
| Runs table cap | `console-ui/src/pages/SystemPage.tsx` |
| Run timeline | `console-ui/src/lib/derive.ts` (`buildRunTimeline`), `RunActivity.tsx` |
| Splice claim_zh into APIs | `crates/ovp-server/src/lib.rs` (`splice_theme_pages_zh`, source memory zh) |

---

*End of handoff. Re-verify vault numbers before long LLM batches; paths and counters drift as the queue worker runs.*
