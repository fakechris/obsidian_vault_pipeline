//! Writes the review pack — the harness's only output. Pure rendering +
//! file-copying from the report and the produced vault; mutates nothing else.
//!
//! ```text
//! <out_dir>/
//!   REVIEW.md              top-level human entry point
//!   input.md               the input that was processed
//!   processor-chain.txt    node ids + kinds + edges + topological order
//!   run-report.json        the L4 RunCycleReport (or a {ran:false,error} stub)
//!   apply-summary.txt       records seen/forwarded/dropped, op counts, derived
//!   files-written.txt      every file under vault_root + canonical_root
//!   generated/
//!     primary-note.md      the produced interpretation (if discovered)
//!     moc.md               the rebuilt MOC (if present)
//!     knowledge-index.json the rebuilt index (if present)
//!   canonical/summary.json concept count + slugs + evergreen paths
//!   lint.json / lint.txt   L5 health findings
//!   query-stats.json / .txt L5 read-model stats
//!   rag-context.json / .txt L6 retrieval (only with --rag-query)
//!   comparison/            (only with --expected-dir)
//!     summary.md
//!     frontmatter.diff
//!     interpretation.diff
//! ```

use ovp_domain::{KnowledgeIndexBuilder, MocBuilder};

use crate::compare::Comparison;
use crate::{ensure_dir, write_file, ReviewError, ReviewReport, ReviewRunConfig};

pub fn write(
    config: &ReviewRunConfig,
    report: &ReviewReport,
    comparison: Option<&Comparison>,
) -> Result<(), ReviewError> {
    let out = &config.out_dir;

    // input.md — copy what was processed (note absence rather than failing).
    match std::fs::read_to_string(&config.input_path) {
        Ok(c) => write_file(&out.join("input.md"), &c)?,
        Err(e) => write_file(
            &out.join("input.md"),
            &format!("(input `{}` not readable: {e})\n", config.input_path.display()),
        )?,
    }

    // processor-chain.txt
    let chain_txt = match &report.chain {
        Some(chain) => chain.render_text(),
        None => format!(
            "(processor chain unavailable: {})\n",
            report.chain_error.as_deref().unwrap_or("unknown")
        ),
    };
    write_file(&out.join("processor-chain.txt"), &chain_txt)?;

    // run-report.json
    let run_json = match &report.run {
        Some(r) => serde_json::to_string_pretty(r).map_err(io)?,
        None => {
            let reason = report.run_error.as_deref().or(report.chain_error.as_deref());
            serde_json::to_string_pretty(&serde_json::json!({ "ran": false, "error": reason }))
                .map_err(io)?
        }
    };
    write_file(&out.join("run-report.json"), &run_json)?;

    // apply-summary.txt
    write_file(&out.join("apply-summary.txt"), &render_apply_summary(report))?;

    // files-written.txt
    write_file(&out.join("files-written.txt"), &render_files(report))?;

    // generated/ — copy the produced artifacts when they exist on disk.
    let generated = out.join("generated");
    ensure_dir(&generated)?;
    if let Some(rel) = &report.primary_note {
        if let Ok(c) = std::fs::read_to_string(config.vault_root.join(rel)) {
            write_file(&generated.join("primary-note.md"), &c)?;
        }
    }
    let moc_rel = MocBuilder::new().moc_path().as_str().to_string();
    if let Ok(c) = std::fs::read_to_string(config.vault_root.join(&moc_rel)) {
        write_file(&generated.join("moc.md"), &c)?;
    }
    let index_rel = KnowledgeIndexBuilder::new().index_path().as_str().to_string();
    if let Ok(c) = std::fs::read_to_string(config.vault_root.join(&index_rel)) {
        write_file(&generated.join("knowledge-index.json"), &c)?;
    }

    // canonical/summary.json
    write_file(
        &out.join("canonical").join("summary.json"),
        &serde_json::to_string_pretty(&report.canonical).map_err(io)?,
    )?;

    // lint.json / lint.txt
    write_file(&out.join("lint.json"), &serde_json::to_string_pretty(&report.lint).map_err(io)?)?;
    write_file(&out.join("lint.txt"), &render_lint(report))?;

    // query-stats.json / query-stats.txt
    write_file(
        &out.join("query-stats.json"),
        &serde_json::to_string_pretty(&report.query_stats).map_err(io)?,
    )?;
    write_file(&out.join("query-stats.txt"), &render_query(report))?;

    // rag-context.json / rag-context.txt — only when a query was supplied.
    if let Some(rag) = &report.rag {
        write_file(&out.join("rag-context.json"), &serde_json::to_string_pretty(rag).map_err(io)?)?;
        write_file(&out.join("rag-context.txt"), &render_rag(report))?;
    }

    // comparison/ — only when --expected-dir was supplied.
    if let Some(c) = comparison {
        let dir = out.join("comparison");
        ensure_dir(&dir)?;
        write_file(&dir.join("summary.md"), &c.summary_md)?;
        write_file(&dir.join("frontmatter.diff"), &c.frontmatter_diff)?;
        write_file(&dir.join("interpretation.diff"), &c.interpretation_diff)?;
    }

    // REVIEW.md — written last; the top-level entry point.
    write_file(&out.join("REVIEW.md"), &render_review_md(report))?;
    Ok(())
}

fn io<E: std::fmt::Display>(e: E) -> ReviewError {
    ReviewError::Io(e.to_string())
}

fn render_apply_summary(report: &ReviewReport) -> String {
    let mut s = String::new();
    match &report.run {
        None => {
            s.push_str("cycle did not run.\n");
            if let Some(reason) = report.run_error.as_deref().or(report.chain_error.as_deref()) {
                s.push_str(&format!("reason: {reason}\n"));
            }
            return s;
        }
        Some(r) => {
            if r.dry_run {
                s.push_str("mode:              dry-run (nothing written)\n");
            }
            s.push_str(&format!("records_seen:      {}\n", r.records_seen));
            s.push_str(&format!("records_forwarded: {}\n", r.records_forwarded_to_sinks));
            s.push_str(&format!("records_dropped:   {}\n", r.records_dropped));
            s.push_str(&format!("plan ops:          {}\n", r.ops_emitted));
            let a = r.apply.counts();
            s.push_str(&format!(
                "main apply:        applied={} skipped={} failed={} unsupported={}\n",
                a.applied, a.skipped, a.failed, a.unsupported
            ));
            match &r.moc {
                Some(m) => s.push_str(&format!(
                    "moc:               applied={} skipped={} failed={} unsupported={}\n",
                    m.applied, m.skipped, m.failed, m.unsupported
                )),
                None => s.push_str("moc:               (skipped)\n"),
            }
            match &r.knowledge_index {
                Some(k) => s.push_str(&format!(
                    "knowledge_index:   applied={} skipped={} failed={} unsupported={}\n",
                    k.applied, k.skipped, k.failed, k.unsupported
                )),
                None => s.push_str("knowledge_index:   (skipped)\n"),
            }
            if let Some(reason) = &r.derived_skipped_reason {
                s.push_str(&format!("derived skipped:   {reason}\n"));
            }
            s.push_str(&format!("succeeded:         {}\n", r.succeeded()));
        }
    }
    s
}

fn render_files(report: &ReviewReport) -> String {
    let mut s = String::new();
    s.push_str(&format!("vault_root files ({}):\n", report.files.vault.len()));
    if report.files.vault.is_empty() {
        s.push_str("  (none)\n");
    }
    for f in &report.files.vault {
        s.push_str(&format!("  {f}\n"));
    }
    s.push_str(&format!("\ncanonical_root files ({}):\n", report.files.canonical.len()));
    if report.files.canonical.is_empty() {
        s.push_str("  (none)\n");
    }
    for f in &report.files.canonical {
        s.push_str(&format!("  {f}\n"));
    }
    s
}

fn render_lint(report: &ReviewReport) -> String {
    use ovp_lint::Severity;
    let report = &report.lint;
    let mut s = String::new();
    if report.findings.is_empty() {
        s.push_str("clean: no findings\n");
        return s;
    }
    for f in &report.findings {
        let loc = f.location.as_deref().unwrap_or("-");
        s.push_str(&format!("[{}] {}  {}  ({})\n", f.severity.as_str(), f.code, f.detail, loc));
    }
    s.push_str(&format!(
        "\n{} finding(s): {} error, {} warning, {} info\n",
        report.findings.len(),
        report.count(Severity::Error),
        report.count(Severity::Warning),
        report.count(Severity::Info),
    ));
    s
}

fn render_query(report: &ReviewReport) -> String {
    match &report.query_stats {
        Some(stats) => format!(
            "concepts:                   {}\n\
             index present:              {}\n\
             total backlinks:            {}\n\
             concepts without backlinks: {}\n",
            stats.concept_count,
            stats.index_present,
            stats.total_backlinks,
            stats.concepts_without_backlinks,
        ),
        None => format!(
            "(query stats unavailable: {})\n",
            report.query_error.as_deref().unwrap_or("unknown")
        ),
    }
}

fn render_rag(report: &ReviewReport) -> String {
    let Some(ctx) = &report.rag else {
        return "(no RAG context)\n".to_string();
    };
    let mut s = String::new();
    s.push_str(&format!("query: {}\n", ctx.query));
    if ctx.selected.is_empty() {
        s.push_str("(no matching concepts)\n");
        return s;
    }
    for (i, c) in ctx.selected.iter().enumerate() {
        s.push_str(&format!("\n{}. {}  [{}]  score={}\n", i + 1, c.title, c.slug, c.score));
        s.push_str(&format!("   note: {}\n", c.evergreen_path));
        if let Some(snippet) = &c.snippet {
            s.push_str(&format!("   snippet: {snippet}\n"));
        }
        if !c.backlinks.is_empty() {
            s.push_str(&format!("   backlinks: {}\n", c.backlinks.join(", ")));
        }
    }
    s
}

fn render_review_md(report: &ReviewReport) -> String {
    let mut s = String::new();
    s.push_str("# Review pack\n\n");

    // Verdicts: the L4 cycle, the contract comparison, and the overall review
    // gate (which the CLI exit code follows).
    let cycle = if report.cycle_succeeded() { "✅ SUCCEEDED" } else { "❌ FAILED" };
    let contract = match report.contract_clean() {
        Some(true) => "✅ CLEAN".to_string(),
        Some(false) => "❌ FAILED".to_string(),
        None => "— (no expected-dir contract)".to_string(),
    };
    let review = if report.review_passed() {
        "✅ PASSED".to_string()
    } else {
        format!("❌ FAILED — {}", report.failure_reason().unwrap_or_default())
    };
    s.push_str(&format!("- **Cycle (L4):** {cycle}\n"));
    s.push_str(&format!("- **Contract:** {contract}\n"));
    s.push_str(&format!("- **Review:** {review}\n\n"));

    s.push_str(&format!("- **Input:** `{}`\n", report.input_path));
    s.push_str(&format!("- **Manifest:** `{}`\n", report.manifest_path));
    s.push_str(&format!("- **Run id:** `{}`\n", report.run_id));
    s.push_str(&format!("- **Output:** `{}`\n", report.out_dir));
    if let Some(note) = &report.primary_note {
        s.push_str(&format!("- **Primary note:** `{note}`\n"));
    }

    // Processor chain.
    s.push_str("\n## Processor chain\n\n");
    match &report.chain {
        Some(chain) => {
            s.push_str("```\n");
            s.push_str(&chain.render_text());
            s.push_str("```\n");
        }
        None => s.push_str(&format!(
            "_unavailable: {}_\n",
            report.chain_error.as_deref().unwrap_or("unknown")
        )),
    }

    // Apply summary.
    s.push_str("\n## Apply summary\n\n```\n");
    s.push_str(&render_apply_summary(report));
    s.push_str("```\n");

    // Files written.
    s.push_str("\n## Files written\n\n```\n");
    s.push_str(&render_files(report));
    s.push_str("```\n");

    // Canonical.
    s.push_str("\n## Canonical store\n\n");
    s.push_str(&format!("- concepts: **{}**\n", report.canonical.concept_count));
    for slug in &report.canonical.slugs {
        s.push_str(&format!("  - `{slug}`\n"));
    }

    // Lint.
    s.push_str("\n## Lint\n\n```\n");
    s.push_str(&render_lint(report));
    s.push_str("```\n");

    // Query stats.
    s.push_str("\n## Query stats\n\n```\n");
    s.push_str(&render_query(report));
    s.push_str("```\n");

    // RAG preview.
    if report.rag.is_some() {
        s.push_str("\n## RAG preview\n\n```\n");
        s.push_str(&render_rag(report));
        s.push_str("```\n");
    } else if let Some(err) = &report.rag_error {
        s.push_str(&format!("\n## RAG preview\n\n_unavailable: {err}_\n"));
    }

    // Comparison.
    if let Some(cmp) = &report.comparison {
        s.push_str("\n## Comparison vs expected\n\n");
        s.push_str(&format!("- expected files: {}\n", cmp.expected_files.len()));
        s.push_str(&format!("- actual files: {}\n", cmp.actual_files.len()));
        s.push_str(&format!(
            "- frontmatter: {}\n",
            if cmp.frontmatter_changed { "differs (see comparison/frontmatter.diff)" } else { "identical" }
        ));
        s.push_str(&format!(
            "- interpretation: {}\n",
            if cmp.interpretation_changed { "differs (see comparison/interpretation.diff)" } else { "identical" }
        ));
        match &cmp.contract {
            Some(c) => {
                s.push_str(&format!(
                    "- contract MUST: {} passed, {} failed → **{}**\n",
                    c.must_passed,
                    c.must_failed,
                    if c.must_clean { "CLEAN" } else { "FAILED" }
                ));
                s.push_str(&format!(
                    "- contract SHOULD: {} passed, {} failed\n",
                    c.should_passed, c.should_failed
                ));
            }
            None => s.push_str("- contract: (no contract.yaml or no produced note)\n"),
        }
        s.push_str("\nSee `comparison/summary.md` for detail.\n");
    }

    s.push_str(
        "\n---\n_Formatting drift is informational. **Review** = cycle succeeded AND \
         (no expected contract OR contract MUST-clean); it is what the CLI exit code follows._\n",
    );
    s
}
