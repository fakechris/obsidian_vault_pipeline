//! One diagnostic shape for every health surface.
//!
//! `lint`, `doctor` and `crystal-lint` each grew their own finding type with
//! its own severity scale and its own text rendering. Anything that wants to
//! consume them together — the portal's system page, the MCP `doctor` tool, an
//! agent repairing what it just wrote — had to parse three prose formats. This
//! module is the common projection: every source keeps its native type and
//! its native gate semantics, and converts INTO [`Diagnostic`] at the edge.
//!
//! Deliberately not a new state layer: nothing here is persisted, and nothing
//! here decides pass/fail for any command. The gates stay where they are.

use serde::{Deserialize, Serialize};

/// Severity on a single scale. `Error` is what a gate would fail on; `Warning`
/// is actionable but not blocking; `Info` is visibility only.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    Info,
    Warning,
    Error,
}

/// Which health surface produced the diagnostic. Codes are unique within an
/// origin, not across them.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Origin {
    Lint,
    Doctor,
    Crystal,
}

/// One finding. `code` is a stable dotted identifier (`wikilink.broken`,
/// `doctor.crystal-staleness`, `crystal.citation.quote_not_in_unit`) that a
/// consumer can match on; `message` is for people; `location` names the
/// offending file / concept / claim when there is one; `hint` is the concrete
/// next action when the producer knows it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Diagnostic {
    pub origin: Origin,
    pub severity: Severity,
    pub code: String,
    pub message: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub location: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hint: Option<String>,
}

/// A set of diagnostics in producer order.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct DiagnosticReport {
    pub diagnostics: Vec<Diagnostic>,
}

impl DiagnosticReport {
    pub fn new(diagnostics: Vec<Diagnostic>) -> Self {
        Self { diagnostics }
    }

    /// The most severe diagnostic, or `None` if clean.
    pub fn worst(&self) -> Option<Severity> {
        self.diagnostics.iter().map(|d| d.severity).max()
    }

    /// Count of diagnostics at exactly `severity`.
    pub fn count(&self, severity: Severity) -> usize {
        self.diagnostics
            .iter()
            .filter(|d| d.severity == severity)
            .count()
    }

    /// True iff nothing is at or above `threshold`.
    pub fn passed(&self, threshold: Severity) -> bool {
        !self.diagnostics.iter().any(|d| d.severity >= threshold)
    }

    /// Append another report's diagnostics, keeping order.
    pub fn extend(&mut self, other: DiagnosticReport) {
        self.diagnostics.extend(other.diagnostics);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(sev: Severity, code: &str) -> Diagnostic {
        Diagnostic {
            origin: Origin::Lint,
            severity: sev,
            code: code.into(),
            message: "m".into(),
            location: None,
            hint: None,
        }
    }

    #[test]
    fn report_gate_helpers_follow_lint_report_semantics() {
        let r = DiagnosticReport::new(vec![d(Severity::Info, "a"), d(Severity::Warning, "b")]);
        assert_eq!(r.worst(), Some(Severity::Warning));
        assert_eq!(r.count(Severity::Info), 1);
        assert!(r.passed(Severity::Error));
        assert!(!r.passed(Severity::Warning));
        assert_eq!(DiagnosticReport::default().worst(), None);
        assert!(DiagnosticReport::default().passed(Severity::Info));
    }

    #[test]
    fn serde_shape_is_stable_and_omits_empty_optionals() {
        let r = DiagnosticReport::new(vec![d(Severity::Error, "x.y")]);
        let s = serde_json::to_string(&r).unwrap();
        assert_eq!(
            s,
            r#"{"diagnostics":[{"origin":"lint","severity":"error","code":"x.y","message":"m"}]}"#
        );
        let back: DiagnosticReport = serde_json::from_str(&s).unwrap();
        assert_eq!(back, r);
    }
}
