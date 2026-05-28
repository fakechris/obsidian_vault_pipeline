//! Integration test for MarkdownInboxSource against the real article_clean
//! fixture. Verifies that frontmatter parsing matches what the legacy
//! pipeline produced for that file.

use ovp_core::{RunId, Source, SourceOutput, StepId};
use ovp_domain::*;

fn fixture_path(rel: &str) -> std::path::PathBuf {
    // tests/ is at crates/ovp-domain/tests/; the fixture is at the repo root.
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").unwrap();
    std::path::Path::new(&manifest_dir)
        .ancestors()
        .nth(2) // crates/ovp-domain → crates → repo root
        .unwrap()
        .join(rel)
}

#[test]
fn reads_article_clean_fixture() {
    let path = fixture_path("fixtures/article_clean/input.md");
    assert!(path.exists(), "fixture missing at {}", path.display());

    let run_id = RunId::new("md-inbox-test");
    let mut src = MarkdownInboxSource::new("md_inbox", run_id.clone(), &path);

    let out = src.produce();
    let records = match out {
        SourceOutput::Records(rs) => rs,
        other => panic!("expected Records, got {other:?}"),
    };
    assert_eq!(records.len(), 1);

    let body = match &records[0].body {
        DomainBody::Source(s) => s,
        other => panic!("expected DomainBody::Source, got {}", other.variant_name()),
    };

    assert_eq!(body.title, "A Guide to Agent-native Product Management");
    assert_eq!(
        body.source_url,
        "https://every.to/guides/ai-product-management-guide?source=post_button"
    );
    // Author wikilink should be unwrapped from `[[Marcus Moretti]]` to plain text.
    assert_eq!(body.author.as_deref(), Some("Marcus Moretti"));
    assert!(body.tags.contains(&"clippings".to_string()));
    // Body should contain article content, not the frontmatter.
    assert!(body.body_markdown.contains("product management"));
    assert!(!body.body_markdown.contains("source: \"https://every.to"));

    // Source is now exhausted.
    assert!(matches!(src.produce(), SourceOutput::Exhausted));
}

#[test]
fn step_id_round_trips() {
    let path = fixture_path("fixtures/article_clean/input.md");
    let src = MarkdownInboxSource::new("md_inbox", RunId::new("r"), &path);
    assert_eq!(src.step_id(), &StepId::new("md_inbox"));
}

#[test]
fn missing_file_surfaces_as_error() {
    let bogus = std::path::PathBuf::from("/this/does/not/exist.md");
    let mut src = MarkdownInboxSource::new("md_inbox", RunId::new("r"), &bogus);
    match src.produce() {
        SourceOutput::Error(e) => {
            assert_eq!(e.code.as_str(), "source.markdown_inbox.io");
        }
        other => panic!("expected Error, got {other:?}"),
    }
}
