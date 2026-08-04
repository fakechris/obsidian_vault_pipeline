//! Auto / batch enqueue helpers for source-work (daily + backfill).
//!
//! Pure planning + queue writes. Does not call the LLM — the portal worker
//! executes translate/summarize. Callers (daily CLI, backfill CLI) assemble
//! candidates; this module never depends on ovp-daily.

use std::path::Path;

use ovp_index::model::{IndexModel, SourceStatus};
use serde::Serialize;

use crate::source_work::{self, is_primarily_english, load_status};
use crate::source_work_config::SourceWorkConfig;
use crate::source_work_queue::{EnqueueRequest, SourceWorkQueue};

/// One source considered for auto/backfill enqueue.
#[derive(Debug, Clone)]
pub struct SourceWorkCandidate {
    pub sha256: String,
    pub title: Option<String>,
    /// Optional body for English detection + existing-artifact status.
    /// When `None`, translate may still be enqueued; the worker declines
    /// non-English at execution time.
    pub body: Option<String>,
}

/// Result of one auto/backfill planning pass.
#[derive(Debug, Clone, Default, Serialize)]
pub struct AutoEnqueueReport {
    pub considered: usize,
    pub enqueued: usize,
    pub skipped_complete: usize,
    pub skipped_not_english: usize,
    pub skipped_cap: usize,
    pub errors: Vec<String>,
    pub item_ids: Vec<String>,
    /// Per-kind breakdown of `enqueued` (dry-run included) — feeds the
    /// backfill dry-run token estimate (`ovp2 usage` cold-start constants /
    /// lane averages).
    pub enqueued_translate: usize,
    pub enqueued_summarize: usize,
}

/// Decide translate/summarize flags for one candidate under `cfg`.
///
/// Returns `None` when nothing is needed (already complete / not English /
/// both flags off).
pub fn plan_tasks(
    vault_root: &Path,
    cand: &SourceWorkCandidate,
    cfg: &SourceWorkConfig,
    force: bool,
) -> Option<(bool, bool)> {
    if !cfg.auto_translate && !cfg.auto_summarize && !force {
        return None;
    }
    let body = cand.body.as_deref().unwrap_or("");
    let st = load_status(
        vault_root,
        &cand.sha256,
        cand.title.as_deref(),
        body,
    );
    let looks_english = body.is_empty() || is_primarily_english(body) || st.primarily_english;
    let want_translate = (cfg.auto_translate || force) && (force || !st.has_zh) && (force || looks_english);
    let want_summarize = (cfg.auto_summarize || force) && (force || !st.has_summary);
    // Body present and clearly non-English → skip translate (unless force).
    let want_translate = if !body.is_empty() && !is_primarily_english(body) && !force {
        false
    } else {
        want_translate
    };
    if !want_translate && !want_summarize {
        return None;
    }
    Some((want_translate, want_summarize))
}

/// Enqueue missing source-work for a list of candidates.
///
/// `force` re-queues even when artifacts exist (operator backfill --force).
/// `override_translate` / `override_summarize` when `Some` pin task kinds
/// regardless of config (CLI flags). When both overrides are `None`, config
/// flags apply.
pub fn enqueue_candidates(
    vault_root: &Path,
    queue: &SourceWorkQueue,
    candidates: &[SourceWorkCandidate],
    cfg: &SourceWorkConfig,
    force: bool,
    override_translate: Option<bool>,
    override_summarize: Option<bool>,
    dry_run: bool,
) -> AutoEnqueueReport {
    let mut report = AutoEnqueueReport::default();
    let mut cfg = cfg.clone();
    if let Some(t) = override_translate {
        cfg.auto_translate = t;
    }
    if let Some(s) = override_summarize {
        cfg.auto_summarize = s;
    }
    let cap = if cfg.auto_max_per_run == 0 {
        usize::MAX
    } else {
        cfg.auto_max_per_run
    };

    for cand in candidates {
        report.considered += 1;
        if report.enqueued >= cap {
            report.skipped_cap += 1;
            continue;
        }
        let Some((want_t, want_s)) = plan_tasks(vault_root, cand, &cfg, force) else {
            let body = cand.body.as_deref().unwrap_or("");
            if !body.is_empty()
                && !is_primarily_english(body)
                && cfg.auto_translate
                && !cfg.auto_summarize
            {
                report.skipped_not_english += 1;
            } else {
                report.skipped_complete += 1;
            }
            continue;
        };
        if dry_run {
            report.enqueued += 1;
            report.enqueued_translate += usize::from(want_t);
            report.enqueued_summarize += usize::from(want_s);
            report
                .item_ids
                .push(format!("dry-{}", &cand.sha256[..cand.sha256.len().min(8)]));
            continue;
        }
        let req = EnqueueRequest {
            sha256: cand.sha256.clone(),
            title: cand.title.clone(),
            translate: want_t,
            summarize: want_s,
            force,
            notify: cfg.auto_notify,
            // Bulk daily/backfill never jumps ahead of interactive UI jobs.
            priority: crate::source_work_queue::PRIORITY_BACKFILL,
        };
        match queue.enqueue(req) {
            Ok(item) => {
                report.enqueued += 1;
                report.enqueued_translate += usize::from(want_t);
                report.enqueued_summarize += usize::from(want_s);
                report.item_ids.push(item.id);
            }
            Err(e) => report.errors.push(format!("{}: {e}", cand.sha256)),
        }
    }
    report
}

/// Build candidates from `(sha, title, optional paths to try for body)`.
pub fn candidate_with_paths(
    vault_root: &Path,
    sha256: impl Into<String>,
    title: Option<String>,
    body_paths: &[&str],
) -> SourceWorkCandidate {
    let sha256 = sha256.into();
    let mut body = None;
    for rel in body_paths {
        let p = vault_root.join(rel);
        if p.is_file() {
            if let Ok(s) = std::fs::read_to_string(&p) {
                if !s.trim().is_empty() {
                    body = Some(s);
                    break;
                }
            }
        }
    }
    if body.is_none() {
        let work = source_work::work_rel_for(&sha256, title.as_deref());
        body = source_work::read_work_file(vault_root, &work, "original.md");
    }
    SourceWorkCandidate {
        sha256,
        title,
        body,
    }
}

/// Title heuristic from a vault-relative source path.
pub fn title_from_source_path(path: &str) -> Option<String> {
    let name = Path::new(path)
        .file_stem()
        .and_then(|s| s.to_str())
        .filter(|s| !s.is_empty())?;
    Some(name.replace('_', " "))
}

/// Build candidates from the index (readable sources).
pub fn candidates_from_index(
    vault_root: &Path,
    model: &IndexModel,
    only_missing: bool,
) -> Vec<SourceWorkCandidate> {
    let mut out = Vec::new();
    for src in &model.sources {
        match src.status {
            SourceStatus::Blocked
            | SourceStatus::NeedsContent
            | SourceStatus::Queued
            | SourceStatus::Unparseable
            // Operator-excluded: it never became a readable source, and
            // queueing it here would spend on exactly what `ovp/skip` declined.
            | SourceStatus::Skipped
            | SourceStatus::Failed => continue,
            SourceStatus::Processed | SourceStatus::Duplicate => {}
        }
        let body = src
            .rel_path
            .as_ref()
            .and_then(|rel| std::fs::read_to_string(vault_root.join(rel)).ok())
            .filter(|s| !s.trim().is_empty());
        let cand = SourceWorkCandidate {
            sha256: src.sha256.clone(),
            title: src.title.clone(),
            body,
        };
        if only_missing {
            let b = cand.body.as_deref().unwrap_or("");
            let st = load_status(vault_root, &cand.sha256, cand.title.as_deref(), b);
            if st.has_zh && st.has_summary {
                continue;
            }
        }
        out.push(cand);
    }
    out
}

/// Load config + open queue + enqueue candidates (shared entry for daily/backfill).
pub fn run_auto_enqueue(
    vault_root: &Path,
    candidates: &[SourceWorkCandidate],
    force: bool,
    override_translate: Option<bool>,
    override_summarize: Option<bool>,
    dry_run: bool,
) -> Result<AutoEnqueueReport, String> {
    let cfg = SourceWorkConfig::load(vault_root)?;
    if !cfg.auto_summarize && !cfg.auto_translate {
        if override_translate.is_none() && override_summarize.is_none() && !force {
            return Ok(AutoEnqueueReport::default());
        }
    }
    let _ = SourceWorkConfig::ensure_template(vault_root);
    let queue = SourceWorkQueue::open(vault_root);
    Ok(enqueue_candidates(
        vault_root,
        &queue,
        candidates,
        &cfg,
        force,
        override_translate,
        override_summarize,
        dry_run,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn en_body() -> String {
        "The harness is all you need for reliable evaluation of agent systems in production. \
         Teams that invest in harnesses ship faster with fewer regressions over months of work."
            .into()
    }

    #[test]
    fn plan_skips_when_complete() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path();
        let sha = "abcdef0123456789deadbeef";
        let body = en_body();
        let work = source_work::work_rel_for(sha, Some("Harness"));
        let dir = vault.join(&work);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("zh.md"), "中文").unwrap();
        std::fs::write(dir.join("summary.md"), "摘要").unwrap();
        let cand = SourceWorkCandidate {
            sha256: sha.into(),
            title: Some("Harness".into()),
            body: Some(body),
        };
        let cfg = SourceWorkConfig::default();
        assert!(plan_tasks(vault, &cand, &cfg, false).is_none());
    }

    #[test]
    fn plan_wants_both_when_empty() {
        let tmp = tempfile::tempdir().unwrap();
        let cand = SourceWorkCandidate {
            sha256: "aaaaaaaaaaaaaaaa".into(),
            title: Some("T".into()),
            body: Some(en_body()),
        };
        let cfg = SourceWorkConfig::default();
        let (t, s) = plan_tasks(tmp.path(), &cand, &cfg, false).unwrap();
        assert!(t && s);
    }

    #[test]
    fn plan_skips_translate_for_chinese_body() {
        let tmp = tempfile::tempdir().unwrap();
        let zh = "这是一篇关于大模型评估与生产落地的深度笔记。我们讨论了评测集与可靠性，以及团队如何在业务中迭代系统。";
        let cand = SourceWorkCandidate {
            sha256: "cccccccccccccccc".into(),
            title: Some("中文".into()),
            body: Some(zh.into()),
        };
        let mut cfg = SourceWorkConfig::default();
        cfg.auto_summarize = false;
        cfg.auto_translate = true;
        assert!(plan_tasks(tmp.path(), &cand, &cfg, false).is_none());
    }

    #[test]
    fn enqueue_dry_run_counts() {
        let tmp = tempfile::tempdir().unwrap();
        let queue = SourceWorkQueue::open(tmp.path());
        let cand = SourceWorkCandidate {
            sha256: "bbbbbbbbbbbbbbbb".into(),
            title: Some("X".into()),
            body: Some(en_body()),
        };
        let report = enqueue_candidates(
            tmp.path(),
            &queue,
            &[cand],
            &SourceWorkConfig::default(),
            false,
            None,
            None,
            true,
        );
        assert_eq!(report.enqueued, 1);
        assert_eq!(report.considered, 1);
        // Per-kind breakdown feeds the backfill dry-run token estimate.
        assert_eq!(report.enqueued_translate, 1);
        assert_eq!(report.enqueued_summarize, 1);
    }

    #[test]
    fn dry_run_counts_follow_kind_overrides() {
        let tmp = tempfile::tempdir().unwrap();
        let queue = SourceWorkQueue::open(tmp.path());
        let cand = SourceWorkCandidate {
            sha256: "bbbbbbbbbbbbbbbb".into(),
            title: Some("X".into()),
            body: Some(en_body()),
        };
        let report = enqueue_candidates(
            tmp.path(),
            &queue,
            &[cand],
            &SourceWorkConfig::default(),
            false,
            Some(true),
            Some(false),
            true,
        );
        assert_eq!(report.enqueued_translate, 1);
        assert_eq!(report.enqueued_summarize, 0);
    }

    #[test]
    fn title_from_path() {
        assert_eq!(
            title_from_source_path("50-Inbox/01-Raw/Hello_World.md").as_deref(),
            Some("Hello World")
        );
    }
}
