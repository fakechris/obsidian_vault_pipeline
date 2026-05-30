# Stage: L5 Read / Health Layer (`ovp-query`, `ovp-lint`)

> Status: **landed** — both `ovp-query` and `ovp-lint`. The read/health layer
> over the canonical store + knowledge index + vault. Read-only: it never
> mutates, never assembles, never runs a pipeline. This is the layer RAG (L6)
> reads from.

## The problem

L4 produces a coherent vault: canonical records, evergreen notes, a MOC, a
knowledge index. But nothing *reads* it back as a stable surface. A consumer
(a human, a future RAG retriever, an autopilot) has to re-open the canonical
store, re-parse the index, and re-derive backlinks by hand — the same way the
`run-cycle` derived rebuild does. L5 gives that read a home, and adds the health
checks that tell you whether the derived state is actually trustworthy.

Two tools, one layer, one shared read model:

- **`ovp-query`** — answer questions over the existing state: list concepts, look
  one up, search, show backlinks. Stable API + CLI. Pure read.
- **`ovp-lint`** — health checks over the same state: canonical payload validity,
  evergreen-file existence, MOC/index freshness, broken wikilinks, orphan
  canonical records, missing notes. Reports findings; **never fixes** (a fix is
  a write, and writes go through L3/L4, not here).

## Shared read model: `KnowledgeView`

One snapshot type, loaded once, that both tools read:

```rust
pub struct KnowledgeView {
    concepts: Vec<CanonicalConcept>,   // authority: canonical store (strict parse)
    index: Option<KnowledgeIndex>,     // derived: 60-Logs/knowledge-index.json, if present
    vault_root: PathBuf,               // for on-disk existence / backlink checks
}
```

Load path (read-only, fail-loud on corruption):
1. `CanonicalFsStoreApplier::read_all(canonical_root)` → `CanonicalConcept::try_parse_pairs` (strict: bad payload / key≠slug / invalid slug / wrong evergreen_path → `QueryError::CanonicalParse`; an I/O read error → `QueryError::CanonicalRead`).
2. Read `<vault>/60-Logs/knowledge-index.json`:
   - present + parses → `Some(index)`;
   - **absent** (`ErrorKind::NotFound`) → `None` (an un-rebuilt vault is queryable, just backlink-less);
   - present but **unreadable** (any other I/O error — permission, transient) → `QueryError::IndexRead` (loud — *unreadable ≠ absent*);
   - present but **unparseable** → `QueryError::IndexParse`.

The **canonical store is the authority** for which concepts exist; the index is a
derived convenience for backlinks. `KnowledgeView` lives in `ovp-query`;
`ovp-lint` depends on `ovp-query` to load it (no duplicate loader).

## `ovp-query` — operations (v1)

| Op | Returns |
|---|---|
| `concepts()` | all canonical concepts, slug-sorted |
| `get(slug)` | the concept (title, evergreen_path, provenance) or `None` |
| `search(needle)` | concepts whose slug or title contains `needle` (case-insensitive), slug-sorted |
| `backlinks(slug)` | vault-relative note paths referencing the concept (from the index; empty if no index/entry) |
| `stats()` | concept count, index-present flag, total backlinks, concepts-with-zero-backlinks |

All results are serializable so the CLI can emit `--json`.

### CLI

```
ovp-next query --vault-root V --canonical-root C [--json] <KIND> [TERM]
KIND = list | get | search | backlinks | stats
TERM = slug (get/backlinks) or substring (search); ignored for list/stats
```

Exit non-zero on a load error (corrupt canonical / index) so a broken store is
loud; a successful query with zero results exits 0.

## `ovp-lint` — checks (next, designed here)

Each check yields zero or more `LintFinding { severity, code, detail, location }`.
Read-only. Planned v1 checks:

| Code | Severity | What |
|---|---|---|
| `canonical.unreadable` / `canonical.unparseable` | error | the canonical store can't be read / a record fails strict parse (a load failure surfaced as a finding, not an abort) |
| `index.unreadable` / `index.unparseable` | error | the persisted index exists but can't be read / doesn't parse (load failure → finding) |
| `vault.scan_failed` | error | walking the vault for markdown failed — the health gate must NOT pass on an unreadable vault (never silently "no backlinks / no broken links") |
| `evergreen.missing_note` | error | a canonical concept's `evergreen_path` does not exist on disk |
| `index.stale` | warning | the persisted knowledge index ≠ a freshly-built one (structural comparison; whitespace edits are not "stale") |
| `index.absent` / `moc.absent` | warning | no knowledge index / MOC exists yet |
| `moc.stale` | warning | the persisted MOC ≠ a freshly-rendered one |
| `wikilink.broken` | warning | a `[[target]]` resolving to no canonical concept or vault note |
| `canonical.orphan` | info | a canonical concept with zero backlinks (nothing references it) |

The vault is walked **once** per lint pass (shared by the staleness + broken-link
checks); the backlink map is built with `ovp-stores::backlinks_from_files` — the
**same** helper the run-cycle's `scan_backlinks` uses, so L4 and L5 can't diverge.

`ovp-lint` exits non-zero if any finding is at/above a severity threshold
(`--max-severity`, default `error`), so it can gate CI. It proposes no fixes;
remediation is re-running `run-cycle` (which rebuilds derived state) or fixing the
vault by hand.

## Crate placement

Two new crates at L5:
- `ovp-query` — depends on `ovp-domain`, `ovp-stores`, `ovp-core`. **Not** `ovp-app`/`ovp-run` (no assembly, no run).
- `ovp-lint` — depends on `ovp-query` (for `KnowledgeView`) + `ovp-domain`, `ovp-stores`, `ovp-core`.

Dependency direction stays acyclic and matches the layer model:
`ovp-cli → {ovp-run, ovp-query, ovp-lint}`; `ovp-lint → ovp-query → {ovp-domain, ovp-stores, ovp-core}`.

## Boundaries held

- **Read-only.** No `PlanApplier`, no `WritePlan`, no `fs::write` anywhere in L5
  (the CLI's `--json` goes to stdout; only an explicit `--report`-style dump, if
  added later, would write, and that lives in `ovp-cli`).
- No `ovp-core` domain knowledge added; L5 composes existing types.
- Fail-loud on a corrupt store — a query/lint over unparseable canonical state is
  an error, not silently-empty results.
- No subprocess to legacy Python; no async; default tests need no network and
  never touch a real vault (tempdirs only).

## Acceptance tests

`ovp-query`:
1. load a seeded canonical store + index → `concepts()`/`get()`/`search()`/`backlinks()`/`stats()` return the expected values.
2. corrupt canonical store → `load` returns `QueryError::CanonicalParse` (loud).
3. canonical store present, no index → loads, `backlinks()` empty, `stats().index_present == false`.
4. **unreadable** index (present but not a readable file) → `QueryError::IndexRead` — *unreadable ≠ absent*.
5. round-trip through a real `run-cycle` output: query the vault a `run-cycle` just produced and confirm a known concept + its article backlink.

`ovp-lint`:
6. a vault with a missing evergreen note → `evergreen.missing_note` (error).
7. a stale index (a concept added to canonical, index not rebuilt) → `index.stale`.
8. a broken `[[wikilink]]` → `wikilink.broken`.
9. a corrupt canonical store → a single `canonical.unparseable` finding (no abort/panic).
10. an **unreadable vault** (unreadable subdirectory) → `vault.scan_failed` (error) — the gate does NOT pass.
11. a clean `run-cycle` output → zero findings at/above `error`.
