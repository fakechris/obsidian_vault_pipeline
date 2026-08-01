# Library Source Enrichment Plan

**Goal**: Make Source detail (`/library/:sha`) a rich reading + analysis surface
for GitHub, arXiv, and long-form English notes — companion links, bilingual
archive, deep summary, and chat-on-this-source.

**Honesty**: LLM work writes durable artifacts; `last_run` / job status must
never greenwash a failed translate/summarize.

---

## Stages

| Stage | Deliverable | Status |
|-------|-------------|--------|
| **S1** Companion links | GitHub → zread / deepwiki; arXiv → ar5iv / alphaXiv | Done |
| **S2** Translate | EN detect → LLM refined zh → dual archive | Done |
| **S3** Deep summary | LLM structured summary artifact + tab | Done |
| **S4** Chat on this | `/ask` focused on one source body | Done |
| **S5** Video (TODO) | YouTube / Bili / X captions — later | Deferred |

## S5 deferred detail (do not implement yet)

- YouTube: timedtext / caption tracks → markdown transcript note
- Bilibili: subtitle API / cc
- X (Twitter) video: when captions available
- Store under `40-Resources/Source-Work/` or `50-Inbox` intake path
- Trigger from companion chip row when URL matches video hosts

---

## S1 — Companion links (client-only)

**Inputs**: `source.url`, `source.entities[]` (`github:owner/repo`, `arxiv:id`).

| Kind | Primary | Companions |
|------|---------|------------|
| GitHub `owner/repo` | github.com | zread.ai/github/owner/repo, deepwiki.com/owner/repo |
| arXiv `YYMM.NNNNN` | arxiv.org/abs | ar5iv.org/html/id, alphaxiv.org/abs/id |

UI: chip row under URL on SourceDetailPage (live + static).

---

## S2 — Translation

**Detect**: body CJK ratio < ~15% and Latin-heavy → offer “译为中文”.

**Engine**: product LLM via `providers.toml` / ask client factory — **not** free MT.

Quality stack (product subset of industry refined translators e.g. baoyu-translate):

| Layer | What |
|-------|------|
| System prompt | 信达雅 + KEEP list (tickers, ETF codes, AUM/TRS/ADR…) + standard finance CN |
| Glossary pre-pass | Multi-chunk / long body: extract `EN → CN\|KEEP` (≤40), inject every chunk; also `glossary.md` |
| Chunking | ~12k chars, paragraph breaks, shared glossary |
| Portal UX | Desktop WebView may drop long POSTs — UI **polls** `GET /work` until `has_zh` / new `translated_at` so the 中文 tab appears without leaving the page |

Full multi-pass review/polish (baoyu refined) is a future latency trade-off, not default.

**Archive** (idempotent; skip if artifact fresh):

```text
40-Resources/Source-Work/<sha8>_<slug>/
  meta.json      # sha, url, model, times, lang
  original.md    # body snapshot at first translate
  zh.md          # Chinese translation
  glossary.md    # optional session glossary (audit)
```

**API**:
- `GET  /api/source/:sha/work` → meta + paths + flags + zh/summary body
- `POST /api/source/:sha/translate` → runs LLM, writes archive, returns zh preview

---

## S3 — Deep summary

Same work dir: `summary.md` (structured: 一句话 / 要点 / 方法 / 局限 / 可行动作).

**API**: `POST /api/source/:sha/summarize`

UI tab: Summary | Source | Memory | 中文.

---

## S4 — Chat on this source (source-grounded, in-context)

**Product model (not a bare Ask jump):**

| Surface | Job |
|---------|-----|
| **Source detail dock** | Read + talk about THIS doc. Stay on the page. |
| **Ask (global)** | Vault-wide questions. Unified history for all sessions. |

**Interaction**

1. 「针对本文对话」opens a **right dock** on `/library/:sha` (URL `?chat=1` or
   `?chat=<stem>` to resume). Document stays visible.
2. Context pack chips show what is auto-injected: **Body · Memory · Crystal**.
3. Each `POST /api/ask` with `focus_source` injects body + memory cards/units +
   citing claims (server-side). User question is stored raw in history.
4. Seed prompts are source-local; multi-turn stays in the dock.
5. “Open in Ask” / history badges link both surfaces without losing session.

**History (one spine: `.ovp/chats/`)**

- New source-grounded files get `<!-- ovp:focus_source=… -->` (+ title).
- `GET /api/chats` returns `focus_source`, `focus_title`, `preview`.
- **Ask** history: filter All | On sources | Vault-wide; badge + jump back to
  source dock.
- **Library detail**: “Past chats on this source” list inside the dock.

**Non-goals for S4:** separate chat product, re-selecting source in Ask empty
state, dumping the focus pack into the saved **Q:** line.

---

## S5 — Video (deferred TODO)

- YouTube / Bilibili / X video: caption fetch + transcript note
- Not in this implementation wave

---

## Non-goals (this wave)

- Auto-translate on every daily run
- Replacing Memory/cards pipeline
- Free Google/DeepL engines
