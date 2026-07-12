//! Ops dashboard pages: /ops, /audit, /candidates.
//! Pure HTML rendering from IndexModel (same as the main console — no JS,
//! no runtime, rebuildable from index.json).

use ovp_index::{ClaimStatus, DAYS_STUCK_AMBER, DAYS_STUCK_RED, IndexModel, SourceStatus};

use crate::{claim_status_label, source_status_label};

const CSS: &str = include_str!("ops.css");

pub fn render_ops_page(model: &IndexModel) -> String {
    let mut page = String::with_capacity(32 * 1024);
    page.push_str(&ops_header(model));
    page.push_str(&health_section(model));
    page.push_str(&queue_section(model));
    page.push_str(&blocked_section(model));
    page.push_str(&stuck_section(model));
    page.push_str(&recent_failures_section(model));
    page.push_str(&run_stats_section(model));
    page.push_str("</main></body></html>\n");
    page
}

/// Aging label + CSS class for a `days_stuck` value: fresh / amber (warn) /
/// red (chronic). Returns `("", "")` when the age is unknown — the render then
/// shows a neutral dash rather than a misleading "0d".
fn aging(days: Option<usize>) -> (String, &'static str) {
    match days {
        None => (String::new(), ""),
        Some(d) if d >= DAYS_STUCK_RED => (format!("{d}d"), "aging-red"),
        Some(d) if d >= DAYS_STUCK_AMBER => (format!("{d}d"), "aging-amber"),
        Some(d) => (format!("{d}d"), "aging-ok"),
    }
}

pub fn render_audit_page(model: &IndexModel) -> String {
    let mut page = String::with_capacity(16 * 1024);
    page.push_str(&audit_header(model));
    page.push_str(&runs_timeline(model));
    page.push_str("</main></body></html>\n");
    page
}

pub fn render_candidates_page(model: &IndexModel) -> String {
    let mut page = String::with_capacity(16 * 1024);
    page.push_str(&candidates_header(model));
    page.push_str(&caveated_claims(model));
    page.push_str("</main></body></html>\n");
    page
}

fn ops_header(model: &IndexModel) -> String {
    format!(
        r##"<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OVP Ops · 运维</title>
<style>{CSS}</style></head>
<body><main>
<h1>OVP Ops <span class="zh">运维面板</span></h1>
<p class="sub">built {built} · <a href="index.html">← console</a> · <a href="audit.html">audit</a> · <a href="candidates.html">candidates</a></p>
"##,
        built = esc(model.built_at.as_deref().unwrap_or(&model.date)),
    )
}

fn health_section(model: &IndexModel) -> String {
    let t = &model.totals;
    let total = t.sources;
    let ok = t.processed + t.duplicates;
    let attention = t.failed + t.blocked + t.needs_content + t.unparseable;
    let health_pct = if total > 0 {
        (ok as f64 / total as f64) * 100.0
    } else {
        100.0
    };
    let class = if health_pct >= 90.0 {
        "ok"
    } else if health_pct >= 70.0 {
        "warn"
    } else {
        "bad"
    };
    format!(
        r#"<section><h2>Health Score <span class="zh">健康度</span></h2>
<div class="health-score {class}">{health_pct:.0}%</div>
<p>{ok} ok / {attention} need attention / {total} total sources</p>
</section>
"#,
    )
}

fn queue_section(model: &IndexModel) -> String {
    let depth = model.ops.queue_depth;
    let capped = model.ops.capped;
    // "Backlog not draining" note: a capped last run with a non-empty queue is
    // the visible signal the operator was blind to.
    let capped_note = if capped > 0 {
        format!(
            r#"<p class="warn">last run capped {capped} source(s) — backlog not draining at the current --max-sources</p>"#
        )
    } else {
        String::new()
    };
    format!(
        r#"<section><h2>Queue <span class="zh">待处理队列</span></h2>
<div class="metric">{depth}</div>
<p>sources waiting for reader processing</p>
{capped_note}</section>
"#,
    )
}

fn blocked_section(model: &IndexModel) -> String {
    if model.ops.blocked_sources.is_empty() {
        return r#"<section><h2>Blocked <span class="zh">阻塞来源</span></h2><p class="empty">No blocked sources. 无阻塞来源。</p></section>
"#
        .into();
    }
    let mut out = String::from(
        "<section><h2>Blocked <span class=\"zh\">阻塞来源</span></h2>\n<table><thead><tr><th>Source</th><th>Fails</th><th>Stuck</th><th>Last Reason</th><th>Last Attempt</th></tr></thead><tbody>\n",
    );
    for b in &model.ops.blocked_sources {
        let (age_label, age_class) = aging(b.days_stuck);
        let age_cell = if age_label.is_empty() {
            "<td>—</td>".to_string()
        } else {
            format!("<td class=\"{age_class}\">{age_label}</td>")
        };
        out.push_str(&format!(
            "<tr><td>{title}</td><td>{fails}</td>{age_cell}<td>{reason}</td><td>{date}</td></tr>\n",
            title = esc(b.title.as_deref().unwrap_or(&b.sha256[..8])),
            fails = b.fail_count,
            reason = esc(b.last_reason.as_deref().unwrap_or("—")),
            date = esc(b.last_attempt.as_deref().unwrap_or("—")),
        ));
    }
    out.push_str("</tbody></table></section>\n");
    out
}

fn stuck_section(model: &IndexModel) -> String {
    if model.ops.stuck_sources.is_empty() {
        return String::new();
    }
    let mut out = String::from(
        "<section><h2>Needs Content <span class=\"zh\">待补内容</span></h2>\n<table><thead><tr><th>Source</th><th>Stuck</th><th>First Seen</th></tr></thead><tbody>\n",
    );
    for s in &model.ops.stuck_sources {
        let (age_label, age_class) = aging(s.days_stuck);
        let age_cell = if age_label.is_empty() {
            "<td>—</td>".to_string()
        } else {
            format!("<td class=\"{age_class}\">{age_label}</td>")
        };
        out.push_str(&format!(
            "<tr><td>{title}</td>{age_cell}<td>{seen}</td></tr>\n",
            title = esc(s.title.as_deref().unwrap_or(&s.sha256[..8])),
            seen = esc(s.first_seen.as_deref().unwrap_or("—")),
        ));
    }
    out.push_str("</tbody></table></section>\n");
    out
}

fn recent_failures_section(model: &IndexModel) -> String {
    let failed: Vec<_> = model
        .sources
        .iter()
        .filter(|s| s.status == SourceStatus::Failed)
        .collect();
    if failed.is_empty() {
        return r#"<section><h2>Recent Failures <span class="zh">近期失败</span></h2><p class="empty">No recent failures. 无近期失败。</p></section>
"#
        .into();
    }
    let mut out = String::from(
        "<section><h2>Recent Failures <span class=\"zh\">近期失败</span></h2>\n<table><thead><tr><th>Source</th><th>Fails</th><th>Reason</th><th>Date</th></tr></thead><tbody>\n",
    );
    for s in failed.iter().take(20) {
        let (_, _, class) = source_status_label(s.status);
        out.push_str(&format!(
            "<tr class=\"lv-{class}\"><td>{title}</td><td>{fails}</td><td>{reason}</td><td>{date}</td></tr>\n",
            title = esc(s.title.as_deref().unwrap_or(&s.sha256[..8])),
            fails = s.fail_count,
            reason = esc(s.last_reason.as_deref().unwrap_or("—")),
            date = esc(s.date.as_deref().unwrap_or("—")),
        ));
    }
    out.push_str("</tbody></table></section>\n");
    out
}

fn run_stats_section(model: &IndexModel) -> String {
    let Some(stats) = &model.ops.run_stats else {
        return r#"<section><h2>Run Stats (30d) <span class="zh">运行统计</span></h2><p class="empty">No runs in window. 窗口内无运行记录。</p></section>
"#
        .into();
    };
    format!(
        r#"<section><h2>Run Stats (30d) <span class="zh">运行统计</span></h2>
<div class="stats-grid">
<div class="stat"><span class="label">Runs</span><span class="value">{runs}</span></div>
<div class="stat"><span class="label">Success Rate</span><span class="value">{rate:.1}%</span></div>
<div class="stat"><span class="label">Avg Processed/Run</span><span class="value">{avg:.1}</span></div>
<div class="stat"><span class="label">Total Succeeded</span><span class="value">{succ}</span></div>
<div class="stat"><span class="label">Total Failed</span><span class="value">{fail}</span></div>
</div>
</section>
"#,
        runs = stats.total_runs,
        rate = stats.success_rate_pct,
        avg = stats.avg_processed_per_run,
        succ = stats.succeeded,
        fail = stats.failed,
    )
}

fn audit_header(model: &IndexModel) -> String {
    format!(
        r##"<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OVP Audit · 审计</title>
<style>{CSS}</style></head>
<body><main>
<h1>OVP Audit <span class="zh">审计时间线</span></h1>
<p class="sub">built {built} · <a href="index.html">← console</a> · <a href="ops.html">ops</a> · <a href="candidates.html">candidates</a></p>
"##,
        built = esc(model.built_at.as_deref().unwrap_or(&model.date)),
    )
}

fn runs_timeline(model: &IndexModel) -> String {
    if model.runs.is_empty() {
        return "<section><p class=\"empty\">No runs recorded. 无运行记录。</p></section>\n".into();
    }
    let mut out = String::from(
        "<section><h2>Run Timeline <span class=\"zh\">运行时间线</span></h2>\n<table><thead><tr><th>Date</th><th>Run ID</th><th>✓</th><th>✗</th><th>⊘</th><th>⊗</th><th>Ingested</th><th>Pinboard</th></tr></thead><tbody>\n",
    );
    for r in model.runs.iter().rev() {
        out.push_str(&format!(
            "<tr><td>{date}</td><td><code>{run_id}</code></td><td class=\"ok\">{succ}</td><td class=\"bad\">{fail}</td><td>{skip}</td><td>{block}</td><td>{ing}</td><td>{pin}</td></tr>\n",
            date = esc(&r.date),
            run_id = esc(&r.run_id),
            succ = r.succeeded,
            fail = r.failed,
            skip = r.skipped,
            block = r.blocked,
            ing = r.ingested,
            pin = r.pinboard_new,
        ));
    }
    out.push_str("</tbody></table></section>\n");
    out
}

fn candidates_header(model: &IndexModel) -> String {
    format!(
        r##"<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OVP Candidates · 候选</title>
<style>{CSS}</style></head>
<body><main>
<h1>OVP Candidates <span class="zh">待审候选</span></h1>
<p class="sub">built {built} · <a href="index.html">← console</a> · <a href="ops.html">ops</a> · <a href="audit.html">audit</a></p>
"##,
        built = esc(model.built_at.as_deref().unwrap_or(&model.date)),
    )
}

fn caveated_claims(model: &IndexModel) -> String {
    // The HUMAN queue is the Review lane only; single-source Supported claims
    // (lane = source_insight) are parked per-source insights, not review debt.
    let (insights, review): (Vec<_>, Vec<_>) = model
        .claims
        .iter()
        .filter(|c| c.status == ClaimStatus::Caveated)
        .partition(|c| c.lane.as_deref() == Some("source_insight"));
    let mut out = String::new();
    if review.is_empty() {
        out.push_str("<section><p class=\"empty\">No caveated claims pending review. 无待审保留意见主张。</p></section>\n");
    } else {
        out.push_str(
            "<section><h2>Caveated Claims <span class=\"zh\">保留意见主张</span></h2>\n<table><thead><tr><th>Theme</th><th>Claim</th><th>Strength</th></tr></thead><tbody>\n",
        );
        for c in &review {
            let (_, _, class) = claim_status_label(c.status);
            out.push_str(&format!(
                "<tr class=\"lv-{class}\"><td>{theme}</td><td>{claim}</td><td>{strength}</td></tr>\n",
                theme = esc(c.theme.as_deref().unwrap_or("—")),
                claim = esc(&c.claim),
                strength = esc(c.strength.as_deref().unwrap_or("—")),
            ));
        }
        out.push_str("</tbody></table></section>\n");
    }
    if !insights.is_empty() {
        out.push_str(&format!(
            "<section><h2>Source Insights <span class=\"zh\">单源洞见（待第二来源）</span></h2>\n\
             <p class=\"empty\">{} grounded, Supported, single-source claim(s) — parked \
             outside the review queue until more sources arrive. 已接地且判定成立，但仅有单一来源。</p>\n\
             <details><summary>Show list · 展开</summary><table><thead><tr><th>Theme</th><th>Claim</th></tr></thead><tbody>\n",
            insights.len()
        ));
        for c in &insights {
            out.push_str(&format!(
                "<tr><td>{theme}</td><td>{claim}</td></tr>\n",
                theme = esc(c.theme.as_deref().unwrap_or("—")),
                claim = esc(&c.claim),
            ));
        }
        out.push_str("</tbody></table></details></section>\n");
    }
    out
}

fn esc(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}
