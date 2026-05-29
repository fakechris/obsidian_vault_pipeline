//! Acceptance tests for the Graph Assembly Layer: the three shipped manifests
//! assemble from `(DomainPipelineSpec + AppWiring)` and reproduce the same
//! behavior the hand-wired domain/stores tests assert — plus the assembler's
//! failure modes (unknown kind, missing wiring, category mismatch).
//!
//! Offline: the only `ModelClient` is a replay-only cassette client; no network.

use std::path::{Path, PathBuf};

use ovp_app::{AppWiring, AssemblyError, DomainPipelineSpec, GraphAssembler};
use ovp_core::{
    ApplyMode, EventKind, GraphRunner, PipelineManifest, PlanApplier, RunId, RunReport, WriteOp,
};
use ovp_domain::{
    ArticleParser, ArticleVaultPlanSink, CanonicalConcept, ConceptRegistry, ConceptResolver,
    DomainBody, LLMInvoker, MarkdownInboxSource, PromptBuilder, SourceResolver, ARTICLE_PROMPT_ID,
};
use ovp_llm::{CacheMode, CachedModelClient, ModelClient, NeverCallsClient};
use ovp_stores::{CanonicalFsStoreApplier, CompositePlanApplier, VaultFsPlanApplier};

fn repo_root() -> PathBuf {
    // CARGO_MANIFEST_DIR = <root>/crates/ovp-app
    let md = std::env::var("CARGO_MANIFEST_DIR").unwrap();
    Path::new(&md).ancestors().nth(2).unwrap().to_path_buf()
}

/// Replay-only cassette client. The per-request `cache_namespace` set by each
/// prompt builder selects the right cassette dir, so one client serves both
/// the article and paper prompts regardless of this constructor namespace.
fn cassette_client(root: &Path) -> Box<dyn ModelClient> {
    Box::new(
        CachedModelClient::new(
            NeverCallsClient,
            root.join("crates/ovp-domain/tests/cassettes"),
            ARTICLE_PROMPT_ID,
            CacheMode::ReplayOnly,
        )
        .unwrap(),
    )
}

fn read_spec(root: &Path, rel: &str) -> DomainPipelineSpec {
    let toml = std::fs::read_to_string(root.join(rel)).unwrap();
    DomainPipelineSpec::parse(&toml).unwrap()
}

/// The article pipeline built BY HAND (the pre-assembly wiring), for the
/// equals-manual equivalence proof.
fn manual_article_report(
    root: &Path,
    run_id: RunId,
    input: PathBuf,
    date: &str,
    slugs: &[&str],
) -> RunReport {
    let toml = std::fs::read_to_string(root.join("manifests/article.pipeline.toml")).unwrap();
    let manifest = PipelineManifest::parse(&toml).unwrap();
    let mut runner: GraphRunner<DomainBody> = GraphRunner::new(manifest, run_id.clone());
    runner.register_source(
        "markdown_inbox",
        MarkdownInboxSource::new("markdown_inbox", run_id.clone(), input),
    );
    runner.register_transform("source_resolver", SourceResolver::new("source_resolver"));
    runner.register_transform("prompt_builder", PromptBuilder::new("prompt_builder"));
    runner.register_effectful_transform(
        "llm_invoker",
        LLMInvoker::new("llm_invoker", cassette_client(root)),
    );
    runner.register_transform("article_parser", ArticleParser::new("article_parser", "ai", date));
    runner.register_transform(
        "concept_resolver",
        ConceptResolver::new("concept_resolver", ConceptRegistry::from_slugs(slugs)),
    );
    runner.register_sink(
        "article_vault_plan",
        ArticleVaultPlanSink::new("article_vault_plan", run_id),
    );
    runner.run().unwrap()
}

/// The same article pipeline built BY THE ASSEMBLER from the manifest + wiring.
fn assembled_article_report(
    root: &Path,
    run_id: RunId,
    input: PathBuf,
    date: &str,
    slugs: &[&str],
) -> RunReport {
    let spec = read_spec(root, "manifests/article.pipeline.toml");
    let wiring = AppWiring::new(run_id)
        .with_date_stamp(date)
        .with_area("ai")
        .with_input_path(input)
        .with_client("default_llm", cassette_client(root))
        .with_registry("default", ConceptRegistry::from_slugs(slugs));
    GraphAssembler::with_domain_nodes().assemble(&spec, wiring).unwrap().run().unwrap()
}

#[test]
fn assembled_article_writeplan_equals_manual_empty_registry() {
    // The literal proof of "assembled == manual behavior": with the same run
    // id, input, date, and (empty) registry, the assembled and hand-wired
    // pipelines produce byte-identical WritePlans.
    let root = repo_root();
    let input = root.join("fixtures/article_clean/input.md");
    let manual = manual_article_report(&root, RunId::new("eq"), input.clone(), "2026-05-04", &[]);
    let assembled = assembled_article_report(&root, RunId::new("eq"), input, "2026-05-04", &[]);
    assert_eq!(
        assembled.write_plan, manual.write_plan,
        "assembled WritePlan must equal the hand-wired one"
    );
}

#[test]
fn assembled_article_writeplan_equals_manual_with_registry() {
    // Same equivalence, but with a NON-EMPTY ConceptRegistry — proving the
    // manifest's `config = { registry = "default" }` binding actually drives
    // canonical-concept promotion (the article_mixed_lang scenario).
    let root = repo_root();
    let input = root.join("fixtures/article_mixed_lang/input.md");
    let slugs = &["ai-agent", "competitive-advantage"];
    let manual = manual_article_report(&root, RunId::new("eq2"), input.clone(), "2026-05-05", slugs);
    let assembled =
        assembled_article_report(&root, RunId::new("eq2"), input.clone(), "2026-05-05", slugs);
    assert_eq!(
        assembled.write_plan, manual.write_plan,
        "registry binding must drive identical canonical promotion"
    );
    // The binding is load-bearing: with no registry, promotion differs, so the
    // assembled plan must differ — i.e. the registry name really reached the node.
    let empty = assembled_article_report(&root, RunId::new("eq2"), input, "2026-05-05", &[]);
    assert_ne!(
        assembled.write_plan, empty.write_plan,
        "a non-empty registry must change the output vs an empty one"
    );
}

#[test]
fn assembled_unified_pipeline_routes_article() {
    // The unified manifest's OTHER branch: an article input must route to the
    // article note (not the paper note) and emit source_routed{article}.
    let root = repo_root();
    let spec = read_spec(&root, "manifests/unified.pipeline.toml");
    let wiring = AppWiring::new(RunId::new("asm-uni-article"))
        .with_date_stamp("2026-05-04")
        .with_area("ai")
        .with_input_path(root.join("fixtures/article_clean/input.md"))
        .with_client("default_llm", cassette_client(&root))
        .with_registry("default", ConceptRegistry::from_slugs(&[]));

    let report = GraphAssembler::with_domain_nodes().assemble(&spec, wiring).unwrap().run().unwrap();
    assert_eq!(report.write_plan.len(), 1, "article note only");
    match &report.write_plan.ops[0] {
        WriteOp::VaultCreate(o) => assert!(
            o.path.as_str().starts_with("20-Areas/AI-Research/Topics/"),
            "got {}",
            o.path.as_str()
        ),
        other => panic!("expected VaultCreate, got {other:?}"),
    }
    let routed = report.events.iter().any(|e| {
        matches!(&e.kind, EventKind::SourceRouted { source_kind, .. } if source_kind == "article")
    });
    assert!(routed, "expected an observable source_routed{{article}} event");
}

#[test]
fn assembled_article_pipeline_matches_manual_behavior() {
    let root = repo_root();
    let spec = read_spec(&root, "manifests/article.pipeline.toml");
    let wiring = AppWiring::new(RunId::new("asm-article"))
        .with_date_stamp("2026-05-04")
        .with_area("ai")
        .with_input_path(root.join("fixtures/article_clean/input.md"))
        .with_client("default_llm", cassette_client(&root))
        .with_registry("default", ConceptRegistry::from_slugs(&[]));

    let runner = GraphAssembler::with_domain_nodes().assemble(&spec, wiring).unwrap();
    let report = runner.run().unwrap();

    // Same core outcomes the hand-wired article_clean test asserts.
    assert_eq!(report.records_dropped, 0);
    assert_eq!(report.records_forwarded_to_sinks, 1);
    assert_eq!(report.write_plan.len(), 1);
    match &report.write_plan.ops[0] {
        WriteOp::VaultCreate(o) => {
            assert!(
                o.path.as_str().starts_with("20-Areas/AI-Research/Topics/2026-05/"),
                "got {}",
                o.path.as_str()
            );
            assert!(
                o.body.contains("https://every.to/guides/ai-product-management-guide"),
                "frontmatter should round-trip the source url"
            );
        }
        other => panic!("expected VaultCreate, got {other:?}"),
    }
}

#[test]
fn assembled_unified_pipeline_routes_paper() {
    let root = repo_root();
    let spec = read_spec(&root, "manifests/unified.pipeline.toml");
    let wiring = AppWiring::new(RunId::new("asm-paper"))
        .with_date_stamp("2026-05-29")
        .with_area("ai")
        .with_input_path(root.join("fixtures/paper_arxiv/input.md"))
        .with_client("default_llm", cassette_client(&root))
        .with_registry("default", ConceptRegistry::from_slugs(&[]));

    let runner = GraphAssembler::with_domain_nodes().assemble(&spec, wiring).unwrap();
    let report = runner.run().unwrap();

    assert_eq!(report.write_plan.len(), 1, "paper note only");
    match &report.write_plan.ops[0] {
        WriteOp::VaultCreate(o) => assert!(
            o.path.as_str().starts_with("20-Areas/AI-Research/Papers/"),
            "got {}",
            o.path.as_str()
        ),
        other => panic!("expected VaultCreate, got {other:?}"),
    }
    let routed = report.events.iter().any(|e| {
        matches!(&e.kind, EventKind::SourceRouted { source_kind, .. } if source_kind == "paper")
    });
    assert!(routed, "expected an observable source_routed{{paper}} event");
}

#[test]
fn assembled_evergreen_pipeline_applies_through_composite() {
    let root = repo_root();
    let spec = read_spec(&root, "manifests/article_evergreen.pipeline.toml");
    let wiring = AppWiring::new(RunId::new("asm-evg"))
        .with_date_stamp("2026-05-04")
        .with_area("ai")
        .with_input_path(root.join("fixtures/article_clean/input.md"))
        .with_client("default_llm", cassette_client(&root))
        .with_registry("default", ConceptRegistry::from_slugs(&[]));

    let runner = GraphAssembler::with_domain_nodes().assemble(&spec, wiring).unwrap();
    let report = runner.run().unwrap();

    let canon_upserts = report
        .write_plan
        .ops
        .iter()
        .filter(|o| matches!(o, WriteOp::CanonicalUpsert(_)))
        .count();
    assert!(canon_upserts >= 1, "evergreen pipeline must emit canonical upserts");

    let vault = tempfile::tempdir().unwrap();
    let canon = tempfile::tempdir().unwrap();
    let mut applier = CompositePlanApplier::new(vec![
        Box::new(VaultFsPlanApplier::new(vault.path())),
        Box::new(CanonicalFsStoreApplier::new(canon.path())),
    ]);
    let apply = applier.apply(&report.write_plan, ApplyMode::Apply);
    let counts = apply.counts();
    assert_eq!(counts.failed, 0, "no failed ops");
    assert_eq!(counts.unsupported, 0, "composite leaves nothing unsupported");
    assert_eq!(counts.applied as usize, report.write_plan.len(), "every op applied");

    // The canonical store is self-consistent: read_all + strict parse succeed,
    // one concept per upsert.
    let store = CanonicalFsStoreApplier::new(canon.path());
    let concepts = CanonicalConcept::try_parse_pairs(store.read_all().unwrap()).unwrap();
    assert_eq!(concepts.len(), canon_upserts);
}

#[test]
fn unknown_kind_is_a_clear_error() {
    let toml = r#"
        [pipeline]
        nodes = ["src", "snk"]
        edges = [["src", "snk"]]
        [assembly.src]
        kind = "source.markdown_inbox"
        [assembly.snk]
        kind = "sink.nonexistent"
    "#;
    let spec = DomainPipelineSpec::parse(toml).unwrap();
    let wiring = AppWiring::new(RunId::new("x")).with_input_path("/tmp/x.md");
    let err = GraphAssembler::with_domain_nodes().assemble(&spec, wiring).err().expect("expected assembly to fail");
    match err {
        AssemblyError::UnknownKind { node_id, kind } => {
            assert_eq!(node_id, "snk");
            assert_eq!(kind, "sink.nonexistent");
        }
        other => panic!("expected UnknownKind, got {other:?}"),
    }
}

#[test]
fn missing_wiring_is_a_clear_error() {
    let root = repo_root();
    let spec = read_spec(&root, "manifests/article.pipeline.toml");
    // No client registered under "default_llm" → llm_invoker can't bind.
    // (date_stamp is set so we get past the runtime-wiring date check.)
    let wiring = AppWiring::new(RunId::new("x"))
        .with_date_stamp("2026-05-04")
        .with_input_path(root.join("fixtures/article_clean/input.md"))
        .with_registry("default", ConceptRegistry::from_slugs(&[]));
    let err = GraphAssembler::with_domain_nodes().assemble(&spec, wiring).err().expect("expected assembly to fail");
    match err {
        AssemblyError::MissingWiring { node_id, name } => {
            assert_eq!(node_id, "llm_invoker");
            assert_eq!(name, "default_llm");
        }
        other => panic!("expected MissingWiring, got {other:?}"),
    }
}

#[test]
fn floating_node_is_disconnected_graph() {
    // `floating` is a valid transform but wired to nothing — it would silently
    // never run. Assembly must reject it.
    let toml = r#"
        [pipeline]
        nodes = ["src", "a", "snk", "floating"]
        edges = [["src", "a"], ["a", "snk"]]
        [assembly.src]
        kind = "source.markdown_inbox"
        [assembly.a]
        kind = "transform.source_resolver"
        [assembly.snk]
        kind = "sink.article_vault_plan"
        [assembly.floating]
        kind = "transform.source_resolver"
    "#;
    let spec = DomainPipelineSpec::parse(toml).unwrap();
    let wiring = AppWiring::new(RunId::new("x")).with_input_path("/tmp/x.md");
    let err = GraphAssembler::with_domain_nodes().assemble(&spec, wiring).err().expect("expected assembly to fail");
    match err {
        AssemblyError::DisconnectedGraph { node_id, .. } => assert_eq!(node_id, "floating"),
        other => panic!("expected DisconnectedGraph, got {other:?}"),
    }
}

#[test]
fn source_with_no_outbound_is_disconnected_graph() {
    // A source that reaches no sink (and a sink fed by nothing): both halves of
    // the pipeline are dead.
    let toml = r#"
        [pipeline]
        nodes = ["src", "snk"]
        edges = []
        [assembly.src]
        kind = "source.markdown_inbox"
        [assembly.snk]
        kind = "sink.article_vault_plan"
    "#;
    let spec = DomainPipelineSpec::parse(toml).unwrap();
    let wiring = AppWiring::new(RunId::new("x")).with_input_path("/tmp/x.md");
    let err = GraphAssembler::with_domain_nodes().assemble(&spec, wiring).err().expect("expected assembly to fail");
    assert!(matches!(err, AssemblyError::DisconnectedGraph { .. }), "got {err:?}");
}

#[test]
fn cyclic_graph_is_rejected_at_assembly() {
    // a↔b is a cycle. Every node is still source-reachable and sink-reaching,
    // so the reachability check alone would pass — the topo_order pass must
    // catch the cycle at assembly time, not defer it to run().
    let toml = r#"
        [pipeline]
        nodes = ["src", "a", "b", "snk"]
        edges = [["src", "a"], ["a", "b"], ["b", "a"], ["b", "snk"]]
        [assembly.src]
        kind = "source.markdown_inbox"
        [assembly.a]
        kind = "transform.source_resolver"
        [assembly.b]
        kind = "transform.source_resolver"
        [assembly.snk]
        kind = "sink.article_vault_plan"
    "#;
    let spec = DomainPipelineSpec::parse(toml).unwrap();
    let wiring = AppWiring::new(RunId::new("x")).with_input_path("/tmp/x.md");
    let err = GraphAssembler::with_domain_nodes().assemble(&spec, wiring).err().expect("expected assembly to fail");
    assert!(matches!(err, AssemblyError::Manifest(_)), "expected a cycle/Manifest error, got {err:?}");
}

#[test]
fn config_on_wrong_kind_is_unexpected_config() {
    // `client` is meaningful only on effect.llm_invoker; on a source it would
    // be silently ignored — a typo trap. Assembly must reject it.
    let toml = r#"
        [pipeline]
        nodes = ["src", "snk"]
        edges = [["src", "snk"]]
        [assembly.src]
        kind = "source.markdown_inbox"
        config = { client = "default_llm" }
        [assembly.snk]
        kind = "sink.article_vault_plan"
    "#;
    let spec = DomainPipelineSpec::parse(toml).unwrap();
    let wiring = AppWiring::new(RunId::new("x")).with_input_path("/tmp/x.md");
    let err = GraphAssembler::with_domain_nodes().assemble(&spec, wiring).err().expect("expected assembly to fail");
    match err {
        AssemblyError::UnexpectedConfig { node_id, field, .. } => {
            assert_eq!(node_id, "src");
            assert_eq!(field, "client");
        }
        other => panic!("expected UnexpectedConfig, got {other:?}"),
    }
}

#[test]
fn missing_date_stamp_is_a_clear_error() {
    // The article manifest has article_parser, which needs a date_stamp.
    let root = repo_root();
    let spec = read_spec(&root, "manifests/article.pipeline.toml");
    let wiring = AppWiring::new(RunId::new("x"))
        .with_input_path(root.join("fixtures/article_clean/input.md"))
        .with_client("default_llm", cassette_client(&root))
        .with_registry("default", ConceptRegistry::from_slugs(&[]));
    let err = GraphAssembler::with_domain_nodes().assemble(&spec, wiring).err().expect("expected assembly to fail");
    match err {
        AssemblyError::MissingWiring { node_id, name } => {
            assert_eq!(node_id, "article_parser");
            assert_eq!(name, "date_stamp");
        }
        other => panic!("expected MissingWiring(date_stamp), got {other:?}"),
    }
}

#[test]
fn malformed_date_stamp_is_invalid_wiring() {
    let root = repo_root();
    let spec = read_spec(&root, "manifests/article.pipeline.toml");
    let wiring = AppWiring::new(RunId::new("x"))
        .with_date_stamp("May 4 2026")
        .with_input_path(root.join("fixtures/article_clean/input.md"))
        .with_client("default_llm", cassette_client(&root))
        .with_registry("default", ConceptRegistry::from_slugs(&[]));
    let err = GraphAssembler::with_domain_nodes().assemble(&spec, wiring).err().expect("expected assembly to fail");
    match err {
        AssemblyError::InvalidWiring { node_id, name, .. } => {
            assert_eq!(node_id, "article_parser");
            assert_eq!(name, "date_stamp");
        }
        other => panic!("expected InvalidWiring(date_stamp), got {other:?}"),
    }
}

#[test]
fn missing_input_path_fails_before_build() {
    let root = repo_root();
    let spec = read_spec(&root, "manifests/article.pipeline.toml");
    // Everything but the source's input_path.
    let wiring = AppWiring::new(RunId::new("x"))
        .with_date_stamp("2026-05-04")
        .with_client("default_llm", cassette_client(&root))
        .with_registry("default", ConceptRegistry::from_slugs(&[]));
    let err = GraphAssembler::with_domain_nodes().assemble(&spec, wiring).err().expect("expected assembly to fail");
    match err {
        AssemblyError::MissingWiring { node_id, name } => {
            assert_eq!(node_id, "markdown_inbox");
            assert_eq!(name, "input_path");
        }
        other => panic!("expected MissingWiring(input_path), got {other:?}"),
    }
}

#[test]
fn duplicate_client_binding_fails_before_build() {
    // Two effect.llm_invoker nodes bind the same move-only client.
    let toml = r#"
        [pipeline]
        nodes = ["src", "llm1", "llm2", "snk"]
        edges = [["src", "llm1"], ["llm1", "llm2"], ["llm2", "snk"]]
        [assembly.src]
        kind = "source.markdown_inbox"
        [assembly.llm1]
        kind = "effect.llm_invoker"
        config = { client = "shared" }
        [assembly.llm2]
        kind = "effect.llm_invoker"
        config = { client = "shared" }
        [assembly.snk]
        kind = "sink.article_vault_plan"
    "#;
    let root = repo_root();
    let spec = DomainPipelineSpec::parse(toml).unwrap();
    let wiring = AppWiring::new(RunId::new("x"))
        .with_input_path("/tmp/x.md")
        .with_client("shared", cassette_client(&root));
    let err = GraphAssembler::with_domain_nodes().assemble(&spec, wiring).err().expect("expected assembly to fail");
    match err {
        AssemblyError::ClientReused { client, first_node, second_node } => {
            assert_eq!(client, "shared");
            assert_eq!(first_node, "llm1");
            assert_eq!(second_node, "llm2");
        }
        other => panic!("expected ClientReused, got {other:?}"),
    }
}

#[test]
fn missing_registry_fails_before_build() {
    let root = repo_root();
    let spec = read_spec(&root, "manifests/article.pipeline.toml");
    // No registry registered under "default".
    let wiring = AppWiring::new(RunId::new("x"))
        .with_date_stamp("2026-05-04")
        .with_input_path(root.join("fixtures/article_clean/input.md"))
        .with_client("default_llm", cassette_client(&root));
    let err = GraphAssembler::with_domain_nodes().assemble(&spec, wiring).err().expect("expected assembly to fail");
    match err {
        AssemblyError::MissingWiring { node_id, name } => {
            assert_eq!(node_id, "concept_resolver");
            assert_eq!(name, "default");
        }
        other => panic!("expected MissingWiring(default registry), got {other:?}"),
    }
}

#[test]
fn two_island_graph_is_rejected() {
    // Two independent source→sink pipelines in one manifest. Each island is
    // internally well-formed, so only the weak-connectivity check catches it.
    let toml = r#"
        [pipeline]
        nodes = ["s1", "k1", "s2", "k2"]
        edges = [["s1", "k1"], ["s2", "k2"]]
        [assembly.s1]
        kind = "source.markdown_inbox"
        [assembly.k1]
        kind = "sink.article_vault_plan"
        [assembly.s2]
        kind = "source.markdown_inbox"
        [assembly.k2]
        kind = "sink.article_vault_plan"
    "#;
    let spec = DomainPipelineSpec::parse(toml).unwrap();
    let wiring = AppWiring::new(RunId::new("x")).with_input_path("/tmp/x.md");
    let err = GraphAssembler::with_domain_nodes().assemble(&spec, wiring).err().expect("expected assembly to fail");
    assert!(matches!(err, AssemblyError::DisconnectedGraph { .. }), "got {err:?}");
}

#[test]
fn calendar_invalid_date_is_invalid_wiring() {
    let root = repo_root();
    let spec = read_spec(&root, "manifests/article.pipeline.toml");
    // Shape-valid but not a real date (Feb 30).
    let wiring = AppWiring::new(RunId::new("x"))
        .with_date_stamp("2026-02-30")
        .with_input_path(root.join("fixtures/article_clean/input.md"))
        .with_client("default_llm", cassette_client(&root))
        .with_registry("default", ConceptRegistry::from_slugs(&[]));
    let err = GraphAssembler::with_domain_nodes().assemble(&spec, wiring).err().expect("expected assembly to fail");
    match err {
        AssemblyError::InvalidWiring { node_id, name, .. } => {
            assert_eq!(node_id, "article_parser");
            assert_eq!(name, "date_stamp");
        }
        other => panic!("expected InvalidWiring, got {other:?}"),
    }
}

#[test]
fn source_with_inbound_edge_is_category_mismatch() {
    // `a` is declared a source but the topology gives it an inbound edge.
    let toml = r#"
        [pipeline]
        nodes = ["a", "b"]
        edges = [["a", "b"], ["b", "a"]]
        [assembly.a]
        kind = "source.markdown_inbox"
        [assembly.b]
        kind = "sink.article_vault_plan"
    "#;
    let spec = DomainPipelineSpec::parse(toml).unwrap();
    let wiring = AppWiring::new(RunId::new("x")).with_input_path("/tmp/x.md");
    let err = GraphAssembler::with_domain_nodes().assemble(&spec, wiring).err().expect("expected assembly to fail");
    assert!(matches!(err, AssemblyError::CategoryMismatch { .. }), "got {err:?}");
}
