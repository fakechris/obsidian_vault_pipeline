//! Writes the comparison pack — the comparator's only output beyond the M7
//! review pack (which `ReviewRun` already wrote into `ovp/review-pack/`). Pure
//! rendering from the [`Comparison`] + the two normalized subjects + the raw
//! Nowledge captures; mutates nothing else.
//!
//! ```text
//! <out>/
//!   REVIEW.md                       top-level human entry point
//!   input.md | input-url.txt        what was compared
//!   grounding-reference.txt         the text claims were grounded against
//!   ovp/
//!     review-pack/                  (the full M7 pack, written by ReviewRun)
//!     normalized.json
//!   nowledge/
//!     source-detail.json            source + per-source memories (parsed capture)
//!     mem-search.json               raw /memories/search results
//!     normalized.json
//!   comparison/
//!     summary.md
//!     concept-overlap.md
//!     claim-diff.md
//!     grounding-audit.md
//!     retrieval-comparison.md
//!     score.json                    the full structured Comparison
//! ```

use crate::compare::Comparison;
use crate::nowledge::{MemorySearchResult, SourceDetail};
use crate::normalize::NormalizedSubject;
use crate::{write_file, CompareConfig, CompareError};

pub(crate) struct PackInputs<'a> {
    pub config: &'a CompareConfig,
    pub comparison: &'a Comparison,
    pub input_mode: &'a str,
    pub ovp_subject: Option<&'a NormalizedSubject>,
    pub nowledge_subject: Option<&'a NormalizedSubject>,
    pub nowledge_detail: Option<&'a SourceDetail>,
    /// Raw whole-store /memories/search results (the background lane).
    pub nowledge_global_raw: &'a [MemorySearchResult],
    /// The materialized shared artifact text, if `--materialize-from-nowledge`.
    pub materialized_text: Option<&'a str>,
    pub markdown_text: Option<&'a str>,
    pub reference: &'a str,
}

pub(crate) fn write(p: PackInputs<'_>) -> Result<(), CompareError> {
    let out = &p.config.out_dir;

    // Input + grounding reference.
    match (p.markdown_text, &p.config.url) {
        (Some(md), _) => write_file(&out.join("input.md"), md)?,
        (None, Some(url)) => write_file(&out.join("input-url.txt"), &format!("{url}\n"))?,
        (None, None) => write_file(&out.join("input-url.txt"), "(no input provided)\n")?,
    }
    write_file(&out.join("grounding-reference.txt"), p.reference)?;
    if let Some(m) = p.materialized_text {
        write_file(&out.join("materialized-input.md"), m)?;
    }

    // Normalized subjects.
    if let Some(s) = p.ovp_subject {
        write_file(&out.join("ovp").join("normalized.json"), &to_json(s)?)?;
    }
    if let Some(s) = p.nowledge_subject {
        write_file(&out.join("nowledge").join("normalized.json"), &to_json(s)?)?;
    }

    // Raw Nowledge captures (parsed subset the comparator actually saw).
    if let Some(d) = p.nowledge_detail {
        write_file(&out.join("nowledge").join("source-detail.json"), &to_json(d)?)?;
    }
    write_file(&out.join("nowledge").join("mem-search-global.json"), &to_json(&p.nowledge_global_raw)?)?;

    // score.json — the whole structured Comparison (counts + labeled metrics).
    write_file(&out.join("comparison").join("score.json"), &to_json(p.comparison)?)?;

    // Per-dimension human renderings.
    write_file(&out.join("comparison").join("concept-overlap.md"), &render_concepts(p.comparison))?;
    write_file(&out.join("comparison").join("claim-diff.md"), &render_claims(p.comparison))?;
    write_file(&out.join("comparison").join("grounding-audit.md"), &render_grounding(p.comparison))?;
    write_file(
        &out.join("comparison").join("retrieval-comparison.md"),
        &render_retrieval(p.comparison),
    )?;
    write_file(&out.join("comparison").join("summary.md"), &render_summary(p.comparison))?;

    // REVIEW.md last — the entry point.
    write_file(&out.join("REVIEW.md"), &render_review(&p))?;
    Ok(())
}

fn to_json<T: serde::Serialize>(v: &T) -> Result<String, CompareError> {
    serde_json::to_string_pretty(v).map_err(|e| CompareError::Io(format!("serializing: {e}")))
}

fn status_line(c: &Comparison) -> String {
    let side = |s: &crate::compare::SideStatus| {
        if s.available {
            "available".to_string()
        } else {
            format!("UNAVAILABLE — {}", s.detail.clone().unwrap_or_default())
        }
    };
    format!("ovp2: {} | nowledge-mem: {}", side(&c.ovp), side(&c.nowledge))
}

fn render_review(p: &PackInputs<'_>) -> String {
    let c = p.comparison;
    let mut s = String::new();
    s.push_str(&format!("# External E2E comparison — `{}`\n\n", c.case_id));
    s.push_str("> ovp2 vs **Nowledge Mem** (external reference system). All cross-system\n");
    s.push_str("> metrics are LEXICAL — they flag things to inspect, not semantic verdicts.\n\n");
    s.push_str(&format!("**Sides:** {}\n\n", status_line(c)));
    s.push_str(&format!("**Input mode:** {}\n\n", p.input_mode));

    if let Some(md) = &p.config.markdown_input {
        s.push_str(&format!("- **Input (markdown):** `{}`\n", md.display()));
    }
    if let Some(url) = &p.config.url {
        s.push_str(&format!("- **Input (url):** {url}\n"));
    }
    if p.materialized_text.is_some() {
        s.push_str("- **Materialized shared artifact:** `materialized-input.md` (both sides analyzed this)\n");
    }
    let ref_src = c.grounding.as_ref().map(|g| g.reference_source.as_str()).unwrap_or("—");
    s.push_str(&format!(
        "- **Grounding reference:** {} bytes — {ref_src} (`grounding-reference.txt`)\n",
        p.reference.len()
    ));
    s.push_str("- **ovp review pack:** `ovp/review-pack/REVIEW.md`\n");

    s.push_str("\n## Findings\n\n");
    if c.findings.is_empty() {
        s.push_str("- (none)\n");
    }
    for f in &c.findings {
        s.push_str(&format!("- {f}\n"));
    }

    s.push_str("\n## Dimensions\n\n");
    s.push_str("| dimension | file |\n|---|---|\n");
    s.push_str("| concept overlap | `comparison/concept-overlap.md` |\n");
    s.push_str("| claim diff | `comparison/claim-diff.md` |\n");
    s.push_str("| grounding audit | `comparison/grounding-audit.md` |\n");
    s.push_str("| retrieval comparison | `comparison/retrieval-comparison.md` |\n");
    s.push_str("| full structured score | `comparison/score.json` |\n");

    // Compact headline numbers.
    s.push_str("\n## At a glance\n\n");
    if let Some(co) = &c.concept_overlap {
        s.push_str(&format!(
            "- concepts: {} ovp / {} nowledge, {} shared, {} ovp-only, {} nowledge-only (lexical Jaccard {:.3})\n",
            co.ovp_count,
            co.nowledge_count,
            co.shared_count,
            co.ovp_only_count,
            co.nowledge_only_count,
            co.jaccard_lexical
        ));
    }
    if let Some(g) = &c.grounding {
        s.push_str(&format!(
            "- grounding: ovp {}/{} grounded ({:.2}); nowledge {}/{} grounded ({:.2})\n",
            g.ovp_grounded,
            g.ovp_grounded + g.ovp_ungrounded,
            g.ovp_rate,
            g.nowledge_grounded,
            g.nowledge_grounded + g.nowledge_ungrounded,
            g.nowledge_rate
        ));
    }
    if let Some(st) = &c.structure {
        let crystals = match st.nowledge_global_crystals {
            Some(n) => format!("{n} (global, NOT this input)"),
            None => format!("unavailable ({})", st.crystal_status),
        };
        s.push_str(&format!(
            "- structure: ovp {} concepts / {} claims; nowledge {} memories / {} memory-titles; crystals {crystals}\n",
            st.ovp_concepts, st.ovp_claims, st.nowledge_memories, st.nowledge_memory_titles
        ));
    }

    s.push_str("\n---\n_Observational comparison: the two systems extract different unit types and use different retrieval models; this pack shows where they diverge, it does not declare a winner._\n");
    s
}

fn render_summary(c: &Comparison) -> String {
    let mut s = String::new();
    s.push_str(&format!("# Comparison summary — `{}`\n\n", c.case_id));
    s.push_str(&format!("**Sides:** {}\n\n", status_line(c)));
    s.push_str("## Findings\n\n");
    if c.findings.is_empty() {
        s.push_str("- (none)\n");
    }
    for f in &c.findings {
        s.push_str(&format!("- {f}\n"));
    }
    s.push_str("\nSee the per-dimension files for detail; `score.json` holds the full structured result.\n");
    s
}

fn render_concepts(c: &Comparison) -> String {
    let mut s = String::new();
    s.push_str("# Concept overlap\n\n");
    match &c.concept_overlap {
        None => s.push_str("_unavailable: both sides are required._\n"),
        Some(co) => {
            s.push_str(&format!("- metric: **{}**\n", co.metric));
            s.push_str(&format!("- normalization: `{}`\n", co.normalization));
            s.push_str(&format!("- ovp concepts: {}\n", co.ovp_count));
            s.push_str(&format!("- nowledge concepts: {}\n", co.nowledge_count));
            s.push_str(&format!("- shared: {}\n", co.shared_count));
            s.push_str(&format!("- ovp-only: **{}** (exact)\n", co.ovp_only_count));
            s.push_str(&format!("- nowledge-only: **{}** (exact)\n", co.nowledge_only_count));
            s.push_str(&format!("- lexical Jaccard: {:.3}\n\n", co.jaccard_lexical));
            list_section(&mut s, "Shared", &co.shared);
            list_section(
                &mut s,
                &cap_title("ovp-only", co.ovp_only_count, co.ovp_only_truncated),
                &co.ovp_only,
            );
            list_section(
                &mut s,
                &cap_title("nowledge-only", co.nowledge_only_count, co.nowledge_only_truncated),
                &co.nowledge_only,
            );
        }
    }
    s
}

fn render_claims(c: &Comparison) -> String {
    let mut s = String::new();
    s.push_str("# Claim diff\n\n");
    match &c.claim_diff {
        None => s.push_str("_unavailable: both sides are required._\n"),
        Some(cd) => {
            s.push_str(&format!("- metric: **{}**\n", cd.metric));
            s.push_str(&format!("- ovp claims: {}\n", cd.ovp_claim_count));
            s.push_str(&format!("- nowledge claims: {}\n", cd.nowledge_claim_count));
            s.push_str(&format!("- lexically overlapping ovp claims (≥3 shared tokens): {}\n", cd.lexically_overlapping_claims));
            s.push_str(&format!("- LLM judge: {}\n\n", cd.llm_judge));
            s.push_str("## ovp claims by section\n\n");
            for (k, v) in &cd.ovp_by_section {
                s.push_str(&format!("- {k}: {v}\n"));
            }
            s.push_str("\n## nowledge claims by section (unit_type)\n\n");
            for (k, v) in &cd.nowledge_by_section {
                s.push_str(&format!("- {k}: {v}\n"));
            }
        }
    }
    s
}

fn render_grounding(c: &Comparison) -> String {
    let mut s = String::new();
    s.push_str("# Grounding audit\n\n");
    match &c.grounding {
        None => s.push_str("_unavailable._\n"),
        Some(g) => {
            s.push_str(&format!("- metric: **{}**\n", g.metric));
            s.push_str(&format!("- threshold: {}\n", g.threshold));
            s.push_str(&format!("- reference (single, shared): {}\n", g.reference_source));
            s.push_str(&format!("- ⚠️ {}\n\n", g.warning));
            s.push_str(&format!(
                "- ovp: {} grounded / {} ungrounded (rate {:.2})\n",
                g.ovp_grounded, g.ovp_ungrounded, g.ovp_rate
            ));
            s.push_str(&format!(
                "- nowledge: {} grounded / {} ungrounded (rate {:.2})\n\n",
                g.nowledge_grounded, g.nowledge_ungrounded, g.nowledge_rate
            ));
            list_section(&mut s, "ovp ungrounded examples", &g.ovp_ungrounded_examples);
            list_section(&mut s, "nowledge ungrounded examples", &g.nowledge_ungrounded_examples);
        }
    }
    s
}

fn render_retrieval(c: &Comparison) -> String {
    let r = &c.retrieval;
    let mut s = String::new();
    s.push_str("# Retrieval comparison\n\n");
    s.push_str(&format!("- metric: **{}**\n", r.metric));
    s.push_str(&format!("- comparable lane: {}\n", r.comparable_lane));
    s.push_str(&format!("- background lane: {}\n", r.background_lane));
    s.push_str(&format!("- ovp status: {}\n", r.ovp_status));
    s.push_str(&format!("- nowledge scoped status: {}\n", r.nowledge_scoped_status));
    s.push_str(&format!("- nowledge global status: {}\n\n", r.nowledge_global_status));
    for row in &r.rows {
        s.push_str(&format!("## query: {}\n\n", row.query));
        s.push_str("**Comparable lane (both over THIS input):**\n\n");
        s.push_str(&format!(
            "- ovp-rag: {} hits ({} grounded) — top: {}\n",
            row.ovp_hits,
            row.ovp_grounded,
            join_or_dash(&row.ovp_top)
        ));
        s.push_str(&format!(
            "- nowledge (this source's memories): {} hits ({} grounded) — top: {}\n\n",
            row.nowledge_scoped_hits,
            row.nowledge_scoped_grounded,
            join_or_dash(&row.nowledge_scoped_top)
        ));
        s.push_str("**Background lane (Nowledge whole store — context only, NOT comparable):**\n\n");
        s.push_str(&format!(
            "- nowledge global: {} hits — top: {}\n\n",
            row.nowledge_global_hits,
            join_or_dash(&row.nowledge_global_top)
        ));
    }
    s
}

fn cap_title(label: &str, exact: usize, truncated: bool) -> String {
    if truncated {
        format!("{label} (showing first {TOP_N_SHOWN} of {exact})")
    } else {
        format!("{label} ({exact})")
    }
}

const TOP_N_SHOWN: usize = 25;

fn list_section(s: &mut String, title: &str, items: &[String]) {
    s.push_str(&format!("## {title} ({})\n\n", items.len()));
    if items.is_empty() {
        s.push_str("- (none)\n\n");
        return;
    }
    for i in items {
        s.push_str(&format!("- {i}\n"));
    }
    s.push('\n');
}

fn join_or_dash(items: &[String]) -> String {
    if items.is_empty() {
        "—".to_string()
    } else {
        items.join("; ")
    }
}
