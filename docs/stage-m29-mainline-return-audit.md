# Stage M29 — Legacy Mainline Return Criteria + Rust Codebase Health Audit

**Type:** Audit + design decision (no new product features).
**Repo confirmed:** `/Users/chris/Documents/obsidian-vault-pipeline`, branch `codex/rust-migration`,
HEAD `a7fd390`, `origin/main` present. Correct tree — not `ovp-next`, not a stale checkout.
**Date:** 2026-06-08.

> Companion matrix: [`docs/mainline-return-matrix.md`](./mainline-return-matrix.md).
> Constraint honored: KnowledgeMem not inspected, no KMEM credentials used, no `.run`/cassette/
> secret/dump touched or committed.

---

## 0. Verification (ran)

| Check | Result |
|---|---|
| `cargo metadata --no-deps` | 13 workspace crates, internal dep graph clean (below) |
| `cargo test --workspace` | **556 passed, 1 ignored, 0 failed** (49 bins) |
| `cargo clippy --workspace --all-targets -- -D warnings` | **0 warnings** |
| `bash scripts/check_architecture.sh` | **passed** (all 14 grep-gated invariants) |

Nothing was skipped. Build/test/lint/architecture gates are all green.

---

## 1. The core finding (read this first)

The Rust branch did **not** spend M14–M28 re-cloning legacy. It deliberately **pivoted**. There are
now effectively two candidate products:

- **(A) Legacy OVP** — a vault knowledge-management *pipeline*: Capture (Pinboard / Clippings /
  GitHub / papers) → Absorb → Evergreen → Canonical → MOC → `knowledge.db` → Reader/Ops **web UI**,
  `ovp-ask` / `/digest` / working-memory, `ovp-autopilot` daemon. 84 commands, SQLite projection
  backbone, governance control plane.
- **(B) Rust trunk** — a grounded **reader + durable Crystal truth layer**: `read-source`
  (Source → Grounded Units → Critic Repair → Reader Cards → Reader Pack, M14a–M20, 20/20 held-out,
  `accepted_without_quote=0`) → Crystal pre-write gates (`crystal-lint`) → append-only durable store
  (`crystal-write`) → article-level review console (M26/M28). This is **net-new** and, on the M26
  article-level AB, **ahead of the KMEM baseline** (17 ovp_better / 3 tie / 0 loss).

(B) exists because the heart of (A) — eager concept/canonical/MOC extraction — was found to be the
**wrong root** (0/3 on real models across ~8 milestones) and was **demoted**. The demoted M7–M13
machinery still builds and tests, but it is not the blessed path.

**Consequence for "return to main":** Rust is not "legacy minus a few commands." It is a different,
better-grounded vertical that does **not yet cover legacy's daily product**. Mainline return is
therefore blocked less by code health (which is good) and more by a **product-scope decision**: does
the new mainline keep legacy's (A) daily pipeline, or replace it with (B) plus a *minimal* daily
loop? See §8.

---

## 2. Legacy capability inventory

Grouped product-level (full per-row status in the matrix). Legacy state model is
Source → Candidate → Canonical State → Projection → Access Surface, with Governance cross-cutting.

| Group | Legacy reality | Must-preserve before default? |
|---|---|---|
| Daily capture / ingest | Pinboard, Clippings, raw MD, papers, GitHub, web; URL-dedup staging set | **Yes (P0)** — without intake nothing flows |
| Source lifecycle / file movement | 5-stage L0–L4 staging, VaultLayout, image download, archive | **Yes (P0)** |
| Article / paper / clipping | article 深度解读, paper deep-dive (papers = only surviving LLM deep-dive) | Paper yes; article path redesigned |
| Evergreen / canonical / absorb / crystal | absorb→evergreen→canonical→crystal, promotion lanes | Absorb **P0**; legacy crystal redesigned |
| Query / ask / digest / working-memory | `query_tool`, `ovp-ask`+chats, `/digest`, working-memory | P1 (Reuse surfaces) |
| UI / dashboard / review | Reader shell + `/ops` maintainer shell web server, FTS, atlas/graph | **needs-decision** |
| Automation / autopilot / daemon | `ovp-autopilot` watch=inbox, `ovp --full/--incremental`, task dispatch | P1 |
| Quality / lint / truth / provenance | WIGS lint, evidence verify/replay, contradictions, doctor | Lint covered; rest P1/deferred |
| State stores / knowledge.db / indexes / audit | SQLite `knowledge.db` (FTS5 + embeddings + audit + ops_state + truth_projections), `txn.py` ledger | **Yes (P0 / needs-decision)** |
| Integration surfaces | MCP, GitHub, Pinboard, browser clipper, export | Pinboard/clipper P0; MCP needs-decision |

## 3. Rust current capability inventory

| Area | Rust module/crate | Classification |
|---|---|---|
| Reader trunk (`read-source`) | `ovp-domain::reader` + `units/` | **product trunk** (validated) |
| Grounded units / critic repair | `ovp-domain::units` (2746 lines) | **product trunk** |
| Reader cards / packs / M28 console | `reader/` + `scripts/m26_*`, `scripts/m28/*` | product trunk (Rust) + presentation scripts (diagnostic) |
| Crystal gates / durable store | `ovp-domain::crystal` (1090) + `crystal-{lint,write,review}` | **product trunk** (net-new) |
| run-cycle / canonical / MOC / knowledge-index | `ovp-run`, `canonical.rs`, `moc.rs`, `knowledge_index.rs`, `transforms/` | **partial, demoted** (builds+tests, off blessed path) |
| Evergreen mint/reconcile | `evergreen*.rs`, `transforms/evergreen_concept_writer.rs` | partial, demoted |
| ConceptRegistry / resolver | `concept_registry.rs` | partial, demoted |
| RAG / query / lint / auto-run | `ovp-rag` (lexical), `ovp-query`, `ovp-lint`, `ovp-auto` | diagnostic/read-only; thin vs legacy |
| Referent candidates (M14b) | `ovp-domain::referents` (1446 lines) | **historical/dead path** (demoted, 0/3) |
| KMEM comparator | `ovp-eval` (`compare.rs`, `nowledge.rs`) | **diagnostic/eval only** (gate-fenced above trunk) |
| concept-map v2 | `tests/concept_map_v2_synthetic.rs` + demoted transforms | historical/dead path |

**No SQLite, no embeddings, no web server, no MCP, no daemon, no durable txn ledger** anywhere in
the Rust tree (grep-confirmed, not doc-inferred).

## 4. Architecture health audit

Strong. The original direction is intact.

- **`ovp-core` minimal, domain-blind, IO-blind:** ✅ zero internal deps; gated against
  `serde_json::Value`, `HashMap<String,_>` payloads, async/tokio, `Command::new(python|ovp|sh…)`,
  pyo3, and `ovp_pipeline` imports.
- **Effects behind explicit boundaries:** ✅ `ovp-llm` is a trait + impls; HTTP lives only in
  `ovp-llm` (anthropic feature) and `ovp-eval` (the KMEM comparator). **`ovp-domain` holds no
  HTTP/reqwest** — its `ovp-llm` dependency is type/trait-only (confirmed).
- **Writes routed through plans/stores:** ✅ `WritePlan` → `CompositePlanApplier` (halts on first
  failure) → `VaultFsPlanApplier` / `CanonicalFsStoreApplier`; Crystal writes go through the
  append-only `ledger.jsonl` with `claim_key` idempotency and gate refusal. ⚠️ but durable
  **run/transaction persistence is in-memory only** (`EventLog`) — a real gap before any live
  multi-step run is safe (see P0).
- **Reader/Crystal path clear vs old M13/M14 mixing:** ✅ **strong positive** — `reader/` and
  `crystal.rs` import **none** of `referents` / `concept_registry` / `canonical` / `moc` /
  `knowledge_index` / `evergreen`. The new trunk is import-isolated from the demoted substrate;
  `crystal.rs` is consumed only by the `crystal-*` CLI commands.
- **Hidden coupling from the M19–M28 fixes:** none found. JSON-repair / budget-retry live in
  `model_reply.rs` + `ovp-llm`; render fidelity in `source_map.rs`; crystal gates in `crystal.rs`;
  dashboards in `scripts/` (outside Rust). No fix reached back into `ovp-core`.
- **Doc/code consistency:** ✅ `README.md` and `docs/architecture.md` are honest and current —
  both explicitly state Rust is **not** feature-equivalent to legacy and that M7–M13 is demoted;
  `architecture.md` carries the M18–M26 reader-trunk narrative. ⚠️ **drift:** (1)
  `docs/legacy-alignment.md` predates the pivot (still frames M7–M13 canonical store as the P0
  roadmap) — now superseded by this matrix; (2) `architecture.md`'s reader-trunk note stops at M26
  (no M27/M28). Both are small doc fixes, not code problems.

## 5. Layer / dependency-direction review

Internal dep graph (from `cargo metadata`):

```
ovp-core  → (nothing)                      kernel
ovp-llm   → (nothing)                      effect trait
ovp-domain→ core, llm                      types + transforms
ovp-stores→ core, domain, llm              appliers
ovp-app   → core, domain, llm, stores      assembly
ovp-run   → app, core, domain, stores, llm L4 cycle
ovp-query/lint/rag/review/auto → trunk     L5/L6 read+orchestrate
ovp-eval  → app, core, domain, rag, review, llm   (ABOVE trunk)
ovp-cli   → everything incl. ovp-eval      composition root
```

- **Trunk → eval reverse edge:** ✅ explicitly gated — `check_architecture.sh` fails if any of the
  11 trunk crates depend on `ovp-eval`. The KMEM comparator cannot leak into the product.
- **UI/dashboard logic outside Rust:** ⚠️ the M26/M28 dashboards are **Python scripts** over `.run`
  artifacts. Fine as a *review console*; flag if it becomes the product UI (logic would ossify in
  scripts, outside the gated core).
- **CLI as god object:** ⚠️ `ovp-cli` depends on all 12 crates and exposes ~18 subcommands spanning
  three product lines (demoted canonical cycle / reader+crystal trunk / eval+diagnostic). Mitigated
  by thin command files and gated assembly, but product and diagnostic verbs sit undifferentiated.
- **`ovp-domain` accumulating unrelated concepts:** ⚠️ **yes** (see §6).
- **`DomainBody` dumping ground:** acceptable today (`Source`/`Prompt`/`Model`/`Interpreted`/
  `InterpretedPaper`) — watch as new bodies land.
- **NodeRegistry / GraphAssembler as business logic:** ✅ assembly-only, no authority/runtime reads
  (gated).
- **Crystal store becoming a graph/entity/RAG store by accident:** ✅ no — it is append-only JSONL
  claims with citation chains; no entities, edges, or embeddings.

## 6. God-object / dumping-ground risk

| Suspect | Size / responsibilities | Verdict | Action |
|---|---|---|---|
| **`ovp-domain`** | 4065 top-level + `units/`2746 `transforms/`3157 `referents/`1446 `reader/`747; holds **both** live trunk (units/reader/crystal/model_reply) **and** demoted M7–M13 (referents/concept_registry/canonical/moc/knowledge_index/evergreen) | **Dumping ground by accumulation** (cohesive internally, but mixes two product generations) | **Quarantine** the demoted substrate into a clearly-marked `legacy`/`substrate` module or sibling crate (`ovp-substrate`); keep `reader`/`units`/`crystal` as the trunk crate's spine |
| **`referents/`** (1446) | M14b referent candidates, demoted, 0/3 | **Dead path** | **Delete or feature-gate** behind `--features legacy-substrate`; it is the single clearest removal candidate |
| **`crystal.rs`** (1090) | gate + store + review + render in one file | Cohesive but large | Watch; split `gate / store / render` if it grows past ~1.3k |
| **`ovp-cli` / `main.rs`** (853, ~18 verbs) | composition root across 3 product lines | Breadth god-object, contained by thin commands | **Group + label** diagnostic/eval verbs (`compare-run`, `copy-probe`, `extract-referents`) distinctly from product verbs |
| **`ovp-eval`** | KMEM comparator | Properly fenced (reverse-edge gated) | Leave alone |
| **M28 `scripts/`** | dashboard/product-surface logic in Python | Diagnostic today | Flag if promoted to product UI |

## 7. Mainline readiness levels

### Level 1 — keep as the active Rust development branch in this repo
**Verdict: PASS (clearly).**
Criteria, all met: builds; 556 tests pass / 0 fail; clippy `-D warnings` clean; architecture
invariants pass; kernel minimal; trunk import-isolated from demoted substrate; docs honest about
status; no secret/`.run`/KMEM leakage; eval fenced from trunk.

### Level 2 — make Rust the default development line
**Verdict: NOT YET.** Blocked on a usable daily workflow on the real operator vault. Required:
1. **Real intake** — Pinboard/Clippings/inbox watch + filename normalize + global URL dedup. *(P0, missing)*
2. **Source lifecycle** — staging set L0→L4 file movement + `VaultLayout` wired into sources/sinks. *(P0, partial)*
3. **A blessed write path** end-to-end on real input (intake → note/evergreen → derived index) that a human runs daily — *either* by promoting a slimmed M7–M13 cycle *or* by wiring reader/crystal as the output surface. *(P0, needs-decision)*
4. **Durable run ledger + audit** replacing in-memory `EventLog`, so a partial live run is resumable/auditable. *(P0, missing)*
5. **A persistent read index** good enough for daily query/lint (decide fs-projection vs SQLite). *(P0, needs-decision)*
6. Green gates remain (tests/clippy/arch) + a smoke run on a copy of the real vault.

### Level 3 — deprecate / delete the Python mainline
**Verdict: DEFINITELY NOT.** Additionally requires: knowledge.db-equivalent projection (or an
explicit drop decision) covering query/ops_state/doctor/working-memory; the Reuse surfaces
(`ovp-ask`/`/digest`/working-memory) or explicit drops; web UI / MCP decision resolved and shipped
or dropped; `ovp-autopilot` daemon or explicit drop; migration of (or sign-off to abandon) existing
`knowledge.db` + vault state; 2+ weeks of real daily dogfood on Rust with no fallback to Python.

## 8. Verdict & next stage

**Can Rust return to main now?** **No** — and it should stay `codex/rust-migration` (Level 1) until
a daily product loop exists. The blocker is **product coverage**, not codebase health: the branch is
clean, honest, and well-architected, but it covers a *different* (and stronger) vertical than
legacy's daily pipeline.

**Exact P0 gaps blocking Level 2:** intake/capture; source-lifecycle file movement; a blessed
daily write path; durable run ledger/audit (in-memory `EventLog` today); persistent read index.
Two of these are **needs-decision** (blessed path: promote M7–M13 vs reader/crystal-as-output;
read index: fs-projection vs SQLite `ovp-index` crate).

**Recommended next large stage — M30: Rust Daily Workflow MVP (Capture → Vault Lifecycle → Read).**
Not "polish edges." The smallest end-to-end loop that makes Rust usable daily on the real vault
**without reviving Referent / graph / RAG**:
1. L0/L1 intake source (inbox/clippings/pinboard) + filename normalize + URL dedup → `ovp-domain`
   source filters + `ovp-stores` move/lock primitives.
2. `VaultLayout` port wired through sources/sinks (staging set L0→L4 lifecycle).
3. Durable transaction ledger + audit on disk (`ovp-core` model + `ovp-stores` `TxnFsApplier`),
   retiring in-memory-only `EventLog`.
4. Make `read-source` + reader pack (and optionally Crystal) the **Reuse/output** surface of that
   loop — the validated (B) vertical becomes the daily payoff, not a side experiment.
5. Resolve the read-index decision (start with the existing fs/JSON projection; defer SQLite unless
   query/lint pain proves it).

Parallel cheap hygiene (this stage or M30 prep, doc/refactor only — no new features):
- Quarantine/feature-gate the demoted M7–M13 substrate; delete or gate `referents/`.
- Refresh `legacy-alignment.md` (mark superseded) and extend `architecture.md` to M27/M28.
- Group diagnostic/eval CLI verbs distinctly from product verbs.

**Explicit non-goals reaffirmed:** no prompt tuning; no Referent/graph/RAG revival; no KMEM UI /
credentials; no parity-for-parity; no daemon/DB/graph/new-UI built *in this audit stage*.
