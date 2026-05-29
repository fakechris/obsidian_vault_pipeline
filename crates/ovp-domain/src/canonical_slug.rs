use std::fmt;

/// The single canonical-slug rule for the whole domain.
///
/// A concept's slug is used in three coupled places that MUST agree:
/// - the canonical store key (`<store>/<slug>.json`),
/// - the evergreen page path (`10-Knowledge/Evergreen/<slug>.md`),
/// - the in-pipeline record id (`evg-<slug>`).
///
/// If those diverge, a concept written under one key becomes invisible
/// under another — e.g. a `/` in the slug nests the canonical file in a
/// subdirectory where `CanonicalFsStoreApplier::read_all` (top-level
/// `*.json` only) can never see it, silently dropping the concept from
/// every derived rebuild. This type is the one gate that guarantees a slug
/// is exactly ONE safe path segment, so the three uses can never diverge.
///
/// Validation rejects rather than silently mangles (the one normalization
/// is trimming surrounding ASCII whitespace):
/// - empty after trim → [`SlugError::Empty`]
/// - contains `/` or `\` (path separators) → [`SlugError::PathSeparator`]
/// - contains `..` (parent traversal) → [`SlugError::ParentDir`]
/// - contains interior whitespace → [`SlugError::Whitespace`]
/// - contains an ASCII control char → [`SlugError::Control`]
///
/// Unicode letters are allowed (e.g. `对话即工作`); the rule is about path
/// safety and single-segment integrity, not an ASCII restriction.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct CanonicalSlug(String);

/// Why a raw candidate is not a valid canonical slug.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SlugError {
    Empty,
    PathSeparator,
    ParentDir,
    Whitespace,
    Control,
}

impl SlugError {
    /// Stable dotted reason code, suitable for a `DropReason` / event log.
    pub fn code(&self) -> &'static str {
        match self {
            SlugError::Empty => "slug.empty",
            SlugError::PathSeparator => "slug.path_separator",
            SlugError::ParentDir => "slug.parent_dir",
            SlugError::Whitespace => "slug.whitespace",
            SlugError::Control => "slug.control_char",
        }
    }
}

impl fmt::Display for SlugError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let msg = match self {
            SlugError::Empty => "empty slug",
            SlugError::PathSeparator => "slug contains a path separator (`/` or `\\`)",
            SlugError::ParentDir => "slug contains `..`",
            SlugError::Whitespace => "slug contains interior whitespace",
            SlugError::Control => "slug contains a control character",
        };
        f.write_str(msg)
    }
}

impl std::error::Error for SlugError {}

impl CanonicalSlug {
    /// Validate + normalize a raw candidate into a canonical slug. The only
    /// normalization is trimming surrounding whitespace; anything that would
    /// make the slug unsafe or multi-segment is rejected.
    pub fn parse(raw: &str) -> Result<Self, SlugError> {
        let s = raw.trim();
        if s.is_empty() {
            return Err(SlugError::Empty);
        }
        if s.contains('/') || s.contains('\\') {
            return Err(SlugError::PathSeparator);
        }
        if s.contains("..") {
            return Err(SlugError::ParentDir);
        }
        for c in s.chars() {
            if c.is_whitespace() {
                return Err(SlugError::Whitespace);
            }
            if c.is_control() {
                return Err(SlugError::Control);
            }
        }
        Ok(Self(s.to_string()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    pub fn into_string(self) -> String {
        self.0
    }
}

impl fmt::Display for CanonicalSlug {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_plain_ascii_slug() {
        let s = CanonicalSlug::parse("agent-native-pm").unwrap();
        assert_eq!(s.as_str(), "agent-native-pm");
    }

    #[test]
    fn accepts_unicode_segment() {
        // The fixtures mint `对话即工作`; it is a valid single segment.
        let s = CanonicalSlug::parse("对话即工作").unwrap();
        assert_eq!(s.as_str(), "对话即工作");
    }

    #[test]
    fn accepts_numeric_slug() {
        // `8020` appears in the article fixture's candidates.
        assert_eq!(CanonicalSlug::parse("8020").unwrap().as_str(), "8020");
    }

    #[test]
    fn trims_surrounding_whitespace() {
        assert_eq!(CanonicalSlug::parse("  rag\n").unwrap().as_str(), "rag");
    }

    #[test]
    fn rejects_empty_and_whitespace_only() {
        assert_eq!(CanonicalSlug::parse(""), Err(SlugError::Empty));
        assert_eq!(CanonicalSlug::parse("   "), Err(SlugError::Empty));
    }

    #[test]
    fn rejects_slash_and_backslash() {
        assert_eq!(CanonicalSlug::parse("a/b"), Err(SlugError::PathSeparator));
        assert_eq!(CanonicalSlug::parse("a\\b"), Err(SlugError::PathSeparator));
    }

    #[test]
    fn rejects_parent_dir() {
        assert_eq!(CanonicalSlug::parse(".."), Err(SlugError::ParentDir));
        assert_eq!(CanonicalSlug::parse("a..b"), Err(SlugError::ParentDir));
    }

    #[test]
    fn rejects_interior_whitespace() {
        assert_eq!(CanonicalSlug::parse("agent native"), Err(SlugError::Whitespace));
    }

    #[test]
    fn error_codes_are_stable() {
        assert_eq!(SlugError::Empty.code(), "slug.empty");
        assert_eq!(SlugError::PathSeparator.code(), "slug.path_separator");
        assert_eq!(SlugError::ParentDir.code(), "slug.parent_dir");
    }
}
