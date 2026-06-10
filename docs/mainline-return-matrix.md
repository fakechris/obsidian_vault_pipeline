# Mainline Return Matrix — Legacy Python OVP → Rust trunk

> Companion to [`docs/stage-m29-mainline-return-audit.md`](./stage-m29-mainline-return-audit.md).
> Compares **product capability**, not CLI names. "Covered" means a real human gets the same
> product value, by whatever command/storage the Rust trunk chooses.
> Snapshot: branch `codex/rust-migration` @ M31 (HEAD `754011e`), 2026-06-10.
> M30/M31 deltas marked **(M31)**.
>
> Supersedes the reader-trunk-era view of [`docs/legacy-alignment.md`](./legacy-alignment.md),
> which was written from the pre-pivot M7–M13 canonical-store roadmap and predates M14–M28.

## Status legend

- **covered** — product value present in Rust trunk (different commands/storage OK)
- **partial** — exists but thin / demoted / not the blessed path
- **redesigned** — Rust deliberately solves the same need a different way
- **missing-P0** — blocks a usable daily Rust workflow on the real vault
- **missing-P1** — real users notice within a week
- **dropped** — intentional non-goal
- **deferred** — wanted after mainline switch
- **needs-decision** — product call required before it can be classified

## Matrix

### 1. Daily capture / ingest
| Legacy capability | Rust today | Status |
|---|---|---|
| Pinboard fetch + landing (`pinboard-processor`, L1) | **(M31)** `pinboard-sync` + `daily --pinboard-*`: trait-gated adapter, fixture/export always, live behind `pinboard-live` feature, URL dedup, ledger | covered |
| Clippings / Reader web-clipper intake (L0) | **(M31)** `intake` sweep: Clippings/00-Capture/02-Pinboard → 01-Raw, normalize, flag thin/broken | covered |
| Raw Markdown inbox intake | **(M31)** raw scan + hash dedup in `daily` plan; manual drops indexed as queued | covered |
| GitHub repo intake (`auto_github_processor`) | none | missing-P1 |
| Web-page / article intake (`auto_article_processor` intake path) | `interpret-article` reads one file; no inbox watch/normalize/lifecycle | partial |
| Global URL dedup across active staging set (`source_dedup`) | **(M31)** URL + content-sha256 dedup at intake; duplicates parked, ledger-recorded | covered |

### 2. Source lifecycle & vault file movement
| Legacy capability | Rust today | Status |
|---|---|---|
| 5-stage staging set L0–L4 (`Clippings`→`01-Raw`→`02-Processing`→`03-Processed/YYYY-MM`) | **(M31)** capture→raw→processed/duplicates moves (collision-suffixed, never delete/overwrite, write-logged) | covered (no 02-Processing stage — direct raw→processed) |
| `VaultLayout` (~25 typed vault paths) | `vault_layout.rs` exists (value type) | partial |
| Image/attachment download + rewrite (`image_downloader`) | none | missing-P1 |
| File move/lock/archive primitives | **(M31)** `safe_move`/`write_new` (never overwrite) + `RunLock` single-instance guard | covered |
| Frontmatter repair / normalize (`repair_*`, `note_type_normalize`) | sinks own schema (write-correct-first) | redesigned / deferred |

### 3. Article / paper / clipping handling
| Legacy capability | Rust today | Status |
|---|---|---|
| Article interpret → vault note (深度解读) | `interpret-article` + `VaultFsPlanApplier` (offline, cassette) | covered |
| Paper deep-dive (arXiv, `auto_paper_processor`) | `PaperDoc` types + paper fixture; interpret path partial | partial |
| 13-section LLM article deep-dive | — | dropped (BL-029/BL-066; per legacy itself) |
| Grounded reader cards / pack (NEW) | `read-source`: Source→Units→Critic→Cards→Pack (M14a–M20, 20/20) | redesigned (net-new, ahead of legacy) |

### 4. Evergreen / canonical / absorb / crystal
| Legacy capability | Rust today | Status |
|---|---|---|
| Absorb L2→L3 lifecycle (`run_absorb_workflow`, `absorb_router`) | none on blessed path | missing-P0 |
| Evergreen minting (new note) | `EvergreenConceptWriter` (M12a, demoted) builds+tests | partial (demoted) |
| Same-slug enrich/reconcile | M12b reconcile (demoted) | partial (demoted) |
| ConceptRegistry identity authority | `concept_registry.rs` (181 lines, demoted) + `ConceptResolver` trait | partial (demoted) |
| Canonical store | `CanonicalFsStoreApplier` + `canonical.rs` (demoted but wired) | partial (demoted) |
| Promotion policy lanes (AUTO/ESCALATE/REJECT) | none | missing-P1 |
| Crystal materialization (legacy community/contradiction) | — | redesigned (see below) |
| **Durable Crystal truth layer (NEW)** | `crystal-lint` + `crystal-write` gate + append-only `ledger.jsonl` (M22–M28) | redesigned (net-new) |
| Semantic dedup of near-duplicate claims | none | deferred |

### 5. Query / ask / digest / working memory
| Legacy capability | Rust today | Status |
|---|---|---|
| Read query (list/get/search/backlinks/stats, `query_tool`) | `query` over fs read model (lexical) | partial |
| BM25 + embedding + RRF retrieval | `ovp-rag` lexical-only; **no embeddings** | partial / redesigned |
| Anchored inquiry `ovp-ask` + chats projection (M21) | none | missing-P1 |
| `/digest` daily synthesis (M20) | none | missing-P1 |
| `ovp-working-memory` daily budgeted context pack | none | missing-P1 |
| `ovp-prime` session context pack | none | deferred |

### 6. UI / dashboard / review surfaces
| Legacy capability | Rust today | Status |
|---|---|---|
| Local web UI server (Reader shell + Maintainer `/ops` shell, `ui_server`) | none (no server) | missing-P1 / needs-decision |
| FTS search surface (`page_fts`) | lexical `query search`, no FTS index | partial |
| Atlas / Topics / Map / Graph reader surfaces | none (graph viz intentionally out) | dropped/deferred |
| Ops dashboards (candidates, contradictions, signals, actions, pulse, audit) | none | needs-decision |
| Article-level review console (NEW) | **(M31)** Rust `ovp-console` over PRODUCT state (`.ovp/console`): attention/runs/sources/packs/crystal, bilingual; M28 `.run` console superseded as product surface | redesigned (net-new) |

### 7. Automation / autopilot / daemon / scheduled
| Legacy capability | Rust today | Status |
|---|---|---|
| `ovp-autopilot` daemon (watch=inbox, absorb→moc→knowledge_index) | none | missing-P1 |
| `ovp --full` / `--incremental` daily pipeline | `run-cycle` (one file, demoted cycle) + `auto-run` sweep (offline) | partial |
| Task dispatcher (QUEUE→GENERATED, M20) | none | deferred |
| Scheduled live-concept scan | none | dropped (Referent-adjacent) |

### 8. Quality / lint / truth / provenance
| Legacy capability | Rust today | Status |
|---|---|---|
| WIGS 5-layer lint (`lint_checker`) | `ovp-lint` (missing notes/stale index/broken links/orphans) | covered (read model scope) |
| Truth/provenance grounding | `read-source` `accepted_without_quote=0`; crystal citation→unit→quote→line gate | covered (net-new, stronger) |
| Evidence verify / replay (`evidence_verify`, `evidence_replay`) | crystal-lint re-runs validator | partial / redesigned |
| Contradiction detection / resolution | none | deferred |
| `ovp-doctor` cross-registry health | none | missing-P1 |

### 9. State stores / knowledge.db / indexes / audit
| Legacy capability | Rust today | Status |
|---|---|---|
| `knowledge.db` SQLite (pages_index, FTS5, page_links, embeddings, audit_events, truth_projections, ops_state) | **(M31) decided**: JSON projection `ovp-index` (rebuildable, queryable via `find`); SQLite consciously deferred until query pain proves it; no embeddings/FTS | redesigned (decision made) |
| Schema migration discipline (additive/recompute/breaking, v9) | n/a | deferred |
| Canonical store persistence | `CanonicalFsStoreApplier` (fs records, demoted) | partial |
| Durable run ledger / transactions (`txn.py`, `60-Logs/transactions`) | **(M30/M31)** append-only `.ovp/*.jsonl` ledgers + per-run reports + `pipeline.jsonl` write log with ordering proofs (demoted manifest path still uses in-memory `EventLog`) | covered (blessed path) |
| Audit-event log | **(M31)** every product write logged to `60-Logs/pipeline.jsonl` (`event_type`, legacy-compatible) BEFORE its success record | covered (blessed path) |
| `ops_state` projection | none | missing-P1 |

### 10. Integration surfaces
| Legacy capability | Rust today | Status |
|---|---|---|
| MCP server (`mcp.py`) | none | needs-decision |
| GitHub enrichment | none | missing-P1 |
| Pinboard | **(M31)** covered (see §1) | covered |
| Browser/Reader clipper | **(M31)** clipper lands in `Clippings/`; intake sweeps it | covered |
| KnowledgeMem | `ovp-eval` comparator only (above trunk, gate-fenced) — reference-only | redesigned (eval, not product) |
| Export artifact (`ovp-export`) | none | deferred |

## Roll-up

| Status | Count (capability rows) |
|---|---|
| covered | 14 |
| partial | 11 |
| redesigned (net-new or different) | 10 |
| missing-P0 | 0 |
| missing-P1 | 11 |
| dropped | 3 |
| deferred | 8 |
| needs-decision | 3 |

**(M31) The M29 P0 set is closed**: intake (clippings + pinboard + URL/content dedup), lifecycle file movement, the blessed daily write path (reader/crystal as output surface), the durable run ledger/audit, and the read-index decision (JSON projection) are all shipped on the blessed path. The legacy "absorb→evergreen" P0 is resolved by redesign: the reader/crystal trunk is the blessed enrichment path; eager evergreen/canonical stays demoted. Remaining gaps are P1 (Reuse surfaces, doctor/ops projections, web UI/MCP decisions, daemon) plus real-vault dogfood time — see `stage-m31-mainline-capability-closure.md`.
