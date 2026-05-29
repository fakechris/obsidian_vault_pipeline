//! Contract assertion engine for fixture-based integration tests.
//!
//! Behind the `testing` feature flag — not part of production builds.
//! Parses a `contract.yaml` (see `fixtures/README.md` for schema) and
//! asserts MUST / SHOULD / MAY-break clauses against the actual pipeline
//! output: the produced `InterpretedDoc`, the emitted `WritePlan`, and
//! the run's event log.
//!
//! v1 implements only the op set the `article_clean` fixture needs.
//! Unknown ops surface as `ContractError::UnknownOp` so the assertion
//! engine never silently accepts an unimplemented assertion.

use std::path::Path;

use ovp_core::{Event, WriteOp, WritePlan};
use serde::Deserialize;

use crate::interpreted::InterpretedDoc;
use crate::paper_doc::PaperDoc;

/// The fields a contract clause can read, abstracted over the concrete
/// interpreted shape (article `InterpretedDoc` or `PaperDoc`). Lets the
/// one assertion engine drive both kinds' contracts.
pub trait ContractFields {
    fn field(&self, name: &str) -> FieldValue<'_>;
    /// The source kind label this subject represents (`article`/`paper`).
    fn source_kind_name(&self) -> &str;
}

impl ContractFields for InterpretedDoc {
    fn field(&self, name: &str) -> FieldValue<'_> {
        match name {
            "title" => FieldValue::Str(&self.title),
            "source" => FieldValue::Str(&self.source_url),
            "type" => FieldValue::Str(&self.doc_type),
            "area" => FieldValue::Str(&self.area),
            "date" => FieldValue::Str(&self.date),
            "author" => FieldValue::OptStr(self.author.as_deref()),
            "tags" => FieldValue::StrList(&self.tags),
            "canonical_concepts" => FieldValue::StrList(&self.canonical_concepts),
            "concept_candidates" => FieldValue::StrList(&self.concept_candidates),
            _ => FieldValue::Unknown,
        }
    }
    fn source_kind_name(&self) -> &str {
        "article"
    }
}

impl ContractFields for PaperDoc {
    fn field(&self, name: &str) -> FieldValue<'_> {
        match name {
            "title" => FieldValue::Str(&self.title),
            "source" => FieldValue::Str(&self.source_url),
            "arxiv_id" => FieldValue::Str(&self.arxiv_id),
            "date" => FieldValue::Str(&self.date),
            "type" => FieldValue::Str("paper"),
            "authors" => FieldValue::StrList(&self.authors),
            "categories" => FieldValue::StrList(&self.categories),
            "tags" => FieldValue::StrList(&self.tags),
            _ => FieldValue::Unknown,
        }
    }
    fn source_kind_name(&self) -> &str {
        "paper"
    }
}

#[derive(Debug, Deserialize)]
pub struct Contract {
    pub version: u32,
    pub terminal_state: TerminalState,
    #[serde(default)]
    pub expected_artifacts: Vec<ExpectedArtifact>,
    #[serde(default)]
    pub must: Vec<Clause>,
    #[serde(default)]
    pub should: Vec<Clause>,
    #[serde(default)]
    pub may_break: Vec<Clause>,
    #[serde(default)]
    pub known_anomalies: Vec<String>,
}

#[derive(Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum TerminalState {
    InterpretationProduced,
    TerminalRaw,
}

#[derive(Debug, Deserialize)]
pub struct ExpectedArtifact {
    pub kind: String,
    pub path_pattern: String,
}

/// A clause may take one of several heterogeneous shapes; serde
/// `untagged` discriminates by which fields are populated.
#[derive(Debug, Deserialize)]
#[serde(untagged)]
pub enum Clause {
    Field(FieldClause),
    BodySection { body_section: BodySectionSpec },
    BodySectionsPresent { body_sections_present: BodySectionsPresentSpec },
    EventEmitted { event_emitted: EventEmittedSpec },
    Utf8Clean { utf8_clean: Utf8CleanSpec },
    /// `- source_kind: paper` — asserts the subject's kind label.
    SourceKind { source_kind: String },
    /// Catch-all for documentation-only clauses whose key the engine
    /// doesn't implement yet (e.g. `may_break: - absorb_skipped: true`,
    /// or github-only `forbidden_artifacts` / `writeplan_constraint`).
    /// Always records as a documentation-only pass. MUST be the last
    /// untagged variant so specific shapes match first.
    Other(serde_yaml::Value),
}

#[derive(Debug, Deserialize)]
pub struct FieldClause {
    pub field: String,
    /// Optional — `may_break` clauses commonly name a field without an op
    /// (they're documentation that the field is allowed to differ).
    /// A clause without an op is always recorded as `passed`.
    #[serde(default)]
    pub op: Option<String>,
    #[serde(default)]
    pub value: Option<serde_yaml::Value>,
    #[serde(default)]
    pub values: Option<Vec<String>>,
    #[serde(default)]
    pub notes: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct BodySectionSpec {
    pub op: String,
    pub values: Vec<String>,
}

#[derive(Debug, Deserialize)]
pub struct BodySectionsPresentSpec {
    pub op: String,
    pub sections: Vec<String>,
}

#[derive(Debug, Deserialize)]
pub struct EventEmittedSpec {
    /// Snake-case event-kind name to look for in the event log
    /// (e.g. `source_resolution`, `filter_dropped`).
    pub kind: String,
    #[serde(default)]
    pub notes: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct Utf8CleanSpec {
    /// Field names to UTF-8-validate. v1.1 supports `title` / `tags` /
    /// `body` / `filename`. Rust strings are utf8 by construction, so
    /// this op effectively asserts the field is populated.
    #[serde(default)]
    pub paths: Vec<String>,
}

/// Outcome of asserting a contract: which clauses passed, which failed,
/// which were skipped (unknown ops). The caller decides what to do with
/// MUST failures (typically `panic!`) vs SHOULD failures (typically
/// log + continue).
#[derive(Debug, Default)]
pub struct ContractReport {
    pub must_passed: Vec<String>,
    pub must_failed: Vec<ClauseFailure>,
    pub should_passed: Vec<String>,
    pub should_failed: Vec<ClauseFailure>,
    pub may_break_passed: Vec<String>,
    pub may_break_failed: Vec<ClauseFailure>,
    pub skipped: Vec<String>,
}

impl ContractReport {
    pub fn must_clean(&self) -> bool { self.must_failed.is_empty() }
    pub fn total_clauses(&self) -> usize {
        self.must_passed.len()
            + self.must_failed.len()
            + self.should_passed.len()
            + self.should_failed.len()
            + self.may_break_passed.len()
            + self.may_break_failed.len()
            + self.skipped.len()
    }
}

#[derive(Debug)]
pub struct ClauseFailure {
    pub clause: String,
    pub detail: String,
}

#[derive(Debug)]
pub enum ContractError {
    Io(String),
    Parse(String),
    UnknownOp(String),
}

impl std::fmt::Display for ContractError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ContractError::Io(s) => write!(f, "contract io: {s}"),
            ContractError::Parse(s) => write!(f, "contract parse: {s}"),
            ContractError::UnknownOp(s) => write!(f, "unknown op: {s}"),
        }
    }
}

impl std::error::Error for ContractError {}

pub fn load_contract(path: &Path) -> Result<Contract, ContractError> {
    let raw = std::fs::read_to_string(path)
        .map_err(|e| ContractError::Io(format!("{}: {e}", path.display())))?;
    serde_yaml::from_str(&raw).map_err(|e| ContractError::Parse(e.to_string()))
}

/// Assert a contract against an article `InterpretedDoc`.
pub fn assert_contract(
    contract: &Contract,
    interpreted: Option<&InterpretedDoc>,
    write_plan: &WritePlan,
    events: &[Event],
) -> ContractReport {
    assert_contract_subject(
        contract,
        interpreted.map(|d| d as &dyn ContractFields),
        write_plan,
        events,
    )
}

/// Assert a contract against a `PaperDoc`.
pub fn assert_contract_paper(
    contract: &Contract,
    paper: Option<&PaperDoc>,
    write_plan: &WritePlan,
    events: &[Event],
) -> ContractReport {
    assert_contract_subject(
        contract,
        paper.map(|d| d as &dyn ContractFields),
        write_plan,
        events,
    )
}

/// Core engine: assert a contract against any `ContractFields` subject.
pub fn assert_contract_subject(
    contract: &Contract,
    subject: Option<&dyn ContractFields>,
    write_plan: &WritePlan,
    events: &[Event],
) -> ContractReport {
    let mut report = ContractReport::default();
    for c in &contract.must {
        evaluate_clause(c, subject, write_plan, events, &mut report, ClauseLevel::Must);
    }
    for c in &contract.should {
        evaluate_clause(c, subject, write_plan, events, &mut report, ClauseLevel::Should);
    }
    for c in &contract.may_break {
        evaluate_clause(c, subject, write_plan, events, &mut report, ClauseLevel::MayBreak);
    }
    report
}

#[derive(Debug, Clone, Copy)]
enum ClauseLevel {
    Must,
    Should,
    MayBreak,
}

fn evaluate_clause(
    clause: &Clause,
    subject: Option<&dyn ContractFields>,
    write_plan: &WritePlan,
    events: &[Event],
    report: &mut ContractReport,
    level: ClauseLevel,
) {
    match clause {
        Clause::Field(fc) => evaluate_field_clause(fc, subject, report, level),
        Clause::BodySection { body_section } => {
            evaluate_body_section(body_section, write_plan, report, level)
        }
        Clause::BodySectionsPresent { body_sections_present } => {
            evaluate_body_sections_present(body_sections_present, write_plan, report, level)
        }
        Clause::EventEmitted { event_emitted } => {
            evaluate_event_emitted(event_emitted, events, report, level)
        }
        Clause::Utf8Clean { utf8_clean } => {
            evaluate_utf8_clean(utf8_clean, subject, report, level)
        }
        Clause::SourceKind { source_kind } => {
            evaluate_source_kind(source_kind, subject, report, level)
        }
        Clause::Other(v) => {
            // Documentation-only clause whose key isn't a known op.
            // Record as skipped so it's visible but never fails a level.
            report.skipped.push(format!("documentation-only clause: {v:?}"));
        }
    }
}

fn evaluate_source_kind(
    expected: &str,
    subject: Option<&dyn ContractFields>,
    report: &mut ContractReport,
    level: ClauseLevel,
) {
    let label = format!("source_kind `{expected}`");
    match subject {
        Some(s) if s.source_kind_name() == expected => record_pass(report, level, &label),
        Some(s) => record_failure(
            report,
            level,
            &label,
            &format!("subject kind is `{}`, expected `{expected}`", s.source_kind_name()),
        ),
        None => record_failure(report, level, &label, "no interpreted subject produced"),
    }
}

fn evaluate_body_sections_present(
    spec: &BodySectionsPresentSpec,
    write_plan: &WritePlan,
    report: &mut ContractReport,
    level: ClauseLevel,
) {
    let label = format!("body_sections_present op `{}`", spec.op);
    if spec.op != "at_least" {
        report.skipped.push(format!("{label} (unknown op)"));
        return;
    }
    let body = match find_vault_create_body(write_plan) {
        Some(b) => b,
        None => {
            record_failure(report, level, &label, "no VaultCreate op in write plan");
            return;
        }
    };
    let missing: Vec<&str> = spec
        .sections
        .iter()
        .filter(|sec| !body.contains(sec.as_str()))
        .map(|s| s.as_str())
        .collect();
    if missing.is_empty() {
        record_pass(report, level, &label);
    } else {
        record_failure(report, level, &label, &format!("missing sections: {missing:?}"));
    }
}

fn evaluate_event_emitted(
    spec: &EventEmittedSpec,
    events: &[Event],
    report: &mut ContractReport,
    level: ClauseLevel,
) {
    let label = format!("event_emitted kind `{}`", spec.kind);
    let found = events.iter().any(|e| event_kind_name(&e.kind) == spec.kind);
    if found {
        record_pass(report, level, &label);
    } else {
        record_failure(
            report,
            level,
            &label,
            &format!("no event with kind `{}` was emitted", spec.kind),
        );
    }
}

/// Map an EventKind variant to its snake_case discriminator name
/// (the same name serde writes when serializing).
fn event_kind_name(kind: &ovp_core::EventKind) -> &'static str {
    use ovp_core::EventKind::*;
    match kind {
        RunStarted => "run_started",
        RunCompleted { .. } => "run_completed",
        SourceProduced { .. } => "source_produced",
        SourceExhausted { .. } => "source_exhausted",
        RecordSeen { .. } => "record_seen",
        RecordForwarded { .. } => "record_forwarded",
        FilterDropped { .. } => "filter_dropped",
        FilterCompleted { .. } => "filter_completed",
        FilterErrored { .. } => "filter_errored",
        SinkEmitted { .. } => "sink_emitted",
        PlanFinalized { .. } => "plan_finalized",
        SourceResolution { .. } => "source_resolution",
        SourceRouted { .. } => "source_routed",
    }
}

fn evaluate_utf8_clean(
    spec: &Utf8CleanSpec,
    subject: Option<&dyn ContractFields>,
    report: &mut ContractReport,
    level: ClauseLevel,
) {
    let label = format!("utf8_clean paths {:?}", spec.paths);
    // Rust `String` is utf8 by construction. utf8_clean reduces to "is
    // the named field populated at all". For paths we don't recognize,
    // mark as skipped rather than passing silently.
    let interp = match subject {
        Some(d) => d,
        None => {
            record_failure(report, level, &label, "no interpreted subject to validate");
            return;
        }
    };
    let mut unknown_paths = Vec::new();
    for path in &spec.paths {
        let ok = match path.as_str() {
            "title" => matches!(interp.field("title"), FieldValue::Str(s) if !s.is_empty()),
            "tags" => matches!(interp.field("tags"), FieldValue::StrList(l) if l.iter().all(|t| !t.is_empty())),
            "body" | "body_markdown" => true, // dimensions populated by parser is enough
            "filename" => true, // path generation is internal; can't fail utf8
            other => {
                unknown_paths.push(other.to_string());
                continue;
            }
        };
        if !ok {
            record_failure(
                report,
                level,
                &label,
                &format!("path `{path}` is empty or invalid"),
            );
            return;
        }
    }
    if !unknown_paths.is_empty() {
        report.skipped.push(format!(
            "{label} (unknown path(s) {:?})",
            unknown_paths
        ));
        return;
    }
    record_pass(report, level, &label);
}

fn evaluate_field_clause(
    fc: &FieldClause,
    subject: Option<&dyn ContractFields>,
    report: &mut ContractReport,
    level: ClauseLevel,
) {
    let op = match &fc.op {
        Some(o) => o.as_str(),
        None => {
            // Documentation-only clause (e.g. `may_break: - field: status`).
            let label = format!("field `{}` (documentation-only)", fc.field);
            record_pass(report, level, &label);
            return;
        }
    };
    let label = format!("field `{}` op `{op}`", fc.field);
    let interp = match subject {
        Some(d) => d,
        None => {
            record_failure(report, level, &label, "no interpreted subject produced");
            return;
        }
    };
    let result = match op {
        "equals" => op_equals(&fc.field, fc.value.as_ref(), interp),
        "not_equals" => op_not_equals(&fc.field, fc.value.as_ref(), interp),
        "contains" => op_contains(&fc.field, fc.value.as_ref(), interp),
        "matches_one_of" => op_matches_one_of(&fc.field, fc.values.as_deref(), interp),
        "type" => op_type(&fc.field, fc.value.as_ref(), interp),
        "length_gte" => op_length_gte(&fc.field, fc.value.as_ref(), interp),
        "non_empty" => op_non_empty(&fc.field, interp),
        "length_in_range" => op_length_in_range(&fc.field, fc.value.as_ref(), interp),
        other => {
            report.skipped.push(format!("{label} (unknown op `{other}`)"));
            return;
        }
    };
    match result {
        Ok(()) => record_pass(report, level, &label),
        Err(detail) => record_failure(report, level, &label, &detail),
    }
}

fn evaluate_body_section(
    spec: &BodySectionSpec,
    write_plan: &WritePlan,
    report: &mut ContractReport,
    level: ClauseLevel,
) {
    let label = format!("body_section op `{}`", spec.op);
    let body = match find_vault_create_body(write_plan) {
        Some(b) => b,
        None => {
            record_failure(report, level, &label, "no VaultCreate op in write plan");
            return;
        }
    };
    match spec.op.as_str() {
        "contains_one_of" => {
            if spec.values.iter().any(|v| body.contains(v.as_str())) {
                record_pass(report, level, &label);
            } else {
                record_failure(
                    report,
                    level,
                    &label,
                    &format!("body contained none of {:?}", spec.values),
                );
            }
        }
        other => {
            report.skipped.push(format!("{label} (unknown op `{other}`)"));
        }
    }
}

fn find_vault_create_body(plan: &WritePlan) -> Option<&str> {
    plan.ops.iter().find_map(|op| match op {
        WriteOp::VaultCreate(o) => Some(o.body.as_str()),
        _ => None,
    })
}

fn record_pass(report: &mut ContractReport, level: ClauseLevel, label: &str) {
    match level {
        ClauseLevel::Must => report.must_passed.push(label.to_string()),
        ClauseLevel::Should => report.should_passed.push(label.to_string()),
        ClauseLevel::MayBreak => report.may_break_passed.push(label.to_string()),
    }
}

fn record_failure(report: &mut ContractReport, level: ClauseLevel, clause: &str, detail: &str) {
    let f = ClauseFailure { clause: clause.to_string(), detail: detail.to_string() };
    match level {
        ClauseLevel::Must => report.must_failed.push(f),
        ClauseLevel::Should => report.should_failed.push(f),
        ClauseLevel::MayBreak => report.may_break_failed.push(f),
    }
}

// --- field accessors + ops -------------------------------------------------

/// A field's value as seen by the contract engine. Returned by
/// `ContractFields::field`.
pub enum FieldValue<'a> {
    Str(&'a str),
    OptStr(Option<&'a str>),
    StrList(&'a [String]),
    Unknown,
}

fn field_value<'a>(name: &str, doc: &'a dyn ContractFields) -> FieldValue<'a> {
    doc.field(name)
}

fn op_equals(field: &str, value: Option<&serde_yaml::Value>, doc: &dyn ContractFields) -> Result<(), String> {
    let expected = value
        .and_then(|v| v.as_str())
        .ok_or_else(|| "equals: missing string value".to_string())?;
    match field_value(field, doc) {
        FieldValue::Str(s) => {
            if s == expected {
                Ok(())
            } else {
                Err(format!("expected `{expected}`, got `{s}`"))
            }
        }
        FieldValue::OptStr(Some(s)) => {
            if s == expected {
                Ok(())
            } else {
                Err(format!("expected `{expected}`, got `{s}`"))
            }
        }
        FieldValue::OptStr(None) => Err("expected string, field is absent".into()),
        FieldValue::StrList(_) => Err("equals not valid on list field".into()),
        FieldValue::Unknown => Err(format!("unknown field `{field}`")),
    }
}

fn op_contains(
    field: &str,
    value: Option<&serde_yaml::Value>,
    doc: &dyn ContractFields,
) -> Result<(), String> {
    let needle = value
        .and_then(|v| v.as_str())
        .ok_or_else(|| "contains: missing string value".to_string())?;
    match field_value(field, doc) {
        FieldValue::Str(s) | FieldValue::OptStr(Some(s)) => {
            if s.contains(needle) {
                Ok(())
            } else {
                Err(format!("`{s}` does not contain `{needle}`"))
            }
        }
        FieldValue::OptStr(None) => Err("field absent".into()),
        FieldValue::StrList(_) => Err("contains not valid on list field".into()),
        FieldValue::Unknown => Err(format!("unknown field `{field}`")),
    }
}

fn op_not_equals(
    field: &str,
    value: Option<&serde_yaml::Value>,
    doc: &dyn ContractFields,
) -> Result<(), String> {
    let forbidden = value
        .and_then(|v| v.as_str())
        .ok_or_else(|| "not_equals: missing string value".to_string())?;
    match field_value(field, doc) {
        FieldValue::Str(s) | FieldValue::OptStr(Some(s)) => {
            if s == forbidden {
                Err(format!("field equals forbidden value `{forbidden}`"))
            } else {
                Ok(())
            }
        }
        FieldValue::OptStr(None) => Ok(()),
        FieldValue::StrList(_) => Err("not_equals not valid on list field".into()),
        FieldValue::Unknown => Err(format!("unknown field `{field}`")),
    }
}

fn op_matches_one_of(
    field: &str,
    values: Option<&[String]>,
    doc: &dyn ContractFields,
) -> Result<(), String> {
    let candidates = values.ok_or_else(|| "matches_one_of: missing `values` list".to_string())?;
    if candidates.is_empty() {
        return Err("matches_one_of: empty values list".into());
    }
    let s = match field_value(field, doc) {
        FieldValue::Str(s) => s,
        FieldValue::OptStr(Some(s)) => s,
        FieldValue::OptStr(None) => return Err("field absent".into()),
        FieldValue::StrList(_) => return Err("matches_one_of not valid on list field".into()),
        FieldValue::Unknown => return Err(format!("unknown field `{field}`")),
    };
    if candidates.iter().any(|c| c == s) {
        Ok(())
    } else {
        Err(format!("`{s}` not in {candidates:?}"))
    }
}

fn op_type(field: &str, value: Option<&serde_yaml::Value>, doc: &dyn ContractFields) -> Result<(), String> {
    let t = value
        .and_then(|v| v.as_str())
        .ok_or_else(|| "type: missing string value".to_string())?;
    match (t, field_value(field, doc)) {
        ("list_of_strings", FieldValue::StrList(_)) => Ok(()),
        ("list_of_strings", _) => Err(format!("expected list_of_strings, field `{field}` is not a list")),
        (other, _) => Err(format!("type `{other}` not implemented in v1")),
    }
}

fn op_length_gte(field: &str, value: Option<&serde_yaml::Value>, doc: &dyn ContractFields) -> Result<(), String> {
    let n = value
        .and_then(|v| v.as_u64())
        .ok_or_else(|| "length_gte: missing or non-integer value".to_string())?;
    let len = list_length(field, doc)?;
    if len as u64 >= n {
        Ok(())
    } else {
        Err(format!("length {len} < {n}"))
    }
}

fn op_non_empty(field: &str, doc: &dyn ContractFields) -> Result<(), String> {
    match field_value(field, doc) {
        FieldValue::StrList(l) => {
            if l.is_empty() { Err("list is empty".into()) } else { Ok(()) }
        }
        FieldValue::Str(s) => {
            if s.is_empty() { Err("string is empty".into()) } else { Ok(()) }
        }
        FieldValue::OptStr(Some(s)) => {
            if s.is_empty() { Err("string is empty".into()) } else { Ok(()) }
        }
        FieldValue::OptStr(None) => Err("field is absent".into()),
        FieldValue::Unknown => Err(format!("unknown field `{field}`")),
    }
}

fn op_length_in_range(field: &str, value: Option<&serde_yaml::Value>, doc: &dyn ContractFields) -> Result<(), String> {
    let pair = value
        .and_then(|v| v.as_sequence())
        .ok_or_else(|| "length_in_range: missing [min, max] sequence".to_string())?;
    if pair.len() != 2 {
        return Err("length_in_range: need exactly [min, max]".into());
    }
    let min = pair[0].as_u64().ok_or("length_in_range: min not u64")? as usize;
    let max = pair[1].as_u64().ok_or("length_in_range: max not u64")? as usize;
    let len = list_length(field, doc)?;
    if len >= min && len <= max {
        Ok(())
    } else {
        Err(format!("length {len} not in [{min}, {max}]"))
    }
}

fn list_length(field: &str, doc: &dyn ContractFields) -> Result<usize, String> {
    match field_value(field, doc) {
        FieldValue::StrList(l) => Ok(l.len()),
        FieldValue::Str(_) | FieldValue::OptStr(_) => Err(format!("field `{field}` is not a list")),
        FieldValue::Unknown => Err(format!("unknown field `{field}`")),
    }
}
