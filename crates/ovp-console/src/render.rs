//! Pure HTML rendering over the read model. No clock, no fs, no environment —
//! same model in, same bytes out. Style vocabulary (dark theme, pills,
//! EN + 中文) follows the M28 console.

use ovp_index::{ClaimStatus, IndexModel, PackRow, RunRow, SourceRow, SourceStatus};

use crate::{claim_status_label, source_status_label};

/// How many runs the Runs table shows (newest first). Older runs stay in
/// `.ovp/reports/` — the truncation is announced, never silent.
const MAX_RUNS_SHOWN: usize = 20;

/// From `.ovp/console/index.html` back up to the vault root.
const VAULT_REL: &str = "../..";

pub fn render_console(model: &IndexModel) -> String {
    let mut page = String::with_capacity(64 * 1024);
    page.push_str(&header(model));
    page.push_str(&attention_section(model));
    page.push_str(&runs_section(&model.runs));
    page.push_str(&sources_section(&model.sources));
    page.push_str(&packs_section(&model.packs));
    page.push_str(&crystal_section(model));
    page.push_str(&footer(model));
    page.push_str("</main></body></html>\n");
    page
}

fn header(model: &IndexModel) -> String {
    let t = &model.totals;
    format!(
        r##"<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OVP Console · 工作台</title>
<style>{CSS}</style></head>
<body><main>
<h1>OVP Console <span class="zh">工作台</span></h1>
<p class="sub">read model built {date}{run} · product state only · regenerate: <code>ovp2 console --vault-root …</code></p>
<div class="strip">
{queued}{processed}{failed}{blocked}{needs}{packs}{durable}{caveated}
</div>
"##,
        date = esc(&model.date),
        run = model
            .run_id
            .as_deref()
            .map(|r| format!(" (run {})", esc(r)))
            .unwrap_or_default(),
        queued = stat(t.queued, "queued", "待读"),
        processed = stat(t.processed, "processed", "已处理"),
        failed = stat(t.failed, "failed", "失败"),
        blocked = stat(t.blocked, "blocked", "失败暂停"),
        needs = stat(t.needs_content, "needs content", "待补内容"),
        packs = stat(t.packs, "reader packs", "阅读包"),
        durable = stat(t.claims_durable, "durable claims", "持久化主张"),
        caveated = stat(t.claims_caveated, "caveated", "保留意见"),
    )
}

fn stat(n: usize, en: &str, zh: &str) -> String {
    format!("<div class=\"stat\"><b>{n}</b><span>{en}<i class=\"zh\">{zh}</i></span></div>\n")
}

/// The default review feed: everything that needs the operator.
fn attention_section(model: &IndexModel) -> String {
    let mut items = String::new();
    for s in &model.sources {
        let (en, zh, class) = source_status_label(s.status);
        let action = match s.status {
            SourceStatus::Blocked => "review and fix, then rerun with --retry-blocked · 检查后用 --retry-blocked 重试",
            SourceStatus::Failed => "will retry on the next daily run · 下次运行自动重试",
            SourceStatus::NeedsContent => "add body content where it sits · 原地补充正文",
            SourceStatus::Unparseable => "fix the frontmatter where it sits · 原地修复元数据",
            _ => continue,
        };
        items.push_str(&format!(
            "<li class=\"lv-{class}\"><span class=\"pill s-{class}\">{en} <i class=\"zh\">{zh}</i></span> <b>{title}</b>{loc}{reason}<div class=\"hint\">{action}</div></li>\n",
            title = esc(s.title.as_deref().unwrap_or("(untitled)")),
            loc = s
                .rel_path
                .as_deref()
                .map(|p| format!(" <code>{}</code>", esc(p)))
                .unwrap_or_default(),
            reason = s
                .last_reason
                .as_deref()
                .map(|r| format!("<div class=\"reason\">{}</div>", esc(r)))
                .unwrap_or_default(),
        ));
    }
    let latest_warnings: usize = model.runs.last().map(|r| r.lifecycle_warnings).unwrap_or(0);
    if latest_warnings > 0 {
        items.push_str(&format!(
            "<li class=\"lv-warn\"><span class=\"pill s-warn\">lifecycle <i class=\"zh\">生命周期</i></span> latest run reported {latest_warnings} lifecycle warning(s) — see its report · 最近一次运行有 {latest_warnings} 条生命周期警告，见运行报告</li>\n"
        ));
    }
    let body = if items.is_empty() {
        "<p class=\"empty\">Nothing needs attention. 一切正常。</p>".to_string()
    } else {
        format!("<ul class=\"feed\">\n{items}</ul>")
    };
    format!("<section><h2>Attention <span class=\"zh\">待处理</span></h2>\n{body}\n</section>\n")
}

fn runs_section(runs: &[RunRow]) -> String {
    if runs.is_empty() {
        return "<section><h2>Runs <span class=\"zh\">运行</span></h2><p class=\"empty\">No runs recorded yet. 还没有运行记录。</p></section>\n".into();
    }
    let total = runs.len();
    let shown: Vec<&RunRow> = runs.iter().rev().take(MAX_RUNS_SHOWN).collect();
    let mut rows = String::new();
    for r in &shown {
        rows.push_str(&format!(
            "<tr><td>{}</td><td><a href=\"{}\">{}</a></td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>\n",
            esc(&r.date),
            href(&format!("{VAULT_REL}/{}", r.report_file)),
            esc(&r.run_id),
            r.succeeded,
            r.failed,
            r.skipped,
            r.ingested,
            r.pinboard_new,
        ));
    }
    let note = if total > shown.len() {
        format!("<p class=\"hint\">showing latest {} of {total} runs · 仅显示最近 {} 条</p>", shown.len(), shown.len())
    } else {
        String::new()
    };
    format!(
        "<section><h2>Runs <span class=\"zh\">运行</span></h2>\n<table><thead><tr><th>date · 日期</th><th>run · 运行报告</th><th>ok · 成功</th><th>failed · 失败</th><th>skipped · 跳过</th><th>ingested · 摄入</th><th>pinboard · 书签</th></tr></thead><tbody>\n{rows}</tbody></table>{note}\n</section>\n"
    )
}

fn sources_section(sources: &[SourceRow]) -> String {
    if sources.is_empty() {
        return "<section><h2>Sources <span class=\"zh\">来源</span></h2><p class=\"empty\">No sources yet — drop clippings into the vault or run pinboard-sync. 暂无来源。</p></section>\n".into();
    }
    let mut rows = String::new();
    for s in sources {
        let (en, zh, class) = source_status_label(s.status);
        let title = esc(s.title.as_deref().unwrap_or("(untitled)"));
        // External URLs get attribute-escaping ONLY — percent-encoding `?`/`#`
        // (href() is for vault-relative paths) would rewrite the query string
        // and break the provenance link.
        let title_cell = match (&s.url, &s.rel_path) {
            (Some(u), _) => format!("<a href=\"{}\">{title}</a>", esc(u)),
            (None, _) => title.clone(),
        };
        let pack_cell = s
            .pack_dir
            .as_deref()
            .map(|p| format!("<a href=\"{}\">pack · 阅读包</a>", href(&format!("{VAULT_REL}/{p}/reader.html"))))
            .unwrap_or_default();
        rows.push_str(&format!(
            "<tr><td><span class=\"pill s-{class}\">{en} <i class=\"zh\">{zh}</i></span></td><td>{title_cell}</td><td>{}</td><td><code>{}</code></td><td>{pack_cell}</td></tr>\n",
            esc(s.date.as_deref().unwrap_or("—")),
            esc(s.rel_path.as_deref().unwrap_or("—")),
        ));
    }
    format!(
        "<section><h2>Sources <span class=\"zh\">来源</span></h2>\n<table><thead><tr><th>status · 状态</th><th>title · 标题</th><th>date · 日期</th><th>location · 位置</th><th>provenance · 溯源</th></tr></thead><tbody>\n{rows}</tbody></table>\n</section>\n"
    )
}

fn packs_section(packs: &[PackRow]) -> String {
    if packs.is_empty() {
        return "<section><h2>Reader Packs <span class=\"zh\">阅读包</span></h2><p class=\"empty\">No reader packs yet. 暂无阅读包。</p></section>\n".into();
    }
    let mut cards = String::new();
    for p in packs {
        let mut titles = String::new();
        for t in &p.card_titles {
            titles.push_str(&format!("<li>{}</li>", esc(t)));
        }
        cards.push_str(&format!(
            "<div class=\"card lv-ok\"><h3><a href=\"{html}\">{title}</a></h3><p class=\"meta\">{date} · {cards} cards · {units} grounded units{repair} · <a href=\"{md}\">reader.md</a></p><ul class=\"cardlist\">{titles}</ul></div>\n",
            html = href(&format!("{VAULT_REL}/{}/reader.html", p.pack_dir)),
            md = href(&format!("{VAULT_REL}/{}/reader.md", p.pack_dir)),
            title = esc(&p.title),
            date = esc(p.date.as_deref().unwrap_or("—")),
            cards = p.cards,
            units = p.units,
            repair = if p.json_repaired { " · json-repaired" } else { "" },
        ));
    }
    format!(
        "<section><h2>Reader Packs <span class=\"zh\">阅读包</span></h2>\n<div class=\"cards\">{cards}</div>\n</section>\n"
    )
}

fn crystal_section(model: &IndexModel) -> String {
    let durable: Vec<_> = model
        .claims
        .iter()
        .filter(|c| c.status == ClaimStatus::Durable)
        .collect();
    let rest: Vec<_> = model
        .claims
        .iter()
        .filter(|c| c.status != ClaimStatus::Durable)
        .collect();
    if durable.is_empty() && rest.is_empty() {
        return "<section><h2>Crystal <span class=\"zh\">结晶主张</span></h2><p class=\"empty\">No crystal store yet — see the runbook for crystal-write with the vault-local store. 暂无结晶库。</p></section>\n".into();
    }
    let mut out = String::from("<section><h2>Crystal <span class=\"zh\">结晶主张</span></h2>\n");
    if !durable.is_empty() {
        out.push_str("<h3>Durable <span class=\"zh\">持久化</span></h3><ul class=\"feed\">\n");
        for c in durable {
            out.push_str(&format!(
                "<li class=\"lv-ok\"><span class=\"pill s-ok\">durable <i class=\"zh\">持久化</i></span> <b>{}</b> <span class=\"meta\">{}{}</span><div class=\"reason\">{}</div></li>\n",
                esc(&c.claim_id),
                esc(c.theme.as_deref().unwrap_or("")),
                if c.sources.is_empty() {
                    String::new()
                } else {
                    format!(" · {n} sources · {n} 个来源", n = c.sources.len())
                },
                esc(&c.claim),
            ));
        }
        out.push_str("</ul>\n");
    }
    if !rest.is_empty() {
        out.push_str("<h3>Under review / lifecycle <span class=\"zh\">复核与生命周期</span></h3><ul class=\"feed\">\n");
        for c in rest {
            let (en, zh, class) = claim_status_label(c.status);
            out.push_str(&format!(
                "<li class=\"lv-{class}\"><span class=\"pill s-{class}\">{en} <i class=\"zh\">{zh}</i></span> <b>{}</b>{} <div class=\"reason\">{}</div></li>\n",
                esc(&c.claim_id),
                c.strength
                    .as_deref()
                    .map(|s| format!(" <span class=\"meta\">{}</span>", esc(s)))
                    .unwrap_or_default(),
                esc(&c.claim),
            ));
        }
        out.push_str("</ul>\n");
    }
    out.push_str("</section>\n");
    out
}

fn footer(model: &IndexModel) -> String {
    format!(
        "<footer><p>{} sources · {} packs · {} runs — every row derives from the append-only ledgers (<code>.ovp/daily-runs.jsonl</code>, <code>.ovp/intake.jsonl</code>), the reader packs, and the crystal store; delete <code>.ovp/index</code>/<code>.ovp/console</code> anytime and rebuild. 本页由账本与阅读包推导生成，可随时重建。</p></footer>\n",
        model.totals.sources, model.totals.packs, model.totals.runs
    )
}

fn esc(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

/// Percent-encode the characters that break `href="…"` attributes in paths
/// (notably spaces in pack/report names) without mangling `/` or unicode.
fn href(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '%' => out.push_str("%25"),
            ' ' => out.push_str("%20"),
            '"' => out.push_str("%22"),
            '#' => out.push_str("%23"),
            '?' => out.push_str("%3F"),
            '<' => out.push_str("%3C"),
            '>' => out.push_str("%3E"),
            _ => out.push(c),
        }
    }
    out
}

const CSS: &str = r#"
:root{--bg:#0f1419;--panel:#161c24;--text:#d7dde6;--dim:#7a8aa0;
--ok:#3fb27f;--warn:#d99a2b;--bad:#e0617a;--info:#5b8def;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif}
main{max-width:1080px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:20px;margin:0 0 2px}
h2{font-size:15px;margin:34px 0 10px;border-bottom:1px solid #232c38;padding-bottom:6px}
h3{font-size:13px;margin:14px 0 8px}
.zh{color:var(--dim);font-weight:400;font-size:.85em;margin-left:6px}
.sub{color:var(--dim);margin:0 0 16px}
code{background:var(--panel);padding:1px 5px;border-radius:4px;font-size:12px}
a{color:var(--info);text-decoration:none}a:hover{text-decoration:underline}
.strip{display:flex;flex-wrap:wrap;gap:10px}
.stat{background:var(--panel);border-radius:8px;padding:10px 14px;min-width:96px}
.stat b{font-size:18px;display:block}
.stat span{color:var(--dim);font-size:11px}.stat i{font-style:normal;display:block}
table{width:100%;border-collapse:collapse;background:var(--panel);border-radius:8px;overflow:hidden}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #1f2733;vertical-align:top}
th{color:var(--dim);font-weight:500;font-size:11px}
tr:last-child td{border-bottom:0}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;white-space:nowrap}
.pill .zh{margin-left:4px;font-style:normal}
.s-ok{background:rgba(63,178,127,.12);color:var(--ok)}
.s-warn{background:rgba(217,154,43,.12);color:var(--warn)}
.s-bad{background:rgba(224,97,122,.12);color:var(--bad)}
.s-info{background:rgba(91,141,239,.12);color:var(--info)}
.s-dim{background:rgba(122,138,160,.12);color:var(--dim)}
.feed{list-style:none;margin:0;padding:0}
.feed li{background:var(--panel);border-radius:8px;padding:10px 12px;margin:8px 0;border-left:3px solid var(--dim)}
.lv-ok{border-left-color:var(--ok)!important}
.lv-warn{border-left-color:var(--warn)!important}
.lv-bad{border-left-color:var(--bad)!important}
.lv-info{border-left-color:var(--info)!important}
.lv-dim{border-left-color:var(--dim)!important}
.reason{color:var(--dim);margin-top:4px;font-size:12px}
.hint{color:var(--dim);margin-top:4px;font-size:11px}
.empty{color:var(--dim);background:var(--panel);border-radius:8px;padding:14px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:10px}
.card{background:var(--panel);border-radius:8px;padding:12px 14px;border-left:3px solid var(--dim)}
.card h3{margin:0 0 4px}
.meta{color:var(--dim);font-size:11px}
.cardlist{margin:8px 0 0;padding-left:18px;color:var(--dim)}
footer{margin-top:40px;color:var(--dim);font-size:11px}
"#;

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_index::{ClaimRow, OpsState, PackRow, RunRow, SourceRow, Totals};

    fn model() -> IndexModel {
        IndexModel {
            schema: "ovp.index/v1".into(),
            date: "2026-06-09".into(),
            run_id: Some("daily-2026-06-09".into()),
            totals: Totals { sources: 2, processed: 1, blocked: 1, packs: 1, claims_durable: 1, claims_caveated: 1, runs: 1, ..Default::default() },
            sources: vec![
                SourceRow {
                    sha256: "aaaa".into(),
                    status: SourceStatus::Processed,
                    title: Some("Good <Article> & Co".into()),
                    url: Some("https://e.x/good?a=1&b=2".into()),
                    rel_path: Some("50-Inbox/03-Processed/2026-06/good.md".into()),
                    date: Some("2026-06-09".into()),
                    last_run_id: Some("daily-2026-06-09".into()),
                    pack_dir: Some("40-Resources/Reader/2026-06-09_Good Article-aaaa1111".into()),
                    fail_count: 0,
                    last_reason: None,
                },
                SourceRow {
                    sha256: "cccc".into(),
                    status: SourceStatus::Blocked,
                    title: Some("Flaky".into()),
                    url: None,
                    rel_path: Some("50-Inbox/01-Raw/flaky.md".into()),
                    date: Some("2026-06-09".into()),
                    last_run_id: None,
                    pack_dir: None,
                    fail_count: 3,
                    last_reason: Some("card synthesis did not parse".into()),
                },
            ],
            packs: vec![PackRow {
                pack_dir: "40-Resources/Reader/2026-06-09_Good Article-aaaa1111".into(),
                title: "Good Article".into(),
                date: Some("2026-06-09".into()),
                units: 2,
                cards: 1,
                json_repaired: false,
                card_titles: vec!["Chunks are neutral".into()],
                source_sha256: Some("aaaa".into()),
            }],
            claims: vec![
                ClaimRow {
                    claim_id: "c01".into(),
                    claim: "Filesystem works as memory.".into(),
                    theme: Some("memory".into()),
                    status: ClaimStatus::Durable,
                    sources: vec!["case-a".into(), "case-b".into()],
                    strength: Some("supported".into()),
                    run_id: Some("crystal-1".into()),
                    lane: None,
                },
                ClaimRow {
                    claim_id: "c02".into(),
                    claim: "Context is the moat.".into(),
                    theme: None,
                    status: ClaimStatus::Caveated,
                    sources: vec![],
                    strength: Some("opinion_as_fact".into()),
                    run_id: None,
                    lane: None,
                },
            ],
            runs: vec![RunRow {
                run_id: "daily-2026-06-09".into(),
                date: "2026-06-09".into(),
                report_file: ".ovp/reports/daily-2026-06-09.json".into(),
                succeeded: 1,
                failed: 0,
                skipped: 0,
                blocked: 1,
                ingested: 2,
                pinboard_new: 1,
                lifecycle_warnings: 0,
            }],
            ops: OpsState::default(),
        }
    }

    #[test]
    fn renders_bilingual_sections_pills_and_escaped_links() {
        let html = render_console(&model());
        // Bilingual section headers + status pills.
        for needle in [
            "OVP Console", "工作台", "Attention", "待处理", "Sources", "来源",
            "Reader Packs", "阅读包", "Crystal", "结晶主张", "持久化", "保留意见", "失败暂停",
        ] {
            assert!(html.contains(needle), "missing {needle}");
        }
        // HTML-escaped title; percent-escaped pack link with spaces.
        assert!(html.contains("Good &lt;Article&gt; &amp; Co"));
        assert!(html.contains("40-Resources/Reader/2026-06-09_Good%20Article-aaaa1111/reader.html"));
        // External source URLs keep their query string intact (attribute
        // escaping only — no %3F/%23 mangling).
        assert!(html.contains("href=\"https://e.x/good?a=1&amp;b=2\""), "external URL preserved");
        // Attention feed carries the blocked source + its reason + action hint.
        assert!(html.contains("card synthesis did not parse"));
        assert!(html.contains("--retry-blocked"));
        // Crystal split into durable vs review.
        assert!(html.contains("Filesystem works as memory."));
        assert!(html.contains("opinion_as_fact"));
        // Runs table links the report file.
        assert!(html.contains(".ovp/reports/daily-2026-06-09.json"));
        // Deterministic.
        assert_eq!(html, render_console(&model()));
    }

    #[test]
    fn empty_model_renders_empty_states() {
        let empty = IndexModel {
            schema: "ovp.index/v1".into(),
            date: "2026-06-09".into(),
            run_id: None,
            totals: Totals::default(),
            sources: vec![],
            packs: vec![],
            claims: vec![],
            runs: vec![],
            ops: OpsState::default(),
        };
        let html = render_console(&empty);
        assert!(html.contains("Nothing needs attention"));
        assert!(html.contains("No runs recorded yet"));
        assert!(html.contains("No crystal store yet"));
    }

    #[test]
    fn write_console_writes_under_ovp_console() {
        let dir = tempfile::tempdir().unwrap();
        let rel = crate::write_console(dir.path(), &model()).unwrap();
        assert_eq!(rel, ".ovp/console/index.html");
        assert!(dir.path().join(rel).exists());
    }
}
