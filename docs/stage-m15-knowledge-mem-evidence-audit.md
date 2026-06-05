# M15 Phase 0 — KnowledgeMEM evidence audit (read-only)

> **Gate result: the M15 protocol is NOT invalidated.** Every assumption in
> `docs/stage-m15-methodology-audit.md` (memory-first / complexity-after-memory;
> entity/concept/crystal are post-processing; **no quote/evidence/span field on a
> memory**) is affirmatively corroborated by recovered code + runtime outputs +
> the RUNBOOK. Per-claim provenance labels: `recovered_code` (seen in
> `~/workspace/nowledge-test/unpack/{recovered,decrypted}/…`), `runtime_output`
> (an actual `.run/.../source-detail.json`), `runbook` (`RUNBOOK.md`), `inference`.
> The research doc was treated as secondary and re-verified against primaries.

## Q1 — Source-extraction prompt shape

- `recovered_code` — Extraction is **tool-driven, not a strict output schema**.
  `build_source_extraction_prompt()` dispatches on mime → `_build_document_
  extraction_prompt` (a multi-step agentic *Read→Search→Extract→Link→Synthesize*
  workflow). Extraction = repeatedly calling the **`CreateMemory` tool**; the model
  emits a free-text `[LEARNING_REPORT]` at the end plus side-effecting tool calls.
  No JSON the model must emit. (`unpack/recovered/.../agent/task_prompts.py:604-650`)
- `recovered_code` — It asks for a **bounded count**: "Extract **3–8** distinct,
  high-quality memories per document (not too few, not noise)"; tabular variant
  "Create 2–5 analytical insights". (`task_prompts.py:626`, `:583`)
- `recovered_code` — Hard runtime ceiling `_MAX_MEMORIES_PER_TASK = 15` with a
  per-task counter, distinct from the soft 3–8 guidance.
  (`agent/knowledge_tools/mutations.py:79-87`)
- `recovered_code` — The prompt's **only provenance instruction** is
  "Set `source_id='{source_id}'` so memories link back to the source." **No
  instruction to capture a verbatim quote, char offset, or page span.**
  (`task_prompts.py:618`, `:565`)

## Q2 — `CreateMemory` / `CreateEVOLVES` / `CreateCrystal` schemas

- `recovered_code` — **`CreateMemoryParams`**: `content: str` (req), `title: str`
  (req, ≤200), `unit_type: str` (default `fact`), `importance: float` (0.6, 0–1),
  `labels: Optional[List[str]]` (≤5, lowercase-hyphenated), `source_id`,
  `source_thread_id`, `space_id`. (`mutations.py:89-100`)
- `recovered_code` — **`unit_type` is an 8-value enum**:
  `fact | learning | decision | procedure | preference | plan | context | event`
  (default `fact`; "when uncertain, keep as fact"). Corroborated by
  `_VALID_UNIT_TYPES`, the v0.6 migration, and the reclassification prompt.
  (`mutations.py:79`, `migrations/.../20260204_170000_v06_*.py:14`, `task_prompts.py:454-465`)
- `recovered_code` — **`CreateEVOLVESParams`**: `older_memory_id`, `newer_memory_id`,
  `content_relation` ∈ `{confirms, enriches, replaces, challenges}`, `confidence`
  (0.8), `reason`. Direction older→newer; `replaces` sets the older's
  `is_latest=false`. (`mutations.py:16-31`)
- `recovered_code` — **`CreateCrystalParams`**: `title`, `content` ("Synthesized,
  context-independent knowledge"), `source_memory_ids: List[str]` (req), `labels`,
  `space_id`. (`mutations.py:47-59`)
- `recovered_code` — **THE LOAD-BEARING FACT: no per-memory quote / evidence /
  source-span / verbatim-excerpt field exists** in any of the three param classes.
  A grep of `knowledge_tools/` for `quote|evidence|source_span|verbatim|excerpt|
  provenance|span` returns a single hit — `source_thread_id` (a thread *id*, not
  source text). **Provenance is by IDs, never by quoted source text.**

## Q3 — Actual runtime memories (the 6 captured cases)

- `runtime_output` — Memory counts: rag-wrong **8**, eval-ai-agents **14**,
  agent-memory-zh **14**, graphrag-paper **5**, fde-zh **7**, adapt-claude-code
  **5** (each matches `source.memory_count`).
- `runtime_output` — Memory object fields, m12q2 cases:
  `id, title, content, chunk_index, chunk_range, unit_type, confidence`. eval-path
  cases are SMALLER: `id, title, content, unit_type` only. **In neither shape is
  there a quote / original-sentence / span field** — consistent with Q2.
- `runtime_output` — observed `unit_type` values across the 6 files: `{fact,
  procedure, learning}` (subset of the 8-enum).

## Q4 — Entity / KG / Crystal timing

- `runbook` — 4-phase pipeline: **P1 Ingest → P2 Source extraction (≈3 min/src,
  creates *memory*, auto `SOURCED_FROM`) → P3 KG/community/crystal (chain-triggered,
  10–30 min) → P4 验证+补漏**. Memory is created at P2; entity/KG/crystal at P3/P4.
- `recovered_code` — `source_pipeline.py` lifecycle docstring:
  "Ingest → Parse → Chunk → Index → (Agent Extraction later)". KG/entity is a
  separate agent task (`POST /agent/trigger/kg-extraction`); community + crystal are
  separate trigger endpoints.
- `recovered_code` — **`Concept` is just `EntityNode(entity_type='concept')`** (one
  `EntityNode` class), and **`Crystal` is just `MemoryNode(is_crystal=true)`** (the
  v0.6 migration adds `is_crystal`/`crystal_title`/`source_unit_count` to the MEMORY
  table) linked via `CRYSTALLIZED_FROM`. Neither is a first-class extraction-time
  node.

## Q5 — Known gaps / uncertainties

- The decrypted `.py` are signature/docstring stubs with **string literals
  stripped**, so exact stored `entity_type` literals and the KuzuDB node-table DDL
  are confirmed only via docstrings/migration text, not live values (`inference`).
- Whether the 200-char `content` in `source-detail.json` is a storage cap or a
  projection truncation is **not determinable from outputs** (`unknown`).
- The two output schemas (m12q2 vs eval-path) diverge because they come from
  different projections (`…/nowledge/` vs `…/pack/nowledge/`); not a storage fact
  (`inference`).

## Producibility of the KMEM arm (Phase-3 feasibility)

- `recovered_code` — A capture driver exists: `.run/m12q2/capture_nowledge.py`
  drives a **generic** flow against `BASE=http://127.0.0.1:14242`:
  `POST /sources/ingest/file-path` with an **arbitrary** `{file_path}` (idempotent,
  sha256-dedup) → `GET /sources/{id}` → writes `source-detail.json` + `memories.*`.
  So the KMEM arm **can** be produced for fresh sampled articles.
- `inference` / operational — **REQUIRED CONDITION (the real blocker): a local
  Nowledge Mem service must be running at `127.0.0.1:14242`** (`./start.sh` or
  `/Applications/Nowledge Mem.app`), with the token quota raised. **Probed during
  this audit: the service is DOWN** (`curl :14242` → `000`, no process). The auditor
  did not start it (read-only). Starting it is an operator action.

## Conclusion

Phase 0 does NOT contradict the M15 assumptions → **proceed**. The only thing
between here and a full N=12 both-arms run is **operational**: the Nowledge Mem
service must be up so `capture_nowledge.py` can produce the KMEM arm on the sampled
articles. See `docs/m15/sample-manifest.md` (Phase 1) and the blocker report.
