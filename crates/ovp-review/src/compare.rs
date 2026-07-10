//! Compare the produced note against a frozen `--expected-dir`.
//!
//! Two layers:
//!  1. **Human diffs** — a line diff of `expected/frontmatter.yaml` vs the
//!     produced note's frontmatter, and of `expected/interpretation.md` vs the
//!     produced note's body. These are informational: formatting drift does NOT
//!     fail the review (LLM output legitimately varies).
//!  2. **Contract verdict** — if `expected/contract.yaml` is present, defer to
//!     the existing `ovp-domain` contract engine: parse the produced note's
//!     frontmatter into a `ContractFields` subject + synthesize a one-op write
//!     plan from the note body, then run the engine. This is the authoritative
//!     pass/fail on content, not raw text equality.

use std::path::Path;

use ovp_core::{ContentHash, OpId, RecordId, RunId, VaultCreateOp, VaultPath, WriteOp, WritePlan};
use ovp_domain::testing::{assert_contract_subject, load_contract, ContractFields, FieldValue};
use serde::Serialize;

/// The serializable part of a comparison (stored in the `ReviewReport`).
#[derive(Debug, Clone, Serialize)]
pub struct ComparisonSummary {
    pub expected_files: Vec<String>,
    pub actual_files: Vec<String>,
    /// Whether the produced frontmatter differs from `expected/frontmatter.yaml`.
    pub frontmatter_changed: bool,
    /// Whether the produced body differs from `expected/interpretation.md`.
    pub interpretation_changed: bool,
    /// The contract-engine verdict, if `expected/contract.yaml` was present and
    /// a produced note was available to check.
    pub contract: Option<ContractOutcome>,
}

/// A flattened, serializable view of the contract engine's `ContractReport`.
#[derive(Debug, Clone, Serialize)]
pub struct ContractOutcome {
    pub must_passed: usize,
    pub must_failed: usize,
    pub should_passed: usize,
    pub should_failed: usize,
    pub skipped: usize,
    /// True iff no MUST clause failed — the headline content verdict.
    pub must_clean: bool,
    /// `"<level>: <clause> — <detail>"` for every MUST/SHOULD failure.
    pub failures: Vec<String>,
}

/// The full comparison result: the serializable summary plus the rendered
/// artifacts the pack writes verbatim.
pub struct Comparison {
    pub summary: ComparisonSummary,
    pub summary_md: String,
    pub frontmatter_diff: String,
    pub interpretation_diff: String,
}

/// Run the comparison. `primary_note_rel` is the vault-relative path of the
/// produced note the harness discovered (may be `None` if nothing landed);
/// `vault_files` is every file produced under the vault root.
pub fn run(
    expected_dir: &Path,
    vault_root: &Path,
    primary_note_rel: Option<&str>,
    vault_files: &[String],
) -> Comparison {
    let expected_files = walk_relative(expected_dir);

    let note = primary_note_rel
        .map(|rel| vault_root.join(rel))
        .and_then(|p| std::fs::read_to_string(p).ok());
    // "Actual files" parallels "expected files": the full set produced under
    // the vault root, not just the primary note (the primary note is called out
    // separately in the report + REVIEW.md).
    let actual_files: Vec<String> = vault_files.to_vec();

    let (actual_fm, actual_body) = match &note {
        Some(content) => split_frontmatter(content),
        None => (None, String::new()),
    };

    // --- frontmatter diff ---
    let expected_fm = read(&expected_dir.join("frontmatter.yaml"));
    let (frontmatter_diff, frontmatter_changed) = match (&expected_fm, &actual_fm) {
        (None, _) => ("(no expected/frontmatter.yaml)\n".to_string(), false),
        (Some(_), None) => {
            ("(no frontmatter found in produced note)\n".to_string(), true)
        }
        (Some(e), Some(a)) => line_diff(e, a),
    };

    // --- interpretation (body) diff ---
    let expected_interp = read(&expected_dir.join("interpretation.md"));
    let (interpretation_diff, interpretation_changed) = match (&expected_interp, &note) {
        (None, _) => ("(no expected/interpretation.md)\n".to_string(), false),
        (Some(_), None) => ("(no produced note to compare)\n".to_string(), true),
        (Some(e), Some(_)) => line_diff(e, &actual_body),
    };

    // --- contract verdict ---
    let contract = run_contract(expected_dir, note.as_deref());

    let summary = ComparisonSummary {
        expected_files,
        actual_files,
        frontmatter_changed,
        interpretation_changed,
        contract,
    };
    let summary_md = render_summary_md(&summary);

    Comparison { summary, summary_md, frontmatter_diff, interpretation_diff }
}

/// Defer to the `ovp-domain` contract engine. Returns `None` when there is no
/// `contract.yaml`, no produced note, or the contract can't be loaded.
fn run_contract(expected_dir: &Path, note: Option<&str>) -> Option<ContractOutcome> {
    let contract_path = expected_dir.join("contract.yaml");
    if !contract_path.exists() {
        return None;
    }
    let contract = load_contract(&contract_path).ok()?;
    let note = note?;
    let (fm, _body) = split_frontmatter(note);
    let subject = fm.as_deref().and_then(ReviewedFrontmatter::from_yaml);

    // Synthesize a single VaultCreate carrying the full note so the engine's
    // body_section clauses can read it. Field clauses read the parsed subject;
    // event clauses can't be checked from disk (no event log) and would surface
    // as failures — see the crate-level non-goals.
    let plan = synthetic_plan(note);

    let report = assert_contract_subject(
        &contract,
        subject.as_ref().map(|s| s as &dyn ContractFields),
        &plan,
        &[],
    );

    let mut failures: Vec<String> = report
        .must_failed
        .iter()
        .map(|f| format!("must: {} — {}", f.clause, f.detail))
        .collect();
    failures.extend(
        report
            .should_failed
            .iter()
            .map(|f| format!("should: {} — {}", f.clause, f.detail)),
    );

    Some(ContractOutcome {
        must_passed: report.must_passed.len(),
        must_failed: report.must_failed.len(),
        should_passed: report.should_passed.len(),
        should_failed: report.should_failed.len(),
        skipped: report.skipped.len(),
        must_clean: report.must_clean(),
        failures,
    })
}

/// The produced note's frontmatter, coerced into the fields the article AND
/// paper contracts read (the two interpreted shapes the pipeline emits). One
/// subject covers both: a contract only queries the fields it declares, so the
/// extra fields are inert for the other kind. Tolerant: scalars (incl. numbers)
/// become strings, missing fields become empty — a missing field then fails its
/// clause cleanly rather than crashing the harness.
struct ReviewedFrontmatter {
    title: String,
    source: String,
    doc_type: String,
    area: String,
    date: String,
    author: Option<String>,
    tags: Vec<String>,
    canonical_concepts: Vec<String>,
    concept_candidates: Vec<String>,
    // Paper-only frontmatter (absent on article notes).
    arxiv_id: String,
    authors: Vec<String>,
    categories: Vec<String>,
}

impl ReviewedFrontmatter {
    fn from_yaml(fm: &str) -> Option<Self> {
        let value: serde_yaml::Value = serde_yaml::from_str(fm).ok()?;
        Some(Self {
            title: scalar(&value, "title").unwrap_or_default(),
            source: scalar(&value, "source").unwrap_or_default(),
            doc_type: scalar(&value, "type").unwrap_or_default(),
            area: scalar(&value, "area").unwrap_or_default(),
            date: scalar(&value, "date").unwrap_or_default(),
            author: scalar(&value, "author"),
            tags: list(&value, "tags"),
            canonical_concepts: list(&value, "canonical_concepts"),
            concept_candidates: list(&value, "concept_candidates"),
            arxiv_id: scalar(&value, "arxiv_id").unwrap_or_default(),
            authors: list(&value, "authors"),
            categories: list(&value, "categories"),
        })
    }
}

impl ContractFields for ReviewedFrontmatter {
    fn field(&self, name: &str) -> FieldValue<'_> {
        match name {
            "title" => FieldValue::Str(&self.title),
            "source" => FieldValue::Str(&self.source),
            "type" => FieldValue::Str(&self.doc_type),
            "area" => FieldValue::Str(&self.area),
            "date" => FieldValue::Str(&self.date),
            "author" => FieldValue::OptStr(self.author.as_deref()),
            "tags" => FieldValue::StrList(&self.tags),
            "canonical_concepts" => FieldValue::StrList(&self.canonical_concepts),
            "concept_candidates" => FieldValue::StrList(&self.concept_candidates),
            "arxiv_id" => FieldValue::Str(&self.arxiv_id),
            "authors" => FieldValue::StrList(&self.authors),
            "categories" => FieldValue::StrList(&self.categories),
            _ => FieldValue::Unknown,
        }
    }

    /// Reflect the produced note's `type` so a `source_kind: paper` clause is
    /// judged against the real kind, not a hardcoded one.
    fn source_kind_name(&self) -> &str {
        if self.doc_type == "paper" {
            "paper"
        } else {
            "article"
        }
    }
}

fn synthetic_plan(note_body: &str) -> WritePlan {
    let mut plan = WritePlan::new(RunId::new("review-compare"));
    plan.push(WriteOp::VaultCreate(VaultCreateOp {
        op_id: OpId::new("review-note"),
        path: VaultPath::new("review/primary-note.md"),
        after_hash: ContentHash::new("review"),
        body: note_body.to_string(),
        reason: "review comparison (synthetic; not applied)".to_string(),
        originating_record: RecordId::new("review"),
    }));
    plan
}

fn scalar(value: &serde_yaml::Value, key: &str) -> Option<String> {
    scalar_value(value.get(key)?)
}

fn scalar_value(item: &serde_yaml::Value) -> Option<String> {
    match item {
        serde_yaml::Value::String(s) => Some(s.clone()),
        serde_yaml::Value::Bool(b) => Some(b.to_string()),
        serde_yaml::Value::Number(n) => Some(n.to_string()),
        _ => None,
    }
}

fn list(value: &serde_yaml::Value, key: &str) -> Vec<String> {
    match value.get(key) {
        Some(serde_yaml::Value::Sequence(seq)) => seq.iter().filter_map(scalar_value).collect(),
        _ => Vec::new(),
    }
}

fn read(path: &Path) -> Option<String> {
    std::fs::read_to_string(path).ok()
}

/// Split a note into `(frontmatter_yaml, body)`. Frontmatter is the block
/// between a leading `---` line and the next `---` line. No leading fence →
/// `(None, whole_note)`.
fn split_frontmatter(note: &str) -> (Option<String>, String) {
    let mut lines = note.lines();
    if lines.next().map(str::trim_end) != Some("---") {
        return (None, note.to_string());
    }
    let mut fm: Vec<&str> = Vec::new();
    let mut body: Vec<&str> = Vec::new();
    let mut in_fm = true;
    for line in lines {
        if in_fm && line.trim_end() == "---" {
            in_fm = false;
            continue;
        }
        if in_fm {
            fm.push(line);
        } else {
            body.push(line);
        }
    }
    if in_fm {
        // Opening fence never closed → treat as no frontmatter.
        return (None, note.to_string());
    }
    (Some(fm.join("\n")), body.join("\n"))
}

/// A line-level diff (LCS). Lines are prefixed `  ` (common), `- ` (only in
/// `old`/expected), `+ ` (only in `new`/actual). Returns `(text, changed)`.
/// Falls back to a size summary on very large inputs to keep the O(n·m) table
/// bounded.
fn line_diff(old: &str, new: &str) -> (String, bool) {
    let a: Vec<&str> = old.lines().collect();
    let b: Vec<&str> = new.lines().collect();

    const MAX_CELLS: usize = 4_000_000;
    if a.len().saturating_mul(b.len()) > MAX_CELLS {
        let changed = a != b;
        let note = format!(
            "(diff suppressed: inputs too large — expected {} lines, actual {} lines; {})\n",
            a.len(),
            b.len(),
            if changed { "they differ" } else { "identical" }
        );
        return (note, changed);
    }

    let n = a.len();
    let m = b.len();
    // dp[i][j] = LCS length of a[i..] and b[j..].
    let mut dp = vec![vec![0u32; m + 1]; n + 1];
    for i in (0..n).rev() {
        for j in (0..m).rev() {
            dp[i][j] = if a[i] == b[j] {
                dp[i + 1][j + 1] + 1
            } else {
                dp[i + 1][j].max(dp[i][j + 1])
            };
        }
    }

    let mut out = String::new();
    let mut changed = false;
    let (mut i, mut j) = (0usize, 0usize);
    while i < n && j < m {
        if a[i] == b[j] {
            out.push_str("  ");
            out.push_str(a[i]);
            out.push('\n');
            i += 1;
            j += 1;
        } else if dp[i + 1][j] >= dp[i][j + 1] {
            out.push_str("- ");
            out.push_str(a[i]);
            out.push('\n');
            i += 1;
            changed = true;
        } else {
            out.push_str("+ ");
            out.push_str(b[j]);
            out.push('\n');
            j += 1;
            changed = true;
        }
    }
    while i < n {
        out.push_str("- ");
        out.push_str(a[i]);
        out.push('\n');
        i += 1;
        changed = true;
    }
    while j < m {
        out.push_str("+ ");
        out.push_str(b[j]);
        out.push('\n');
        j += 1;
        changed = true;
    }
    if out.is_empty() {
        out.push_str("(no differences)\n");
    }
    (out, changed)
}

/// All files under `dir`, vault-relative, sorted, `/`-separated.
fn walk_relative(dir: &Path) -> Vec<String> {
    let mut out = Vec::new();
    walk_into(dir, dir, &mut out);
    out.sort();
    out
}

fn walk_into(root: &Path, dir: &Path, out: &mut Vec<String>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(e) => e,
        Err(_) => return,
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            walk_into(root, &path, out);
        } else if let Ok(rel) = path.strip_prefix(root) {
            out.push(rel.to_string_lossy().replace('\\', "/"));
        }
    }
}

fn render_summary_md(summary: &ComparisonSummary) -> String {
    let mut s = String::new();
    s.push_str("# Comparison summary\n\n");
    s.push_str("Formatting differences are informational and do NOT fail the review. ");
    s.push_str("The authoritative content verdict is the contract engine (below), when present.\n\n");

    s.push_str(&format!("## Expected files ({})\n", summary.expected_files.len()));
    for f in &summary.expected_files {
        s.push_str(&format!("- `{f}`\n"));
    }
    s.push_str(&format!("\n## Actual files ({})\n", summary.actual_files.len()));
    if summary.actual_files.is_empty() {
        s.push_str("- (no produced note discovered)\n");
    }
    for f in &summary.actual_files {
        s.push_str(&format!("- `{f}`\n"));
    }

    s.push_str("\n## Diffs\n");
    s.push_str(&format!(
        "- frontmatter: {}\n",
        if summary.frontmatter_changed { "differs (see frontmatter.diff)" } else { "identical" }
    ));
    s.push_str(&format!(
        "- interpretation: {}\n",
        if summary.interpretation_changed { "differs (see interpretation.diff)" } else { "identical" }
    ));

    s.push_str("\n## Contract verdict\n");
    match &summary.contract {
        None => s.push_str("- (no `contract.yaml`, or no produced note to check)\n"),
        Some(c) => {
            s.push_str(&format!(
                "- MUST: {} passed, {} failed → **{}**\n",
                c.must_passed,
                c.must_failed,
                if c.must_clean { "CLEAN" } else { "FAILED" }
            ));
            s.push_str(&format!(
                "- SHOULD: {} passed, {} failed\n",
                c.should_passed, c.should_failed
            ));
            s.push_str(&format!("- skipped (may-break / unimplemented): {}\n", c.skipped));
            if !c.failures.is_empty() {
                s.push_str("\n### Failures\n");
                for f in &c.failures {
                    s.push_str(&format!("- {f}\n"));
                }
            }
        }
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn split_frontmatter_extracts_block_and_body() {
        let note = "---\ntitle: X\ntags: [a]\n---\n# Heading\n\nbody text\n";
        let (fm, body) = split_frontmatter(note);
        assert_eq!(fm.as_deref(), Some("title: X\ntags: [a]"));
        assert!(body.contains("# Heading"));
        assert!(body.contains("body text"));
    }

    #[test]
    fn split_frontmatter_handles_no_fence() {
        let note = "# Just a heading\nno frontmatter\n";
        let (fm, body) = split_frontmatter(note);
        assert!(fm.is_none());
        assert_eq!(body, note);
    }

    #[test]
    fn line_diff_marks_changes_and_commonality() {
        let (text, changed) = line_diff("a\nb\nc\n", "a\nB\nc\n");
        assert!(changed);
        assert!(text.contains("  a"));
        assert!(text.contains("- b"));
        assert!(text.contains("+ B"));
        assert!(text.contains("  c"));
    }

    #[test]
    fn line_diff_identical_is_unchanged() {
        let (_text, changed) = line_diff("x\ny\n", "x\ny\n");
        assert!(!changed);
    }

    #[test]
    fn frontmatter_coerces_numeric_list_items_to_strings() {
        // concept_candidates legitimately contains bare numbers (e.g. `8020`)
        // which YAML parses as integers; they must coerce to strings, not crash.
        let fm = "title: T\nconcept_candidates:\n  - 8020\n  - agent\n";
        let parsed = ReviewedFrontmatter::from_yaml(fm).unwrap();
        assert_eq!(parsed.concept_candidates, vec!["8020".to_string(), "agent".to_string()]);
    }
}
