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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    deployed_to: Option<String>,
}

pub fn run(cmd: PublishCmd) -> Result<(), CliError> {
    let layout = VaultLayout::new();
    let ledger_path = cmd.vault_root.join(layout.publish_ledger());
    // Propagate a CORRUPT publish ledger (a missing one is an empty first run) —
    // silently forgetting the prior state would republish and re-corrupt it.
    let last_hash = read_jsonl::<PublishRecord>(&ledger_path)
        .map_err(CliError::Io)?
        .pop()
        .map(|r| r.content_hash);

    // Assemble the site in a CLEAN directory: reusing --out would leave stale
    // hashed SPA chunks / withdrawn files behind, which then deploy. Guard the
    // recursive delete HARD — `--out .` / the vault / a parent must never be
    // nuked by a typo.
    let site_marker = cmd.out.join(".ovp-site");
    if cmd.out.exists() {
        let out_c = std::fs::canonicalize(&cmd.out)
            .map_err(|e| CliError::Io(format!("resolve --out {}: {e}", cmd.out.display())))?;
        let vault_c =
            std::fs::canonicalize(&cmd.vault_root).unwrap_or_else(|_| cmd.vault_root.clone());
        if out_c.parent().is_none() || vault_c == out_c || vault_c.starts_with(&out_c) {
            return Err(CliError::Io(format!(
                "refusing to publish into {} — it is the vault, an ancestor, or a filesystem root",
                out_c.display()
            )));
        }
        // Only clean an EMPTY dir or a prior publish site (our marker present);
        // never blow away an arbitrary existing directory.
        let is_prior_site = out_c.join(".ovp-site").is_file();
        let is_empty = std::fs::read_dir(&out_c)
            .map(|mut d| d.next().is_none())
            .unwrap_or(false);
        if !is_prior_site && !is_empty {
            return Err(CliError::Io(format!(
                "refusing to overwrite {} — not empty and not a prior publish output; remove it or choose an empty --out",
                out_c.display()
            )));
        }
        std::fs::remove_dir_all(&out_c)
            .map_err(|e| CliError::Io(format!("clean {}: {e}", out_c.display())))?;
    }

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
    // Informational only — the no-op decision is made by git over the COMPLETE
    // assembled site (below), not this content hash, so an SPA/terrain-only
    // change (or a newly-added --repo) still deploys.
    if last_hash.as_deref() == Some(report.content_hash.as_str()) {
        println!("publish: durable content unchanged since last publish");
    }

    // Marker so a re-publish knows this dir is a publish output it may clean.
    std::fs::write(&site_marker, "ovp.publish/v1\n")
        .map_err(|e| CliError::Io(format!("write {}: {e}", site_marker.display())))?;

    // Copy the pre-built static SPA bundle over the site root (if given). The
    // bundle already bakes its base path (VITE_OVP_BASE at build time) and its
    // API base is relative to it — nothing to rewrite here.
    if let Some(spa) = &cmd.spa_dir {
        copy_dir(spa, &cmd.out).map_err(CliError::Io)?;
        println!("publish: copied SPA bundle from {}", spa.display());
    } else {
        println!("publish: no --spa-dir given; wrote API JSON only (build the SPA with VITE_OVP_STATIC=1 and re-run with --spa-dir)");
    }

    // Optional deploy to a public repo. `deploy_git` diffs the WHOLE assembled
    // site against the branch and no-ops the push when nothing changed — the
    // real change-detection, covering API + SPA + terrain.
    let deployed_to = if let Some(repo) = &cmd.repo {
        let pushed = deploy_git(&cmd.out, repo, &cmd.branch, &report.content_hash, cmd.force)
            .map_err(CliError::Io)?;
        println!(
            "publish: {} {repo} ({})",
            if pushed { "pushed to" } else { "no change to deploy for" },
            cmd.branch
        );
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
fn deploy_git(
    site: &Path,
    repo: &str,
    branch: &str,
    hash: &str,
    force: bool,
) -> Result<bool, String> {
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
    // No-op gate over the COMPLETE site, EXCLUDING the volatile-stamp files
    // (published_at/built_at/run_id change every run). Without the excludes a
    // scheduled publish would push every time even when nothing durable, SPA,
    // or terrain changed; the deployed "as of" stamps then only advance when
    // real content does — which is the correct semantics for a published site.
    let msg = format!("publish {}", &hash[..12.min(hash.len())]);
    let unchanged = run_git(
        &work,
        &[
            "diff",
            "--cached",
            "--quiet",
            "--",
            ".",
            ":(exclude)api/meta.json",
            ":(exclude)api/model.json",
            ":(exclude)api/settings.json",
        ],
    )
    .is_ok();
    if unchanged {
        if !force {
            return Ok(false);
        }
        run_git(&work, &["commit", "--allow-empty", "-m", &msg])
            .map_err(|e| format!("git commit: {e}"))?;
        run_git(&work, &["push", "origin", branch]).map_err(|e| format!("git push: {e}"))?;
        return Ok(true);
    }
    run_git(&work, &["commit", "-m", &msg]).map_err(|e| format!("git commit: {e}"))?;
    run_git(&work, &["push", "origin", branch]).map_err(|e| format!("git push: {e}"))?;
    Ok(true)
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
