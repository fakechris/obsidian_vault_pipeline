//! `publish` — snapshot the public-safe API surface + SPA bundle into a static
//! site and (optionally) push it to a public GitHub Pages repo.
//!
//! The published site is *just another projection* of the ledgers: rebuilt
//! whole each run, content-hash-stable, so git computes a minimal diff. A
//! `.ovp/publish.jsonl` ledger records each run's content hash so a no-op
//! publish (nothing changed since last time) skips the push.

use std::path::{Path, PathBuf};

use ovp_domain::VaultLayout;
use ovp_index::now_rfc3339;
use ovp_intake::{append_jsonl, read_jsonl};
use ovp_publish::{PublishArgs, publish};
use serde::{Deserialize, Serialize};

use crate::CliError;

pub struct PublishCmd {
    pub vault_root: PathBuf,
    pub out: PathBuf,
    pub date: String,
    pub base_url: String,
    pub no_rebuild: bool,
    pub spa_dir: Option<PathBuf>,
    pub force: bool,
    pub repo: Option<String>,
    pub branch: String,
}

/// One `.ovp/publish.jsonl` record.
#[derive(Debug, Clone, Serialize, Deserialize)]
struct PublishRecord {
    schema: String,
    run_id: String,
    published_at: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    index_run_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    index_built_at: Option<String>,
    content_hash: String,
    file_count: usize,
    sources: usize,
    claims: usize,
    out_dir: String,
    base_url: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    deployed_to: Option<String>,
}

pub fn run(cmd: PublishCmd) -> Result<(), CliError> {
    let layout = VaultLayout::new();
    let ledger_path = cmd.vault_root.join(layout.publish_ledger());
    let last_hash = read_jsonl::<PublishRecord>(&ledger_path)
        .ok()
        .and_then(|recs| recs.last().map(|r| r.content_hash.clone()));

    let published_at = now_rfc3339();
    let report = publish(&PublishArgs {
        vault_root: cmd.vault_root.clone(),
        out_dir: cmd.out.clone(),
        date: cmd.date.clone(),
        no_rebuild: cmd.no_rebuild,
        published_at: Some(published_at.clone()),
    })
    .map_err(CliError::Io)?;

    println!(
        "publish: {} api file(s) → {} (sources={} durable={})",
        report.file_count,
        cmd.out.join("api").display(),
        report.sources,
        report.claims
    );

    // Change detection: identical content since last publish → skip the push.
    if !cmd.force && last_hash.as_deref() == Some(report.content_hash.as_str()) {
        println!(
            "publish: content unchanged (hash {}) — nothing to publish. Use --force to override.",
            &report.content_hash[..12.min(report.content_hash.len())]
        );
        return Ok(());
    }

    // Copy the pre-built static SPA bundle over the site root (if given).
    if let Some(spa) = &cmd.spa_dir {
        copy_dir(spa, &cmd.out).map_err(CliError::Io)?;
        println!("publish: copied SPA bundle from {}", spa.display());
    } else {
        println!("publish: no --spa-dir given; wrote API JSON only (build the SPA with VITE_OVP_STATIC=1 and re-run with --spa-dir)");
    }

    // Optional deploy to a public repo.
    let deployed_to = if let Some(repo) = &cmd.repo {
        deploy_git(&cmd.out, repo, &cmd.branch, &report.content_hash).map_err(CliError::Io)?;
        println!("publish: pushed to {repo} ({})", cmd.branch);
        Some(repo.clone())
    } else {
        None
    };

    // Record the run (append-only ledger, atomic append).
    let record = PublishRecord {
        schema: "ovp.publish/v1".into(),
        run_id: format!("publish-{published_at}"),
        published_at,
        index_run_id: report.index_run_id.clone(),
        index_built_at: report.index_built_at.clone(),
        content_hash: report.content_hash.clone(),
        file_count: report.file_count,
        sources: report.sources,
        claims: report.claims,
        out_dir: cmd.out.display().to_string(),
        base_url: cmd.base_url.clone(),
        deployed_to,
    };
    append_jsonl(&ledger_path, &record).map_err(CliError::Io)?;
    Ok(())
}

/// Recursively copy `src` into `dst` (merging; `dst` is created if missing).
fn copy_dir(src: &Path, dst: &Path) -> Result<(), String> {
    std::fs::create_dir_all(dst).map_err(|e| format!("mkdir {}: {e}", dst.display()))?;
    for entry in std::fs::read_dir(src).map_err(|e| format!("read {}: {e}", src.display()))? {
        let entry = entry.map_err(|e| format!("read entry: {e}"))?;
        let from = entry.path();
        let to = dst.join(entry.file_name());
        if from.is_dir() {
            copy_dir(&from, &to)?;
        } else {
            std::fs::copy(&from, &to)
                .map_err(|e| format!("copy {} → {}: {e}", from.display(), to.display()))?;
        }
    }
    Ok(())
}

/// Deploy `site` to `<repo>#<branch>` via a temp clone: shallow-clone the
/// branch (or start an orphan branch if it doesn't exist), replace its tracked
/// content with `site`, commit, and push. Uses the ambient git credentials /
/// token (e.g. sourced from `.ovp/daily.env` by the scheduler).
fn deploy_git(site: &Path, repo: &str, branch: &str, hash: &str) -> Result<(), String> {
    let work = site
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join(".publish-deploy");
    let _ = std::fs::remove_dir_all(&work);

    let cloned = run_git(
        Path::new("."),
        &["clone", "--depth", "1", "--branch", branch, repo, &work.display().to_string()],
    );
    if cloned.is_err() {
        // Branch (or repo history) doesn't exist yet — start it fresh.
        run_git(Path::new("."), &["clone", "--depth", "1", repo, &work.display().to_string()])
            .map_err(|e| format!("git clone {repo}: {e}"))?;
        run_git(&work, &["checkout", "--orphan", branch])
            .map_err(|e| format!("git checkout --orphan {branch}: {e}"))?;
        run_git(&work, &["rm", "-rf", "--ignore-unmatch", "."]).ok();
    }

    // Replace tracked content with the freshly-built site (keep .git).
    for entry in std::fs::read_dir(&work).map_err(|e| format!("read deploy dir: {e}"))? {
        let p = entry.map_err(|e| format!("{e}"))?.path();
        if p.file_name().and_then(|n| n.to_str()) == Some(".git") {
            continue;
        }
        if p.is_dir() {
            std::fs::remove_dir_all(&p).ok();
        } else {
            std::fs::remove_file(&p).ok();
        }
    }
    copy_dir(site, &work)?;
    // GitHub Pages: don't run the output through Jekyll.
    std::fs::write(work.join(".nojekyll"), "").ok();

    run_git(&work, &["add", "-A"]).map_err(|e| format!("git add: {e}"))?;
    // Nothing staged → nothing to push (belt-and-suspenders with change detection).
    if run_git(&work, &["diff", "--cached", "--quiet"]).is_ok() {
        return Ok(());
    }
    run_git(&work, &["commit", "-m", &format!("publish {}", &hash[..12.min(hash.len())])])
        .map_err(|e| format!("git commit: {e}"))?;
    run_git(&work, &["push", "origin", branch]).map_err(|e| format!("git push: {e}"))?;
    Ok(())
}

/// Run a git command in `dir`; error carries stderr on non-zero exit.
fn run_git(dir: &Path, args: &[&str]) -> Result<(), String> {
    let out = std::process::Command::new("git")
        .args(args)
        .current_dir(dir)
        .output()
        .map_err(|e| format!("spawn git: {e}"))?;
    if out.status.success() {
        Ok(())
    } else {
        Err(String::from_utf8_lossy(&out.stderr).trim().to_string())
    }
}
