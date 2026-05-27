# OVP Next — Fixtures (B contract)

This directory is the **frozen behavioral contract** between the legacy Python pipeline and OVP Next. Each fixture is a paired snapshot from the live vault at `~/Documents/ovp-vault`, captured read-only.

No legacy code was executed to produce these. We did not run `ovp --full` or import `ovp_pipeline.*`. The files here are the actual artifacts the legacy system wrote on the dates shown in their frontmatter.

> Why this matters: the new system will be measured against these fixtures, not against the legacy code itself. The contract is data-shaped, not code-shaped. The legacy Python remains a frozen oracle — we read its output, not its implementation.

## How to read a fixture

Every fixture is a directory containing:

```
<fixture_name>/
├── input.md              # Raw input as the legacy pipeline received it.
├── expected/
│   ├── contract.yaml     # Machine-readable MUST / SHOULD / MAY-break assertions.
│   ├── interpretation.md # Full interp file the legacy pipeline emitted (when one exists).
│   ├── frontmatter.yaml  # Just the interp's frontmatter, isolated for human inspection.
│   └── ...               # Other expected artifacts when applicable.
└── notes.md              # Prose explanation of the contract, why this fixture exists.
```

The `expected/` directory is the **target**. The new system's pipeline should be able to take `input.md` and produce something that satisfies the `contract.yaml` MUST clauses for that fixture, ideally meeting the SHOULD clauses too. MAY-break clauses are explicit permission to diverge.

`notes.md` is for humans; `contract.yaml` is for the test harness. They should never disagree — if they do, `contract.yaml` is canonical.

### contract.yaml shape

A small schema (the four current contract files are the authoritative examples):

```yaml
version: 1
terminal_state: interpretation_produced | terminal_raw
expected_artifacts:
  - kind: interpretation
    path_pattern: "..."
forbidden_artifacts:
  - kind: ...
    path_glob: "..."
must:
  - field: <name>
    op: equals | contains | matches_regex | matches_one_of | type | non_empty | length_gte | length_in_range | gte | not_equals
    value: ...
  - body_section: { op: contains_one_of, values: [...] }
  - body_sections_present: { op: at_least, sections: [...] }
  - event_emitted: { kind: ... }
  - source_kind: article | paper | github_repo
  - writeplan_constraint: { forbidden_path_prefix: ... }
  - utf8_clean: { paths: [...] }
should: [ ... ]
may_break: [ ... ]
known_anomalies: [ ... ]
```

The ops above are deliberately limited — fixture tests should be cheap to write and obvious to read. If you need a new op, add it to one fixture, justify it in `notes.md`, and propagate.

## Contract levels

Per the original design doc §10, three levels:

- **MUST preserve** — load-bearing. If the new system drops or mutates this, downstream consumers (lookup, MOC routing, identity resolution) break. CI gates the test suite against these.
- **SHOULD preserve** — valuable. The new system may change these with documented rationale in the commit message.
- **MAY break** — explicit permission to do something different. Often a place where the legacy system has a known limitation or quirk.

## Fixtures in this pack

| Fixture | Source kind | Why it's here |
|---|---|---|
| `article_clean` | English article | Happy-path baseline. If this doesn't work, nothing else will. |
| `article_mixed_lang` | English source → Chinese interp | Title reframing, source URL rewrite, UTF-8 throughout, two-tier evergreen extraction. |
| `paper_arxiv` | arXiv paper | Different document kind — 9-section structure, sparse interp frontmatter, papers currently skip absorb. |
| `github_enriched_raw` | GitHub repo (deepwiki-enriched) | "Raw without interpretation" — a real terminal state in the legacy system. Tests whether the new pipeline can route a record to a `StopAtRaw`-style outcome with an explanatory event. |

A 5th fixture for image-bearing content is **not yet captured**. Add when image handling becomes a question for the new system.

## What's NOT in the contract

- The legacy MOC files. Derived state; the new system has its own derived-index model.
- The full evergreen page set. Evergreens are referenced by slug here (`canonical_concepts: [ai-agent, ...]`) but their content lives in a separate contract (post-C).
- The Pinboard sync layer. Not in scope until source kinds beyond local-file ingestion land.
- Exact pipeline run IDs, exact event sequences. Those are legacy provenance artifacts; the new system has its own.

## How the new system uses these

1. **`MarkdownInboxSource`** reads `input.md` as a `SourceDoc` record.
2. **`RouteBySourceKind`** dispatches by `source_type` (or absence — articles have no `source_type`).
3. **`ArticleInterpreter` / `PaperInterpreter` / `GithubInterpreter`** produces `InterpretedDoc` records.
4. **`VaultWritePlanSink`** emits `VaultCreate` ops that, when applied, should produce files satisfying the fixture's contract.
5. The integration test compares produced output to `expected/*` per the rules in `notes.md`.

When the test fails, the failure points at exactly one clause from `notes.md`. That's the design.

## Survey provenance

The vault survey that picked these fixtures lives in `SURVEY.md` next to this file. It documents what other shapes exist in the vault, what was deliberately left out, and the open questions that remain.
