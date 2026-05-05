# BL-058 — Absorb v2: CandidateUnit prompt + legacy tagging

> **Status**: design (ready to implement)
> **Author**: chris
> **Date**: 2026-05-05
> **Decision authority**: chris
> **Depends on**: PR #155 (merged), PR #156 BL-066 (open)
> **Blocks**: future re-extraction (BL-068), reader epistemic HUD (later)

---

## Problem

The current `auto_evergreen_extractor.SYSTEM_PROMPT` has three structural problems
that the 2026-05-05 fidelity audit and prompt A/B experiment both confirmed:

1. **Forced template** — every output must fill `定义 / 详细解释 / 为什么重要`
   sections, which mechanically converts every source content type
   (procedure, case, tradeoff, fact) into "X 是 Y" definitions.
2. **Volume bias** — the prompt literally says **"抽得多比抽得少好"** with
   floors of "5-10 units per short article, 15-30+ per long article",
   no permission to skip low-value sources.
3. **No specificity guard** — no rule requires preserving source numbers
   / named entities / tradeoffs / examples; the LLM freely abstracts
   them away.

50-sample fidelity rubric: most evergreens scored `faithful_generic`
(claim is in source, but specifics were stripped). 6-source A/B experiment
(`60-Logs/prompt-ab/v1/`) confirmed the v2 prompt produces dramatically
more specific units at half the volume, and correctly returns `units=[]`
on a 13-character github stub instead of fabricating 3 units from nothing.

---

## Decisions (locked)

| # | Question | Decision |
|---|---|---|
| 1 | Direct cutover or shadow mode first? | **Direct cutover** — A/B data is sufficient, no need for parallel run. Old evergreens stay as legacy. |
| 2 | New schema fields | **Add 6 fields** (see §4 below); not afraid of schema change since vault is markdown + the existing `knowledge_index` rebuilds idempotently. |
| 3 | Tag legacy 7000 evergreens? | **Yes, mark them all `legacy_unverified=true` + `extraction_prompt_version=v1`**. Plan to re-run high-value subset later (BL-068). Don't delete. |

---

## The v2 prompt

This is the prompt we drafted in OVP's own voice (not transcribed from NM).
It's already in `commands/prompt_ab.py:NEW_PROMPT` and proven on 6 sources.
BL-058 promotes it to production.

```text
你的任务:从一篇源文里抽取对 vault 有价值的 CandidateUnit。
你不是在总结源文,也不是在把源文改写成笔记。
你是在找出源文中那些**保留了原文具体物**(数字、命名实体、方法步骤、
工具名、反例、边界条件、对照选择)的可复用知识单元。

## 输出格式 (严格 JSON,不要 markdown 包装)

{
  "source_value_summary": "一句话概括这篇源文的可抽取价值。如果价值很低,直说。",
  "units": [
    {
      "title": "一句完整的陈述句,不是名词短语",
      "unit_type": "fact|method|procedure|tradeoff|failure_mode|counterexample|case_detail|learning|decision|quote",
      "epistemic_role": "fact|interpretation|method|quote|attributed_claim",
      "content": "markdown,自包含,不需要回读源文也能理解",
      "source_anchor": "源文中逐字出现的短语/数字/名称/API,作为这条单元的具体锚点",
      "specifics": ["保留下来的具体物分类:numbers / names / tradeoffs / examples / edge_cases"],
      "related_concepts": ["相关 wikilink slug,0-5 个,宁缺勿滥"]
    }
  ],
  "skip_reason": "如果 units 是空,说明为什么:常识 / 重复 / 没具体物 / 全是观点没证据 / 等等"
}

## 抽取规则,违反任何一条这条单元就不该存在:

[10 rules — see commands/prompt_ab.py NEW_PROMPT for full text]

加规则 11 (BL-058 implementation, not in prompt_ab v1):

11. **related_concepts**(可选,0-5 个)
    列出这条 unit 真正在概念上相关的其他知识 —— 不是相同 topic 的所有
    东西,是会让人想点过去的特定关联。entity prime 列表里有合适的 slug
    就直接用;否则用 kebab-case slug。**没有强相关就给空列表,不要凑数**。
    与 v1 不同:v1 强制 ≥ 3 个,v2 允许 0 个。
```

The full prompt text including the 11 hard rules will live in
`auto_evergreen_extractor.SYSTEM_PROMPT` after BL-058. Rule 11 is new
and replaces v1's "≥ 3 related_concepts" floor.

---

## Schema changes

### Evergreen frontmatter — add 6 fields

```yaml
---
# Existing fields (unchanged)
note_id: critical-step-prioritization
title: "..."
type: evergreen
entity_type: concept
date: 2026-04-09
tags: [evergreen]
aliases: ["..."]
source_url: "https://x.com/..."
source_title: "..."
source_authors: [...]
source_published_at: "..."
source_fingerprint: "4198ef3f0128"

# NEW fields (BL-058)
extraction_prompt_version: v2          # was implicit "v1" — now explicit
unit_type: fact                        # one of 10 (see prompt)
epistemic_role: fact                   # one of 5
source_anchor: "16% of trajectory steps are critical decision points"  # verbatim
specifics: [numbers, names]            # categorical chips
absorbed_at: 2026-05-15T08:23:14Z      # when this v2 extraction ran
---
```

**Why these 6:**

- `extraction_prompt_version`: enables the legacy/v2 split. Future tools
  (reader UI badges, crystal scoring weights, fidelity replay) key off this.
- `unit_type`: replaces the loosely-used `entity_type=concept` default.
  Lets reader UI surface "show me only methods" / "show me failure modes".
- `epistemic_role`: separates "fact" from "interpretation" at the unit
  level. Crystal evidence aggregation will weight these differently.
- `source_anchor`: the **mechanical fidelity check** — `grep` the anchor
  string in the source body; missing → flag as possible hallucination.
- `specifics`: the chips humans saw in the fidelity HTML. Aggregating
  these across the vault lets us see "method units have higher
  specificity preservation than learning units" as data, not intuition.
- `absorbed_at`: when v2 ran. Distinct from `date` (article publish date)
  and `source_fetched_at` (raw-source intake date in BL-066).

### Evergreen body template — strip forced sections

```markdown
# {title}

{content}                      # ← LLM-produced markdown, free-form, no template

> **Source anchor**: "{source_anchor}"

## Related                     # ← only rendered when related_concepts non-empty
- [[wikilink-1]]
- [[wikilink-2]]

## Source
- [Original]({source_url})
- [[{source_file_stem}]]      # backref to processed source in 03-Processed
```

**What's gone:**
- `> **一句话定义**: ...` — definition is now whatever fits unit_type
- `## 📝 详细解释 / ### 是什么？/ ### 为什么重要？` — model-driven sections

**What's kept (clarification — was previously listed as "removed"):**
- `## Related` block — keeping wikilink generation in absorb.
  Async link-suggestion would leave evergreens disconnected during the
  window between extraction and link generation, breaks MOC/orphan
  detection/reader linked-mentions, and is a slop-prone silent-failure
  pattern.  v2 keeps `related_concepts` in the prompt output (same as
  v1) but loosens the requirement: 0-5 entries, "宁缺勿滥" rule, no
  forced minimum.  Empty array → `## Related` block is omitted.

The LLM produces the content body in whatever shape matches `unit_type`:
- `procedure` → numbered steps
- `tradeoff` → "we chose X over Y because Z"
- `fact` → single concrete statement with grounding
- `quote` → verbatim block + brief annotation
- etc.

### Knowledge index (SQLite) — DEFERRED to BL-058b

Originally proposed: `ALTER TABLE objects ADD COLUMN ...` for the 7
new fields.  Implementation chose **NOT** to do this in BL-058 — see
"Scope cut" below.

The 6 new frontmatter fields are still queryable through the existing
``pages_index.frontmatter_json`` column via ``json_extract``:

```sql
SELECT slug, json_extract(frontmatter_json, '$.unit_type') AS unit_type
FROM pages_index
WHERE json_extract(frontmatter_json, '$.extraction_prompt_version') = 'v2';
```

This is enough for any current or near-term consumer.  When a future
feature (reader UI badges, crystal scoring weights) actually needs
indexed lookups on these fields, BL-058b will materialize them as
real columns.

Reason: avoiding `ALTER TABLE` keeps the rebuild idempotent without
requiring a versioned migration step, and avoids a class of "I rebuilt
knowledge.db on a v1 vault and the columns don't exist" failures
during the rollout window.

### Audit event

Each successful absorb writes an `audit_events` row with type
`evergreen_v2_promoted` containing:
- slug, source_path, source_anchor, unit_type, epistemic_role,
  prompt_version='v2', absorbed_at

This is the v2 equivalent of `evergreen_auto_promoted` from v1.
The v1 event type is preserved so historical audit replay still works.

---

## Legacy migration (one-shot)

A new command `ovp-tag-legacy-evergreens` walks every evergreen in
`10-Knowledge/Evergreen/`:

1. If frontmatter lacks `extraction_prompt_version`:
   - Add `extraction_prompt_version: v1`
   - Add `legacy_unverified: true`
   - Add `legacy_tagged_at: 2026-05-15T...` (so we know when the tag was applied)
2. Don't touch any other field.
3. Don't touch the body.
4. Idempotent — re-running doesn't duplicate.
5. Writes a one-off audit log to `60-Logs/legacy-tag/<run-id>/manifest.json`
   listing every file tagged.

**Estimated scope:** ~7,000 evergreens × ~50ms file rewrite = **~6 minutes**.
No LLM calls. Reversible (the script also accepts `--untag` mode that
removes the two added fields).

After the migration runs once:
- All v1 evergreens have `legacy_unverified=true` and stay searchable / readable
- All NEW evergreens (post-cutover) have `legacy_unverified` absent
  (i.e. implicitly verified by virtue of being v2-extracted with source_anchor)
- Crystal scoring (BL-054) gets a knob to down-weight legacy_unverified
  units — implementation deferred to a follow-up (NOT in BL-058).

---

## Pipeline changes

### Files modified (actual)

| File | Change |
|---|---|
| `auto_evergreen_extractor.py` | Replaced `SYSTEM_PROMPT` (~80 lines, 11 hard rules). New `_parse_v2_response` method handles wrapped JSON + skip_reason + bare-list rejection. New `_unit_to_concept` converts v2 unit dicts to legacy concept-dict shape so `process_file` doesn't need to change. `create_evergreen_note` rewritten — no forced sections, conditional `## Related` block, `Source anchor` blockquote. Dropped legacy `evergreen_low_link` audit (replaced by `absorb_skipped_source` + `absorb_parse_error`). |
| `commands/tag_legacy_evergreens.py` | NEW. Idempotent + reversible migration script. |
| `pyproject.toml` | Register `ovp-tag-legacy-evergreens` entry point. |
| `tests/test_absorb_v2.py` | NEW. 20 tests covering parser / converter / body template / migration command. |
| `tests/test_evergreen_prompt_liberation.py` | Rewritten for v2 contract (output wrapper / source_anchor / specifics / 0-8 cap / 0-5 related). |
| `tests/test_evergreen_extractor_retrieval.py` | Removed two tests for `evergreen_low_link` audit (event was dropped — v2 allows 0-5 related, not "≥3 required"). |

### Files NOT modified (deferred to BL-058b/c)

| File | Why |
|---|---|
| `truth_store.py` | SQLite schema migration deferred — `pages_index.frontmatter_json` already provides queryable access via `json_extract()`. Real columns can wait until a consumer needs indexed lookup. |
| `knowledge_index.py` | Same — no new column wiring needed for v1. |
| `truth_api.py`, `commands/_ui_renderers.py` | Reader UI badges that surface `unit_type` / `legacy_unverified` deferred to BL-058b. Data is captured in frontmatter for downstream readers. |
| `crystal_scoring.py` | `legacy_unverified` weight knob deferred to BL-058c (needs calibration data first). |

### Files NOT modified (deliberate scope cuts)

- `truth_api.py` — keeps reading the same columns; new fields default
  to NULL/empty. Reader UI cosmetics that surface unit_type/legacy
  badges deferred to a follow-up PR (BL-058b).
- `crystal_scoring.py` — keeps current weights. legacy_unverified
  down-weighting deferred (BL-058c).
- `commands/_ui_renderers.py` — no UI changes. Showing the new fields
  in reader / search / briefing is a separate concern.

---

## Testing strategy

### Unit tests

- `test_absorb_v2_prompt_returns_units_or_skip_reason` — mock LLM
  returning a v2 JSON, verify parser handles both shapes (`units=[...]`
  and `units=[]` + `skip_reason`).
- `test_create_evergreen_v2_note_template` — verify new body template
  has no `定义/详细解释/为什么重要` headers and includes the
  source_anchor block.
- `test_legacy_tag_idempotent` — run tag command twice, verify no
  duplicate fields.
- `test_legacy_tag_reversible` — run with `--untag`, verify the two
  added fields disappear.
- `test_knowledge_index_reads_new_columns` — vault with one v2
  evergreen, rebuild, query the new columns, verify populated.

### Regression / contract tests

- `test_v1_evergreens_remain_readable` — ensure absorb-time changes
  don't break reading existing v1 evergreens (different body shape).
- `test_absorb_writes_audit_event_v2_promoted` — new event type fires.
- `test_skip_reason_writes_no_evergreen` — when LLM returns
  `units=[]`, no evergreen file is created.

### End-to-end test

- `test_e2e_v2_absorb_chain` — same shape as `test_absorb_to_entity_extract_chain`
  but with v2 prompt response. Asserts processed_files contains real
  vault paths (regression guard from PR #155 still holds), and the
  evergreen frontmatter has all 6 new fields populated correctly.

### Manual validation checkpoint

After merge, run **one** real `ovp-pipeline --incremental` and verify:
- Pipeline report shows non-zero new evergreens AND non-zero skip_reason rate
- Random sample of 5 new evergreens — manual fidelity check
- `entity_mentions` count grows (regression guard for PR #155 working)
- No `extraction_prompt_version: v1` written to any new file

---

## Rollout plan

```
Day 0   Merge BL-058 PR
        Schema migration runs idempotently (rebuild_knowledge_index does it).
        Old evergreens still untagged.

Day 0+5 Run ovp-tag-legacy-evergreens once.
        ~7000 files tagged. ~6 minutes.
        Manifest at 60-Logs/legacy-tag/<run-id>/.

Day 1   Next ovp-pipeline --incremental run uses v2 prompt.
        New evergreens land with v2 schema; old evergreens carry legacy tags.

Week 1  Watch:
        - skip_reason rate (expecting 10-30% on incremental sources)
        - new evergreen specifics distribution (proxy for "is v2 actually
          preserving specifics?")
        - any LLM JSON parse failures (the v2 schema is more complex)

Week 2+ If quality is good, plan BL-068 (re-extraction of high-value v1 evergreens).
        If not, iterate on prompt — but the schema change stays.
```

---

## What's NOT in BL-058 (deliberate scope cuts)

| Out-of-scope | Why | Future BL |
|---|---|---|
| Reader UI badges for unit_type / legacy | Cosmetic; data is captured, surfacing is separate | BL-058b |
| Crystal scoring weight on legacy_unverified | Easy mechanically but needs calibration data first | BL-058c |
| Re-running v1 evergreens through v2 | High-value subset only; ranking + cost analysis needed | BL-068 |
| Article + paper processors switching to v2 prompt | They produce `_深度解读.md` first; deeper change | BL-058d |
| Entity extraction prompt v2 | Different prompt, different module | BL-067 |
| Reader-side filter "hide legacy by default" | Product decision, not infrastructure | (later) |

---

## Open questions / future work

1. **Article + paper processors** still produce `_深度解读.md` middle layers.
   Until BL-058d, those still go through the OLD absorb prompt for non-github
   sources. After BL-066 + BL-058 merge, the picture is:

   ```
   github sources (BL-066)   →  03-Processed/<slug>.md  →  v2 absorb  →  v2 evergreen
   article/paper sources     →  *_深度解读.md           →  v2 absorb  →  v2 evergreen
                                ↑ still LLM-rewritten, lossy
   ```

   v2 prompt will work on `_深度解读.md` files (it doesn't care about input
   shape) but the upstream LLM rewrite is still there. BL-058d removes
   that intermediate layer.

2. **`unit_type=quote` and `unit_type=case_detail` may overlap with what
   future "raw quote artifacts" should look like**. If we ever add a
   "QuoteArtifact" first-class type (per the four-ledger discussion),
   `unit_type=quote` evergreens become the migration source. Keep
   them as evergreens for now.

3. **What if the same source produces two different units with the same
   anchor?** Current dedup runs on slug; same anchor + different slugs is
   allowed. May want to add an anchor-level dedup pass later (low
   priority — same anchor with different framing is often legitimate
   — e.g. one method-unit + one tradeoff-unit drawn from the same paragraph).

---

## Implementation checklist

When implementing BL-058:

- [ ] Replace `SYSTEM_PROMPT` in `auto_evergreen_extractor.py`
- [ ] Update `extract_concepts` JSON parser for v2 shape (parse `units[]` + `skip_reason`)
- [ ] Update `create_evergreen_note` body template + frontmatter render
- [ ] Add 7 new columns to `objects` table in `truth_store.py`
- [ ] Update `knowledge_index.py` to read new frontmatter into new columns
- [ ] Write `commands/tag_legacy_evergreens.py` (idempotent + reversible)
- [ ] Register `ovp-tag-legacy-evergreens` CLI in `pyproject.toml`
- [ ] Audit event type `evergreen_v2_promoted` writes alongside slug/anchor/etc.
- [ ] Tests per §Testing strategy
- [ ] After merge: run `ovp-tag-legacy-evergreens` once
- [ ] After merge: monitor first incremental for skip_reason rate + parse failures
