//! OVP Enrichment: web fetch, GitHub enrichment, image download.
//!
//! This crate only _enriches_ existing sources — it never owns intake, dedup,
//! or lifecycle (those stay in `ovp-intake`). It also never touches demoted
//! substrate or canonical stores.

pub mod web_fetch;
pub mod github;
pub mod image_download;

/// What a body rewrite replaced. Every enrich path that overwrites a note's
/// body (web fetch, GitHub) returns one of these so the caller can log a
/// `source_enriched` pipeline event — the rewrite is otherwise invisible: no
/// backup, no event, only a content-hash change between two intake sweeps.
///
/// Hashes are over the FULL file bytes (frontmatter + body), the same identity
/// the intake ledger keys on, so `old_sha256` matches the `needs-content`
/// record that put the file on the enrich list and `new_sha256` matches what
/// the next sweep will see.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BodyRewrite {
    /// `chars()` of the trimmed body that was replaced — the same measure the
    /// intake size gate (`MIN_READER_BODY_CHARS`) applies.
    pub old_body_chars: usize,
    pub old_sha256: String,
    pub new_sha256: String,
}

/// Lowercase hex sha256 of `bytes`. Mirrors `ovp_intake::hex_sha256` — this
/// crate must not depend on `ovp-intake` (the dependency already runs the
/// other way at the CLI), so the one-liner is duplicated rather than a crate
/// cycle added.
pub(crate) fn hex_sha256(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    format!("{:x}", Sha256::digest(bytes))
}
