//! Capture tier + worth_distilling prefilter (0-cost signals).
//!
//! Before the `$` reader trunk runs, decide whether a source is worth
//! unit-extraction. Low-signal bookmarks still can be lifecycle-closed as
//! "index-only" (Succeeded, 0 units) so they don't burn retries or LLM budget.

use serde::{Deserialize, Serialize};

/// How aggressive the daily reader is about paying `$` for unit extraction.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum CaptureTier {
    /// Only substantial bodies (default product thrift).
    Focused,
    /// Current production bar (~200 body chars).
    #[default]
    Balanced,
    /// Accept thinner notes; still skip pure bookmarks / empty shells.
    Comprehensive,
}

impl CaptureTier {
    pub fn parse(s: &str) -> Option<Self> {
        match s.trim().to_ascii_lowercase().as_str() {
            "focused" | "focus" => Some(Self::Focused),
            "balanced" | "balance" | "default" => Some(Self::Balanced),
            "comprehensive" | "full" | "all" => Some(Self::Comprehensive),
            _ => None,
        }
    }

    /// Minimum body characters for the tier (after trim).
    pub fn min_body_chars(self) -> usize {
        match self {
            Self::Focused => 800,
            Self::Balanced => 200,
            Self::Comprehensive => 40,
        }
    }
}

/// Why a source failed the worth gate (stable machine tokens for ledger/reason).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NotWorth {
    BodyTooShort { chars: usize, need: usize },
    BookmarkShell,
    EmptyTitleAndBody,
}

impl NotWorth {
    pub fn code(&self) -> String {
        match self {
            Self::BodyTooShort { chars, need } => {
                format!("not_worth:body_too_short:{chars}<{need}")
            }
            Self::BookmarkShell => "not_worth:bookmark_shell".into(),
            Self::EmptyTitleAndBody => "not_worth:empty".into(),
        }
    }
}

/// 0-cost prefilter: is this source worth running the reader trunk?
///
/// Signals (no LLM):
/// - body char count vs tier threshold
/// - "bookmark shell": body is almost only a URL / link line
/// - empty title+body
pub fn worth_distilling(title: &str, body: &str, tier: CaptureTier) -> Result<(), NotWorth> {
    let title = title.trim();
    let body = body.trim();
    if title.is_empty() && body.is_empty() {
        return Err(NotWorth::EmptyTitleAndBody);
    }
    let chars = body.chars().count();
    let need = tier.min_body_chars();
    if chars < need {
        return Err(NotWorth::BodyTooShort { chars, need });
    }
    if is_bookmark_shell(body) {
        return Err(NotWorth::BookmarkShell);
    }
    Ok(())
}

/// Body is essentially a URL or a one-line "saved link" with no prose.
fn is_bookmark_shell(body: &str) -> bool {
    let lines: Vec<&str> = body
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect();
    if lines.is_empty() {
        return true;
    }
    // ≤3 non-empty lines and every line is a URL or markdown link → shell.
    if lines.len() <= 3 && lines.iter().all(|l| looks_like_link_line(l)) {
        return true;
    }
    // Single long URL pasted as the whole body.
    if lines.len() == 1 && looks_like_url(lines[0]) {
        return true;
    }
    false
}

fn looks_like_url(s: &str) -> bool {
    let s = s.trim().trim_start_matches('<').trim_end_matches('>');
    s.starts_with("http://") || s.starts_with("https://")
}

fn looks_like_link_line(s: &str) -> bool {
    if looks_like_url(s) {
        return true;
    }
    // [title](http...)
    if s.starts_with('[') && s.contains("](http") {
        return true;
    }
    // bare domain-ish
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn focused_rejects_short_article() {
        let body = "x".repeat(300);
        assert!(worth_distilling("t", &body, CaptureTier::Focused).is_err());
        assert!(worth_distilling("t", &body, CaptureTier::Balanced).is_ok());
    }

    #[test]
    fn bookmark_shell_rejected() {
        // Long enough to pass the char floor so the shell detector fires.
        let body = format!(
            "https://example.com/a/long/path/{}\n",
            "x".repeat(100)
        );
        assert!(matches!(
            worth_distilling("bookmark", &body, CaptureTier::Comprehensive),
            Err(NotWorth::BookmarkShell)
        ));
    }

    #[test]
    fn real_prose_passes_balanced() {
        let body = "This article explains how grounded claims cite verbatim units \
                    from sources. ".repeat(5);
        assert!(worth_distilling("Grounded claims", &body, CaptureTier::Balanced).is_ok());
    }

    #[test]
    fn parse_tier() {
        assert_eq!(CaptureTier::parse("focused"), Some(CaptureTier::Focused));
        assert_eq!(CaptureTier::parse("BALANCED"), Some(CaptureTier::Balanced));
        assert_eq!(CaptureTier::parse("nope"), None);
    }
}
