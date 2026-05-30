# Stage: L6 RAG read path + automation path (`ovp-rag`, `ovp-auto`)

> Status: **planned** (this doc) → implemented in small commits. Two separate
> surfaces at L6, each on its own crate, built **on top of** the landed L4/L5
> layers and weakening none of L0–L5:
>
> - **`ovp-rag`** — a read-only retrieval surface over `ovp-query::KnowledgeView`.
>   It never assembles, runs, applies, or writes. Retriever + Ranker +
>   ContextBuilder + Eval.
> - **`ovp-auto`** — a one-shot automation sweep that *calls* `ovp-run::RunCycle`
>   per input and then `ovp-lint::Lint`, and emits an operational report. It
>   duplicates none of the assemble/run/apply/rebuild logic.

## The problem

L5 gave the vault a stable read model (`KnowledgeView`) and a health gate
(`Lint`). Two consumers still have no home:

1. **Retrieval.** "Given a question, which concepts/notes are relevant?" The
   knowledge index answers *backlinks*, and `query search` does a literal
   substring scan over slug+title — neither ranks, neither explains *why* a
   result matched, neither bounds a context for downstream use. RAG is more than
   the knowledge index.
2. **Automation.** Running the L4 cycle today is one `--input` at a time by hand.
   An operator dropping N markdown files into an inbox wants one command that
   runs the cycle per file, then lints the result, and reports what happened —
   *without* a second copy of the assemble→run→apply→rebuild logic drifting out
   of sync with `ovp-run`.

Both are L6: they sit above the read model + workflow and compose them. Neither
introduces a new write path, a new authority, or a new async runtime.

## Crate boundaries

Two new crates, one per surface. Both depend only on lower layers; the
dependency graph stays acyclic.

| Crate | Layer | Depends on | Owns | Must NOT |
|---|---|---|---|---|
| `ovp-rag` | L6 (read) | `ovp-query`, `ovp-domain`, `ovp-core`, `serde` | `RagCorpus`, `Retriever`, `Ranker`, `ContextBuilder`, `Eval` + their value types | assemble, run, apply, write, mutate; hold an effect client; require network/LLM for default tests |
| `ovp-auto` | L6 (automation) | `ovp-run`, `ovp-lint`, `ovp-stores`, `serde` | `AutoRun` sweep + `AutoReport` | reimplement RunCycle internals; build wiring/clients itself; write outside `RunCycle` |

Dependency direction (additions in **bold**), acyclic:

```
ovp-cli → { ovp-run, ovp-query, ovp-lint, ovp-rag*, ovp-auto* }
ovp-rag*  → { ovp-query, ovp-domain, ovp-core }
ovp-auto* → { ovp-run, ovp-lint, ovp-stores }      (+ names ovp_run::RunCycleInputs)
```

`ovp-rag` deliberately does **not** depend on `ovp-stores`/`ovp-app`/`ovp-run`:
it reads everything through `KnowledgeView` (concepts, index/backlinks,
`vault_root()`), and reads evergreen note bodies off `vault_root().join(evergreen_path)`
— the same read-only pattern `ovp-lint` already uses to stat evergreen files.

`ovp-auto` deliberately does **not** depend on `ovp-app`/`ovp-domain`/`ovp-llm`.
It receives a fully-built `RunCycleInputs` per input from a **caller-supplied
factory closure** and hands it to `RunCycle::execute`. The factory (which knows
about `DomainPipelineSpec`, `AppWiring`, `build_client`, `ConceptRegistry`) lives
in `ovp-cli`, exactly where that wiring knowledge already lives for `run-cycle`.
This is what keeps `ovp-auto` from duplicating the L4 wiring: it orchestrates
discovery + the loop + lint + the report, and nothing else.

## Public nouns introduced

Kept deliberately small. No synonym of an existing primitive; nothing
speculative. (`Retriever`/`Ranker`/`ContextBuilder`/`Eval` are the four the
brief names; the rest are their plain data shapes.)

**`ovp-rag`:**

| Noun | Kind | One-line |
|---|---|---|
| `RagCorpus` | struct | The loaded, read-only retrieval corpus: a `Vec<ConceptDoc>` built from a `KnowledgeView`. Rebuildable; holds no authority. |
| `ConceptDoc` | struct | One retrievable unit: `slug`, `title`, `evergreen_path`, `provenance_source_url`, `backlinks: Vec<String>`, `body: Option<String>` (evergreen note text, read-only; `None` if the note file is absent). |
| `Retriever` | struct | Deterministic, integer scorer. Holds `RetrievalWeights`. `score(&corpus, query) -> Vec<ScoredConcept>`. No I/O, no float nondeterminism. |
| `RetrievalWeights` | struct | Tunable per-field weights (title/slug token vs. substring, body, backlink). `Default` is the deterministic v1 profile. |
| `ScoredConcept` | struct | `{ slug, score: u32, reasons: Vec<MatchReason> }` — the explanation IS the per-field breakdown. |
| `MatchReason` | struct | `{ field: MatchField, term, hits: u32, contribution: u32 }` — why this term added to the score. |
| `MatchField` | enum | `Title \| Slug \| Body \| Backlink`. |
| `Ranker` | struct | Orders scored results: drop `score == 0` (or `< min_score`), sort by `(score desc, slug asc)`, take `limit`. Deterministic, explainable. |
| `ContextBuilder` | struct | Builds a **bounded** `RagContext` from ranked results: caps concept count, snippet chars, and backlinks per concept. |
| `RagContext` | struct | `{ query, selected: Vec<SelectedConcept> }` — the bounded output object. |
| `SelectedConcept` | struct | `{ slug, title, evergreen_path, score, snippet: Option<String>, backlinks: Vec<String>, reasons }` — a context entry. |
| `Eval` | struct | Offline harness. `Eval::run(&corpus, &retriever, &ranker, &[EvalCase], k) -> EvalReport`. No network, no LLM. |
| `EvalCase` | struct | `{ query, expected: Vec<String> }` — slugs that should appear in the top-k. |
| `EvalOutcome` / `EvalReport` | struct | Per-case recall + the mean; `EvalReport::passed(min_recall)`. |
| `RagError` | enum | `Load(QueryError) \| Body(String)` — fail-loud corpus build (a corrupt read model or an *unreadable* — not merely absent — note body is an error, never silent empty data). |

**`ovp-auto`:**

| Noun | Kind | One-line |
|---|---|---|
| `AutoRun` | unit struct | `AutoRun::sweep(&SweepOptions, make_inputs) -> Result<AutoReport, AutoError>` — the one-shot directory sweep. Mirrors `Lint::check` in shape. |
| `SweepOptions` | struct | `{ inbox_root, vault_root, canonical_root, lint_threshold: Severity }`. |
| `AutoReport` | struct | Operational report: `considered`, `cycles: Vec<CycleOutcome>`, `skipped: Vec<SkippedInput>`, `lint: LintReport`, `lint_passed`, `lint_threshold`. `succeeded()` = all cycles succeeded AND lint passed. |
| `CycleOutcome` | struct | `{ input, run_id, succeeded, reason: Option<String> }` — one file's RunCycle result (reason set on failure). |
| `SkippedInput` | struct | `{ input, reason }` — a discovered file not run (v1: empty/whitespace-only markdown). Logged, never silent. |
| `AutoError` | enum | `Discovery(String)` — the inbox could not be read/walked (fail-loud; never "0 files"). Per-input RunCycle failures are captured as failed `CycleOutcome`s, not this error. |

The `make_inputs` factory is typed `FnMut(&Path) -> Result<RunCycleInputs, String>`
— `ovp-auto` only *names* `ovp_run::RunCycleInputs`; the CLI *constructs* it.

## Data flow

**RAG read path (read-only, no writes anywhere):**

```
vault_root + canonical_root
        │
        ▼
ovp_query::KnowledgeView::load   (L5: authority + index, fail-loud)
        │  concepts / backlinks / vault_root()
        ▼
RagCorpus::from_view             (+ read evergreen bodies off disk, read-only)
        │  Vec<ConceptDoc>
        ▼
Retriever::score(query)  ──►  Vec<ScoredConcept> (+ MatchReasons)
        ▼
Ranker::rank             ──►  top-k ScoredConcept, deterministic order
        ▼
ContextBuilder::build    ──►  RagContext  (bounded: concepts, snippet, backlinks)
        ▼
`ovp-next rag` prints text or --json        Eval::run gates it offline
```

**Automation path (the only writes go through `RunCycle` → `PlanApplier`):**

```
inbox_root  ── walk_markdown (ovp-stores, fail-loud) ──► [(path, content)]
        │   (empty content → SkippedInput, logged)
        ▼  for each input path:
   make_inputs(path)  ──►  RunCycleInputs   (CLI factory: spec + wiring + client)
        ▼
   ovp_run::RunCycle::execute        (L4: assemble→run→apply→rebuild MOC+index)
        ▼  RunCycleReport.succeeded() → CycleOutcome
   …after all inputs…
        ▼
   ovp_lint::Lint::check(vault_root, canonical_root)   (L5 health gate)
        ▼
   AutoReport (text or --json); exit non-zero if any cycle failed OR lint failed
```

`ovp-auto` calls `RunCycle::execute` and `Lint::check`. It contains **zero**
assemble/run/apply/rebuild logic of its own.

## `ovp-rag` — retrieval model (v1, deterministic + explainable)

- **Corpus.** `RagCorpus::from_view(&KnowledgeView) -> Result<RagCorpus, RagError>`.
  One `ConceptDoc` per canonical concept (already slug-sorted). `backlinks` from
  `view.backlinks(slug)`. `body` from reading `vault_root().join(evergreen_path)`:
  `Ok(s)→Some`, `NotFound→None`, any other I/O error → `RagError::Body` (loud).
- **Scoring.** Integer only (no floats in the hot path → no nondeterminism, no
  clippy float-cmp). Query is lowercased + tokenized on non-alphanumerics. For
  each `(doc, term)`: title-token / title-substring / slug-token / slug-substring
  / body-occurrence(capped) / backlink-path-substring each add their weight and
  record a `MatchReason`. `score = Σ contributions`.
- **Ranking.** Drop `score == 0`; sort `(score desc, slug asc)` (slug tie-break =
  total order, reproducible); take `limit`.
- **Context.** `ContextBuilder` caps: ≤`max_concepts`, snippet ≤`max_snippet_chars`
  (first chars of body, trimmed on a char boundary), ≤`max_backlinks` per concept.
  Output is a bounded `RagContext` — safe to hand to a downstream LLM prompt
  later **without** this crate ever calling one.
- **Eval.** `EvalCase { query, expected_slugs }`. `Eval::run` retrieves top-k,
  computes recall per case + mean. Fixtures are seeded in-test (canonical store +
  evergreen notes in a tempdir, or a committed tiny fixture set) with known
  expected targets. Fully offline.

## `ovp-auto` — automation sweep (v1, one-shot, sync)

1. **Discover.** `walk_markdown(inbox_root)` (the same fail-loud helper L4/L5
   use). Unreadable inbox → `AutoError::Discovery` (never silently "0 files").
   Deterministic order.
2. **Per input.** Empty/whitespace file → `SkippedInput` (logged). Else
   `make_inputs(abs_path)` → `RunCycleInputs`; `RunCycle::execute` → record a
   `CycleOutcome { succeeded, reason }`. A factory error or a non-succeeding
   report is a failed cycle (loud `reason`), not a silent skip.
3. **Lint once.** After all inputs: `Lint::check(vault_root, canonical_root)` →
   `LintReport`; `lint_passed = report.passed(threshold)`.
4. **Report.** `AutoReport::succeeded()` = every cycle succeeded AND lint passed.
5. **One-shot, sync.** No async runtime, no watcher daemon in v1 (a future
   `--watch` poll loop can wrap `sweep`; not needed now and explicitly out of
   scope — see non-goals).

## CLI surfaces

```
ovp-next rag --vault-root V --canonical-root C --query "..." [--limit N] [--json]
```
Read-only. Loads `KnowledgeView`, builds the corpus, retrieves → ranks → builds a
bounded context, prints it (text or `--json`). Exits **non-zero** on a corrupt /
unreadable read model (`QueryError`) or an unreadable note body (`RagError`); a
valid query with zero results exits 0.

```
ovp-next auto-run --inbox-root I --vault-root V --canonical-root C \
    [--manifest M] [--cache-dir D] [--concept-registry R] [--run-id ID] \
    [--date YYYY-MM-DD] [--client replay|live] [--max-severity error] \
    [--dry-run] [--json]
```
Discovers markdown under `--inbox-root`, runs the L4 cycle per file (replay
client by default — offline), lints the result, prints the operational report
(text or `--json`). Exits **non-zero** if any cycle failed or lint failed at
`--max-severity`. The CLI builds the per-input `make_inputs` factory; `ovp-auto`
runs the sweep.

## Boundaries held

- **RAG is read-only.** No `PlanApplier`, no `WritePlan`, no `fs::write` in
  `ovp-rag`. It reads concepts/backlinks via `KnowledgeView` and note bodies via
  `vault_root()` (read-only), nothing else. Invariants #10/#11 untouched.
- **Automation owns no workflow logic.** It calls `RunCycle::execute` and
  `Lint::check`. All mutation still flows `WritePlan → CompositePlanApplier`
  inside L4. No second assemble/apply/rebuild path exists (invariant #10).
- **No async, anywhere new.** Both crates are sync. `ovp-core` stays untouched
  (invariant #6). No `tokio`/`futures`.
- **No new authority.** RAG's corpus is derived + rebuildable from the read model
  (invariant #11). Canonical identity stays in `ConceptRegistry` + the canonical
  store.
- **Fail-loud, never silent-empty.** Corrupt read model → `RagError::Load`;
  unreadable note body → `RagError::Body`; unreadable inbox → `AutoError::Discovery`;
  a non-succeeding cycle → a failed `CycleOutcome` with a reason.
- **No legacy shell-out, offline default tests.** No `Command::new("python"/"ovp")`;
  no network; replay cassettes + tempdirs only. `cargo test` needs no API key.
- **Terminology stays small.** No new primitive shadows an existing one; the
  `architecture.md` deprecated-vocabulary table is unchanged (no new term forced).

## Acceptance tests

`ovp-rag`:
1. Build a corpus from a seeded `KnowledgeView` (concepts + index + evergreen
   note files) → `ConceptDoc`s carry slug/title/backlinks/body.
2. A query matching a concept's title/slug ranks it first; `MatchReason`s explain
   the score (title-token vs. body hit distinguishable).
3. Ranking is deterministic: equal scores break by slug; `limit` honored.
4. `ContextBuilder` bounds output: concept count, snippet length, backlink count
   all capped; snippet cut on a char boundary.
5. **Read-only:** a corpus build + full retrieve over a tempdir vault writes
   nothing (assert both roots unchanged).
6. Corrupt canonical store → `RagError::Load`; an unreadable note body →
   `RagError::Body` (loud, not an empty corpus).
7. `Eval::run` over fixtures with known expected slugs → recall 1.0; `passed`
   gates correctly.

`ovp-auto`:
8. Sweep a temp inbox with two replayable fixture inputs → two succeeded
   `CycleOutcome`s, vault + canonical + MOC + index written by L4, `succeeded()`.
9. Idempotent: a second sweep over the same roots → cycles still succeed, L4
   applies nothing new (idempotence is L4's; auto just reports it).
10. An empty markdown file → `SkippedInput` (logged), not a failed cycle.
11. An unreadable inbox → `AutoError::Discovery` (fail-loud; not "0 considered").
12. A run that leaves the vault failing lint at the threshold → `lint_passed ==
    false` and `succeeded() == false` even though the cycles ran.
13. CLI `auto-run` exits non-zero when a cycle fails or lint fails; zero on a
    clean sweep. (Covered via the library report + a thin CLI smoke.)

## Non-goals (explicit)

- **No live LLM in RAG or in default tests.** No embeddings/vector store, no
  semantic model — v1 retrieval is deterministic lexical scoring over the read
  model. (An embedding ranker is a future `RetrievalWeights`-shaped extension,
  not v1.)
- **No watcher daemon / async / filesystem notifications.** v1 automation is a
  one-shot sweep. A polling `--watch` is a thin future wrapper over `sweep`; not
  built now.
- **No new write path.** Automation writes only via `RunCycle`. RAG writes never.
- **No re-implementation of assemble/run/apply/rebuild** in `ovp-auto`.
- **No legacy Python/`ovp` subprocess**, no `pyo3`.
- **RAG does not fix or mutate the vault** (that's L3/L4); it only reads.

## Stop conditions honored

Implementation stops and reports if any of these become true (none expected):
L6 would require weakening an L0–L5 invariant; a write path outside
`RunCycle`/`PlanApplier` is needed; RAG would need a live LLM/network for default
tests; or `ovp-auto` would have to duplicate `RunCycle` internals.
