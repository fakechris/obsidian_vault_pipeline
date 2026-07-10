//! Shared CLI defaults, so a value used by more than one command has a single
//! source of truth (no drift between `run-cycle`, `auto-run`, and
//! `interpret-article`).

/// Default canonical-evergreen seed used when no `--concept-registry` file is
/// supplied. Two entries cover the `article_mixed_lang` MUST clauses. Real runs
/// point `--concept-registry` at a registry JSON or (future) scan the vault's
/// evergreen dir.
pub const DEFAULT_CANONICAL_SLUGS: &[&str] = &["ai-agent", "competitive-advantage"];
