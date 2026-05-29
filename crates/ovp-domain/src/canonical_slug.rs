use std::fmt;

/// Characters that `VaultLayout::sanitize_filename` rewrites to a space.
/// A slug containing any of these would render differently in the evergreen
/// page path (sanitized) than in the canonical store key (verbatim) —
/// exactly the divergence this type exists to prevent. `/` and `\` are also
/// in that sanitized set but are reported separately as
/// [`SlugError::PathSeparator`] (they additionally enable nesting/traversal).
const FILENAME_RESERVED: [char; 7] = [':', '*', '?', '"', '<', '>', '|'];

/// The single canonical-slug rule for the whole domain.
///
/// A concept's slug is used in coupled places that MUST agree byte-for-byte:
/// - the canonical store key (`<store>/<slug>.json`),
/// - the evergreen page path (`10-Knowledge/Evergreen/<slug>.md`, built by
///   `VaultLayout::evergreen_note`, which runs the slug through
///   `sanitize_filename`),
/// - the in-pipeline record id (`evg-<slug>`).
///
/// If those diverge, a concept written under one key becomes invisible under
/// another — e.g. a `/` nests the canonical file where
/// `CanonicalFsStoreApplier::read_all` (top-level `*.json` only) can't see
/// it, and a `:` (or any reserved char) is sanitized to a space in the
/// evergreen filename but left intact in the canonical key. This type is the
/// one gate guaranteeing a slug is exactly ONE filename-safe path segment
/// that survives `VaultLayout` sanitization unchanged, so the uses agree.
///
/// Validation rejects rather than silently mangles (the one normalization is
/// trimming surrounding whitespace):
/// - empty after trim → [`SlugError::Empty`]
/// - `/` or `\` (path separators) → [`SlugError::PathSeparator`]
/// - `..` (parent traversal) → [`SlugError::ParentDir`]
/// - dot-only (`.`) → [`SlugError::DotOnly`]
/// - any of `: * ? " < > |` (VaultLayout-sanitized) → [`SlugError::FilenameReserved`]
/// - interior whitespace → [`SlugError::Whitespace`]
/// - ASCII control char → [`SlugError::Control`]
///
/// Unicode letters are allowed (e.g. `对话即工作`); the rule is about
/// filename safety and single-segment integrity, not an ASCII restriction.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct CanonicalSlug(String);

/// Why a raw candidate is not a valid canonical slug.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SlugError {
    Empty,
    PathSeparator,
    ParentDir,
    DotOnly,
    FilenameReserved,
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
            SlugError::DotOnly => "slug.dot_only",
            SlugError::FilenameReserved => "slug.filename_reserved",
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
            SlugError::DotOnly => "slug is dot-only",
            SlugError::FilenameReserved => {
                "slug contains a filename-reserved character (one of `: * ? \" < > |`)"
            }
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
    /// make the slug unsafe, multi-segment, or VaultLayout-divergent is
    /// rejected.
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
        // A key made only of `.` confuses filename stem/extension splitting
        // and maps to a nonsense vault path. (`..`/`...` are already rejected
        // as ParentDir above; this catches the lone `.`.)
        if s.chars().all(|c| c == '.') {
            return Err(SlugError::DotOnly);
        }
        for c in s.chars() {
            if c.is_whitespace() {
                return Err(SlugError::Whitespace);
            }
            if c.is_control() {
                return Err(SlugError::Control);
            }
            if FILENAME_RESERVED.contains(&c) {
                return Err(SlugError::FilenameReserved);
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
    fn accepts_single_dot_inside_segment() {
        // A dot that isn't dot-only and isn't `..` is fine (it round-trips
        // through `<key>.json` and isn't sanitized by VaultLayout).
        assert_eq!(CanonicalSlug::parse("v1.2").unwrap().as_str(), "v1.2");
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
        assert_eq!(CanonicalSlug::parse("..."), Err(SlugError::ParentDir));
        assert_eq!(CanonicalSlug::parse("a..b"), Err(SlugError::ParentDir));
    }

    #[test]
    fn rejects_dot_only() {
        assert_eq!(CanonicalSlug::parse("."), Err(SlugError::DotOnly));
    }

    #[test]
    fn rejects_filename_reserved_chars() {
        // Every char VaultLayout::sanitize_filename would rewrite.
        for raw in ["a:b", "a*b", "a?b", "a\"b", "a<b", "a>b", "a|b"] {
            assert_eq!(
                CanonicalSlug::parse(raw),
                Err(SlugError::FilenameReserved),
                "expected {raw} rejected as filename-reserved"
            );
        }
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
        assert_eq!(SlugError::DotOnly.code(), "slug.dot_only");
        assert_eq!(SlugError::FilenameReserved.code(), "slug.filename_reserved");
    }
}
