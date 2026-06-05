//! M13.2 — synthetic end-to-end proof for the additive v2 concept-map path.
//!
//! **What this proves:** GIVEN a correct v2 model response, the *pipeline*
//! (ArticleParser → ConceptResolver gate → EvergreenConceptWriter → EvergreenSink
//! render) carries a correct concept map to disk — distinct per-concept
//! definitions, owned claims, the forbidden umbrella/synonym/metadata slugs
//! gated out, and none of the case-level forbidden phrases leaking into a note.
//!
//! **What this does NOT prove:** that the real model emits such a response. The
//! input here is hand-authored from the committed benchmark fixture's
//! `expected_meaning` / `acceptable_claims` (a *simulated ideal model*, not
//! production logic). A pass is **synthetic-green**, not real-green. Closing the
//! real-model loop (v2 prompt-builder wiring, live cassette re-record, default
//! flip, real benchmark green) is M13.3.
//!
//! The rendered notes are written under `OVP_M13_OUT` (if set) or a temp dir,
//! so `scripts/concept_map_bench.py --ovp-root <root> --case rag_wrong` can
//! score the exact bytes this pipeline produced. The `.run/` output is never
//! committed.

use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

use ovp_core::{FilterDecision, Record, RecordId, RecordMeta, RunId, Sink, Transform, WriteOp};
use ovp_domain::{
    ArticleParser, ConceptResolver, DomainBody, EvergreenConceptWriter, EvergreenSink,
    ModelResponse, PromptId, ResponseContent, SourceDoc,
};

/// The ideal v2 response for the `rag_wrong` case: seven source-grounded
/// concepts (definitions from the fixture's `expected_meaning`, claims from its
/// `acceptable_claims` / `required_evidence`), plus three deliberate noise
/// concepts the gate must remove — `knowledge-unit` (merges into idea-block),
/// `data-pipeline` (rejected synonym of blockify), and `rag` (background field).
const IDEAL_V2_JSON: &str = r#"{
  "title": "The chunk is the wrong unit: rebuilding RAG around IdeaBlocks",
  "tags": ["RAG", "retrieval", "knowledge-engineering"],
  "dimensions": {
    "one_liner": "The chunk is the wrong unit of knowledge; replacing it with a structured IdeaBlock and distilling redundancy makes retrieval sharper with less data.",
    "explanation": { "what": "Why prose chunks fail and what replaces them.", "why": "Most retrieval failures are upstream, in the unit.", "how": "Blockify converts documents into governed IdeaBlocks and distills duplicates." },
    "details": [
      "Chunks have no idea boundary, version context, or access state.",
      "IdeaBlocks embed one question, a validated answer, and typed governance fields.",
      "Distillation shrank a corpus while improving vector accuracy by 13.55%."
    ],
    "structure": null,
    "actions": ["Add a distillation layer between parsing and vectorization."]
  },
  "concepts": [
    {
      "slug": "idea-block", "title": "IdeaBlock", "aliases": ["qa-packet"], "kind": "concept",
      "definition": "A structured unit of knowledge that embeds one critical question, its validated 2-3 sentence answer, and typed governance fields as a single object, replacing a prose chunk as the thing you embed.",
      "evidence": ["a structure it calls an IdeaBlock: a question, its validated answer, and typed governance fields like clearance level, version state, and source"],
      "claims": [
        "Each IdeaBlock holds one critical question, one validated answer of two to three sentences, and typed governance fields (line 141).",
        "Embedding a question-answer packet makes the query-to-index match structural rather than just semantic (lines 85-87)."
      ],
      "related": ["chunking-problem"], "merge_with": [], "promote": true
    },
    {
      "slug": "chunking-problem", "title": "The Chunk Is a Bad Unit", "aliases": ["chunk-as-unit"], "kind": "claim",
      "definition": "A prose chunk is a structurally neutral container with no idea boundary, no version context, and no access state, so it is the wrong unit of knowledge and the upstream source of most downstream retrieval failures.",
      "evidence": ["A chunk of text is a structurally neutral container. It knows nothing about: where its ideas begin or end"],
      "claims": [
        "You end up retrieving half a table or a claim stripped of its context (line 57).",
        "Because the chunk carries no metadata, there is nowhere to attach access control in the data itself (line 65)."
      ],
      "related": ["idea-block"], "merge_with": [], "promote": true
    },
    {
      "slug": "blockify", "title": "Blockify", "aliases": ["iternal-blockify"], "kind": "system",
      "definition": "A preprocessing layer and product from Iternal Technologies that sits between the document parser and the vector store and converts documents into IdeaBlocks through a defined seven-stage pipeline.",
      "evidence": ["Blockify, a preprocessing layer from Iternal Technologies, implements this as a structure it calls an IdeaBlock"],
      "claims": [
        "The seven stages are Scoping, Ingestion, Chunking/extraction, Semantic deduplication, Auto-tagging, Human validation, and Export (lines 131-169).",
        "Ingestion uses fine-tuned LLaMA 3 / QWEN 3.5 / Gemma4 to convert raw chunks into draft IdeaBlocks (line 141)."
      ],
      "related": ["idea-block"], "merge_with": [], "promote": true
    },
    {
      "slug": "semantic-deduplication", "title": "Semantic Deduplication / Distillation", "aliases": ["distillation"], "kind": "procedure",
      "definition": "Iterative clustering of blocks by cosine similarity at an 80-85% threshold across 3-5 rounds, merging near-duplicates into a single canonical block, which shrinks the corpus while improving retrieval accuracy.",
      "evidence": ["Blocks are clustered by cosine similarity at an 80-85% threshold across three to five iterative rounds"],
      "claims": [
        "Distilling the dataset improved vector accuracy by 13.55% over the undistilled version (line 115).",
        "Deduplication merges near-duplicates via a second specially tuned LLM and can run on GPU or Intel Xeon CPU (line 155)."
      ],
      "related": ["vector-redundancy"], "merge_with": [], "promote": true
    },
    {
      "slug": "governance-metadata", "title": "Typed Governance Fields", "aliases": ["governance-fields"], "kind": "concept",
      "definition": "Typed metadata such as clearance level, version state, product line, and access boundary carried as schema fields on each IdeaBlock, moving governance into the data layer instead of as logic bolted onto the orchestrator.",
      "evidence": ["clearance level (PUBLIC, INTERNAL, CONFIDENTIAL, SECRET), version state (Current, Deprecated, Draft, Approved)... Applied by the pipeline, not the document author"],
      "claims": [
        "A sales engineer and a legal reviewer querying the same index get different datasets because the blocks carry the access boundary (line 180).",
        "Governance metadata is applied by the pipeline, not the document author (line 161)."
      ],
      "related": ["idea-block"], "merge_with": [], "promote": true
    },
    {
      "slug": "vector-redundancy", "title": "Redundancy Degrades the Retrieval Surface", "aliases": ["retrieval-surface"], "kind": "principle",
      "definition": "Near-duplicate copies of the same content create many competing vectors in one region of embedding space, spreading probability mass and lowering the canonical match score, so the vector index is a retrieval surface that redundancy degrades, not a hard drive to fill.",
      "evidence": ["fifteen near-duplicates of the same paragraph create fifteen competing vectors in the same region of embedding space"],
      "claims": [
        "Fifteen near-duplicates of one paragraph create fifteen competing vectors, so retrieval spreads probability mass across them and pulls the match score down for the canonical version (lines 117-119).",
        "Collapsing the duplicates into one canonical block sharpens the signal; the vector index is a retrieval surface and redundancy degrades it (lines 119-121)."
      ],
      "related": ["semantic-deduplication"], "merge_with": [], "promote": true
    },
    {
      "slug": "distillation-layer", "title": "Distillation Layer (CDN Analogy)", "aliases": ["cdn-analogy"], "kind": "principle",
      "definition": "The architectural pattern of a corpus-distillation stage that RAG stacks are growing between document parsing and vectorization, analogous to how web stacks grew a CDN layer between origin and browser.",
      "evidence": ["RAG stacks are beginning to grow a distillation layer between parsing and vectorization, the way web stacks grew a CDN layer between origin and browser"],
      "claims": [
        "The fix belongs at the data layer, not in a better retrieval algorithm (lines 193-199).",
        "You can build the distillation layer yourself with clustering, LLM-based summarization, and schema enforcement, or use something purpose-built like Blockify (line 197)."
      ],
      "related": ["blockify"], "merge_with": [], "promote": true
    },
    {
      "slug": "knowledge-unit", "title": "Knowledge Unit", "aliases": [], "kind": "concept",
      "definition": "An abstract name for the unit of knowledge the article actually realizes as the IdeaBlock.",
      "evidence": ["unit of knowledge"],
      "claims": ["The article uses 'unit of knowledge' descriptively (line 39)."],
      "related": [], "merge_with": ["idea-block"], "promote": true
    },
    {
      "slug": "data-pipeline", "title": "Data Pipeline", "aliases": [], "kind": "system",
      "definition": "The generic name for the same seven-stage Blockify pipeline.",
      "evidence": ["seven stages"],
      "claims": ["This is the same pipeline Blockify owns."],
      "related": [], "merge_with": [], "reject_reason": "synonym of blockify; merge into the blockify system note", "promote": false
    },
    {
      "slug": "rag", "title": "RAG", "aliases": [], "kind": "concept",
      "definition": "Retrieval-augmented generation, the assumed background field of the article.",
      "evidence": ["RAG"],
      "claims": ["RAG is the background, not a concept this article defines."],
      "related": [], "merge_with": [], "reject_reason": "article/background metadata, not a defined concept", "promote": false
    }
  ]
}"#;

/// The seven concepts the gate must keep + mint, in mint order.
const EXPECTED_MINTED: &[&str] = &[
    "idea-block",
    "chunking-problem",
    "blockify",
    "semantic-deduplication",
    "governance-metadata",
    "vector-redundancy",
    "distillation-layer",
];

/// Slugs the fixture marks `must_not_mint` — the gate must drop these.
const MUST_NOT_MINT: &[&str] = &["knowledge-unit", "data-pipeline", "rag"];

/// A subset of the fixture's `forbidden_phrases_anywhere` (body-unsupported
/// marketing numbers + author metadata) — none may appear in any minted note.
const FORBIDDEN_PHRASES: &[&str] = &["40x", "reducing corpus 40x", "Akshay", "dailydoseofds"];

fn model_record_v2(json: &str) -> Record<DomainBody> {
    let resp = ModelResponse {
        prompt_id: PromptId::new("article_concept_map/v2"),
        schema_version: 2,
        model: "synthetic-ideal".into(),
        content: ResponseContent::Inline { text: json.into() },
        input_tokens: 0,
        output_tokens: 0,
        origin: Box::new(SourceDoc::article(
            "The chunk is the wrong unit",
            "https://example.com/rag-rebuilt",
            Some("Synthetic Author".into()),
            None,
            vec![],
            "",
        )),
    };
    Record::new(
        RecordId::new("rag_wrong"),
        DomainBody::Model(Box::new(resp)),
        RecordMeta { run_id: RunId::new("m13-synthetic"), seq: 0 },
    )
}

/// Where to emit rendered notes for the offline python bench. `OVP_M13_OUT`
/// overrides; otherwise a temp dir (the bench is only run when the operator
/// points it at the emitted dir, so the temp fallback keeps `cargo test`
/// hermetic).
fn out_dir() -> PathBuf {
    match std::env::var_os("OVP_M13_OUT") {
        Some(p) => PathBuf::from(p),
        None => std::env::temp_dir().join("ovp-m13-synthetic/rag_wrong/ovp/evergreen"),
    }
}

#[test]
fn ideal_v2_response_carries_a_clean_concept_map_to_disk() {
    // 1. Parse the ideal v2 response.
    let mut parser = ArticleParser::new("article_parser", "ai", "2026-05-31");
    let parsed = match parser.process(model_record_v2(IDEAL_V2_JSON)) {
        FilterDecision::Forward(rs) => rs.into_iter().next().unwrap(),
        other => panic!("parser: expected Forward, got {other:?}"),
    };
    if let DomainBody::Interpreted(d) = &parsed.body {
        assert_eq!(d.concepts.len(), 10, "all 10 concepts reach the resolver (gate decides)");
    } else {
        panic!("parser did not produce an Interpreted doc");
    }

    // 2. Gate the concept map (empty registry — v2 gating is independent of
    //    v1 canonical promotion).
    let mut resolver = ConceptResolver::from_slugs("concept_resolver", &[]);
    let (gated, drop_events) = match resolver.process(parsed) {
        FilterDecision::ForwardWithEvents { records, events } => {
            (records.into_iter().next().unwrap(), events)
        }
        FilterDecision::Forward(rs) => (rs.into_iter().next().unwrap(), vec![]),
        other => panic!("resolver: expected Forward(WithEvents), got {other:?}"),
    };
    // The three noise concepts dropped observably.
    assert_eq!(drop_events.len(), 3, "knowledge-unit + data-pipeline + rag gated out");
    if let DomainBody::Interpreted(d) = &gated.body {
        let kept: Vec<&str> = d.concepts.iter().map(|c| c.slug.as_str()).collect();
        assert_eq!(kept, EXPECTED_MINTED, "gate keeps exactly the seven, in order");
    } else {
        panic!("resolver did not produce an Interpreted doc");
    }

    // 3. Mint one evergreen per gated concept.
    let mut writer = EvergreenConceptWriter::new("evergreen_writer");
    let minted_records = match writer.process(gated) {
        FilterDecision::FanOut(rs) => rs,
        FilterDecision::Forward(rs) => rs,
        other => panic!("writer: expected FanOut, got {other:?}"),
    };

    // 4. Render each via the real sink (byte-identical to production write).
    let mut sink = EvergreenSink::new("evergreen_sink", RunId::new("m13-synthetic"));
    let mut notes: HashMap<String, String> = HashMap::new();
    for rec in minted_records {
        if !matches!(rec.body, DomainBody::EvergreenConcept(_)) {
            continue; // the primary article note is not ours
        }
        for op in sink.consume(rec).plan_ops {
            if let WriteOp::VaultCreate(c) = op {
                let slug = c
                    .path
                    .as_str()
                    .rsplit('/')
                    .next()
                    .unwrap()
                    .trim_end_matches(".md")
                    .to_string();
                notes.insert(slug, c.body);
            }
        }
    }

    // --- benchmark-carry assertions (the python bench does the full 9 checks) ---

    // Exactly the seven expected slugs minted; no forbidden slug.
    let mut minted: Vec<&String> = notes.keys().collect();
    minted.sort();
    let mut want: Vec<String> = EXPECTED_MINTED.iter().map(|s| s.to_string()).collect();
    want.sort();
    assert_eq!(
        minted.into_iter().cloned().collect::<Vec<_>>(),
        want,
        "minted set is exactly the seven gated concepts"
    );
    for forbidden in MUST_NOT_MINT {
        assert!(!notes.contains_key(*forbidden), "forbidden slug minted: {forbidden}");
    }

    // Distinct per-concept definitions (no shared one_liner).
    let defs: Vec<String> = EXPECTED_MINTED
        .iter()
        .map(|s| definition_of(&notes[*s]))
        .collect();
    for (i, a) in defs.iter().enumerate() {
        for (j, b) in defs.iter().enumerate() {
            if i != j {
                assert_ne!(a, b, "two notes share a definition ({}, {})", EXPECTED_MINTED[i], EXPECTED_MINTED[j]);
            }
        }
    }

    // Each note owns >=1 claim not shared verbatim with another note.
    let mut claim_count: HashMap<String, usize> = HashMap::new();
    let claims_by_note: HashMap<&str, Vec<String>> =
        EXPECTED_MINTED.iter().map(|s| (*s, claims_of(&notes[*s]))).collect();
    for cls in claims_by_note.values() {
        for c in cls {
            *claim_count.entry(c.clone()).or_default() += 1;
        }
    }
    for slug in EXPECTED_MINTED {
        let owned = claims_by_note[slug].iter().any(|c| claim_count[c] == 1);
        assert!(owned, "note `{slug}` has no claim it uniquely owns");
    }

    // No case-level forbidden phrase leaks into any note.
    for (slug, body) in &notes {
        for phrase in FORBIDDEN_PHRASES {
            assert!(
                !body.to_lowercase().contains(&phrase.to_lowercase()),
                "forbidden phrase {phrase:?} found in note `{slug}`"
            );
        }
    }

    // 5. Emit for the offline python bench (not committed).
    let dir = out_dir();
    fs::create_dir_all(&dir).expect("create out dir");
    for (slug, body) in &notes {
        fs::write(dir.join(format!("{slug}.md")), body).expect("write note");
    }
    eprintln!("wrote {} synthetic notes to {}", notes.len(), dir.display());
}

/// The definition is the first `> ...` blockquote line (mirrors the bench's
/// `parse_note`).
fn definition_of(md: &str) -> String {
    md.lines()
        .find_map(|l| l.strip_prefix("> "))
        .unwrap_or("")
        .trim()
        .to_string()
}

/// Claims are the bullets under `## Source-backed claims` (mirrors the bench).
fn claims_of(md: &str) -> Vec<String> {
    let mut in_sec = false;
    let mut out = Vec::new();
    for line in md.lines() {
        if let Some(h) = line.strip_prefix("## ") {
            in_sec = h.trim() == "Source-backed claims";
            continue;
        }
        if in_sec {
            if let Some(item) = line.strip_prefix("- ") {
                out.push(item.trim().to_string());
            }
        }
    }
    out
}
