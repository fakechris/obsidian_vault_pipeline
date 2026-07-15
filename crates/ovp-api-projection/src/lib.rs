//! Shared, drift-proof builders for the read-only `/api/*` response bodies.
//!
//! Both the LIVE server (`ovp-server`, over HTTP) and the STATIC publisher
//! (`ovp-publish`, snapshotting to files) must return byte-identical JSON for
//! the read-only endpoints — otherwise the published site drifts from the app.
//! This crate is the single source of truth: pure `*_body` functions that turn
//! `(IndexModel, DurableRecords, sidecars)` into `serde_json::Value`, plus the
//! filesystem READER helpers they need and a `PublicView` redaction pass.
//!
//! The pure builders never touch the filesystem (parity with `ovp-console`'s
//! "no I/O during render"); the readers do, and are kept separate so a caller
//! reads once and feeds the results into the builders.

pub mod bodies;
pub mod graph;
pub mod readers;
pub mod redact;

pub use redact::PublicView;

/// Source markdown cap shipped in `/api/source/:sha` — kept here so the live
/// server and the publisher truncate identically.
pub const MAX_SOURCE_DOC_BYTES: usize = 200 * 1024;

/// A vault-relative path with no traversal, no absolute root, and no Windows
/// drive/UNC prefix (`C:\…`, `\\srv`) — those would make `Path::join` discard
/// the vault root. `rel_path` comes from our own index, but never trust it.
pub fn is_plain_relative(rel: &str) -> bool {
    if rel.is_empty() || rel.contains('\\') || rel.contains(':') {
        return false;
    }
    std::path::Path::new(rel)
        .components()
        .all(|c| matches!(c, std::path::Component::Normal(_)))
}
