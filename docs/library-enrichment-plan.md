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

**Engine**: product LLM via existing `providers.toml` / ask client factory
(refined prompt: 信达雅, term consistency, no free MT engines).

**Archive** (idempotent; skip if artifact fresh):

```text
40-Resources/Source-Work/<sha8>_<slug>/
  meta.json      # sha, url, model, times, lang
  original.md    # body snapshot at first translate
  zh.md          # Chinese translation
```

**API**:
- `GET  /api/source/:sha/work` → meta + paths + flags
- `POST /api/source/:sha/translate` → runs LLM, writes archive, returns zh preview

---

## S3 — Deep summary

Same work dir: `summary.md` (structured: 一句话 / 要点 / 方法 / 局限 / 可行动作).

**API**: `POST /api/source/:sha/summarize`

UI tab: Summary | Source | Memory | 中文.

---

## S4 — Chat on this source

- Button → `/ask?focus=<sha>`
- Ask POST accepts `focus_source: "<sha>"` → inject full body (capped) as
  primary context; agent tools still available for vault-wide follow-ups.

---

## S5 — Video (deferred TODO)

- YouTube / Bilibili / X video: caption fetch + transcript note
- Not in this implementation wave

---

## Non-goals (this wave)

- Auto-translate on every daily run
- Replacing Memory/cards pipeline
- Free Google/DeepL engines
