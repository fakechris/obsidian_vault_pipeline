//! The full publish RUN — orchestration shared by `ovp2 publish` and the
//! portal's `POST /api/publish`: config resolution (`.ovp/publish.toml` with
//! CLI overrides), out-dir safety guards, site assembly, SPA copy, optional
//! git deploy, and the append-only `.ovp/publish.jsonl` ledger. The CLI and
//! server print/serialize the returned [`RunSummary`]; nothing here writes to
//! stdout.

use std::path::{Path, PathBuf};

use ovp_domain::VaultLayout;
use ovp_index::now_rfc3339;
use ovp_intake::{append_jsonl, read_jsonl};
use serde::{Deserialize, Serialize};

use crate::{PublishArgs, publish};

/// `.ovp/publish.toml` — persistent publish settings so the portal/desktop
/// button and scheduled runs need no flags. Relative paths resolve against
/// the vault root. Every field is optional; CLI flags override.
#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PublishConfig {
    /// Site output directory.
    pub out: Option<PathBuf>,
    /// Git remote to deploy to (e.g. `git@github.com:me/site.git`).
    pub repo: Option<String>,
    /// Deploy branch (default `gh-pages`).
    pub branch: Option<String>,
    /// Pre-built `VITE_OVP_STATIC=1` SPA bundle to copy over the site root.
    pub spa_dir: Option<PathBuf>,
}

/// Read `.ovp/publish.toml`. Missing → `Ok(None)` (flags-only operation);
/// unparseable/unknown keys → `Err` (a typo'd config must not silently
/// publish to the wrong place).
pub fn load_publish_config(vault_root: &Path) -> Result<Option<PublishConfig>, String> {
    let path = vault_root.join(".ovp/publish.toml");
    let raw = match std::fs::read_to_string(&path) {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(format!("reading {}: {e}", path.display())),
    };
    let cfg: PublishConfig =
        toml::from_str(&raw).map_err(|e| format!("parsing {}: {e}", path.display()))?;
    Ok(Some(cfg))
}

/// Flag-level overrides for one run (all optional — config supplies the rest).
#[derive(Debug, Clone, Default)]
pub struct RunOverrides {
    pub out: Option<PathBuf>,
    pub repo: Option<String>,
    pub branch: Option<String>,
    pub spa_dir: Option<PathBuf>,
    pub force: bool,
    pub no_rebuild: bool,
}

/// Fully-resolved settings for one run (override > config > default).
#[derive(Debug, Clone)]
pub struct ResolvedPublish {
    pub out: PathBuf,
    pub repo: Option<String>,
    pub branch: String,
    pub spa_dir: Option<PathBuf>,
}

/// Merge overrides over `.ovp/publish.toml`. `out` is required from one of
/// the two; relative config paths resolve against the vault root.
pub fn resolve_publish(
    vault_root: &Path,
    overrides: &RunOverrides,
) -> Result<ResolvedPublish, String> {
    let cfg = load_publish_config(vault_root)?.unwrap_or_default();
    let against_vault = |p: PathBuf| -> PathBuf {
        if p.is_absolute() {
            p
        } else {
            vault_root.join(p)
        }
    };
    let out = overrides
        .out
        .clone()
        .or_else(|| cfg.out.clone().map(against_vault))
        .ok_or_else(|| {
            format!(
                "no output directory — pass --out or set `out` in {}",
                vault_root.join(".ovp/publish.toml").display()
            )
        })?;
    Ok(ResolvedPublish {
        out,
        repo: overrides.repo.clone().or(cfg.repo),
        branch: overrides
            .branch
            .clone()
            .or(cfg.branch)
            .unwrap_or_else(|| "gh-pages".to_string()),
        spa_dir: overrides
            .spa_dir
            .clone()
            .or_else(|| cfg.spa_dir.map(against_vault)),
    })
}

/// One `.ovp/publish.jsonl` record.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PublishRecord {
    pub schema: String,
    pub run_id: String,
    pub published_at: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub index_run_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub index_built_at: Option<String>,
    pub content_hash: String,
    pub file_count: usize,
    pub sources: usize,
    pub claims: usize,
    pub out_dir: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deployed_to: Option<String>,
}

/// What one publish run did — the CLI prints it, `POST /api/publish` ships it
/// as JSON.
#[derive(Debug, Clone, Serialize)]
pub struct RunSummary {
    pub file_count: usize,
    pub sources: usize,
    pub claims: usize,
    pub content_hash: String,
    /// Durable content hash unchanged since the previous ledger record
    /// (informational — the deploy no-op gate diffs the whole site).
    pub content_unchanged: bool,
    pub out_dir: String,
    pub spa_copied: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub deployed_to: Option<String>,
    /// `Some(true)` pushed, `Some(false)` no-op deploy, `None` no repo.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pushed: Option<bool>,
}

/// Execute a full publish run with resolved settings + `date`.
pub fn run_publish(
    vault_root: &Path,
    date: &str,
    resolved: &ResolvedPublish,
    force: bool,
    no_rebuild: bool,
) -> Result<RunSummary, String> {
    let layout = VaultLayout::new();
    let ledger_path = vault_root.join(layout.publish_ledger());
    // Propagate a CORRUPT publish ledger (a missing one is an empty first
    // run) — silently forgetting prior state would republish and re-corrupt.
    let last_hash = read_jsonl::<PublishRecord>(&ledger_path)?
        .pop()
        .map(|r| r.content_hash);

    guard_out_dir(vault_root, &resolved.out)?;

    let published_at = now_rfc3339();
    let report = publish(&PublishArgs {
        vault_root: vault_root.to_path_buf(),
        out_dir: resolved.out.clone(),
        date: date.to_string(),
        no_rebuild,
        published_at: Some(published_at.clone()),
    })?;

    // Marker so a re-publish knows this dir is a publish output it may clean.
    let site_marker = resolved.out.join(".ovp-site");
    std::fs::write(&site_marker, "ovp.publish/v1\n")
        .map_err(|e| format!("write {}: {e}", site_marker.display()))?;

    // Copy the pre-built static SPA bundle over the site root (if given). The
    // bundle bakes its base path at build time — nothing to rewrite here.
    let spa_copied = if let Some(spa) = &resolved.spa_dir {
        copy_dir(spa, &resolved.out)?;
        true
    } else {
        false
    };

    // Optional deploy. `deploy_git` diffs the WHOLE assembled site against
    // the branch and no-ops the push when nothing changed.
    let (deployed_to, pushed) = if let Some(repo) = &resolved.repo {
        let pushed = deploy_git(
            &resolved.out,
            repo,
            &resolved.branch,
            &report.content_hash,
            force,
        )?;
        (Some(repo.clone()), Some(pushed))
    } else {
        (None, None)
    };

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
        out_dir: resolved.out.display().to_string(),
        deployed_to: deployed_to.clone(),
    };
    append_jsonl(&ledger_path, &record)?;

    Ok(RunSummary {
        file_count: report.file_count,
        sources: report.sources,
        claims: report.claims,
        content_unchanged: last_hash.as_deref() == Some(report.content_hash.as_str()),
        content_hash: report.content_hash,
        out_dir: resolved.out.display().to_string(),
        spa_copied,
        deployed_to,
        pushed,
    })
}

/// Out-dir safety: reject `..`, the vault, ancestors, filesystem roots, and
/// non-empty dirs that are not a prior publish output; clean what remains.
fn guard_out_dir(vault_root: &Path, out: &Path) -> Result<(), String> {
    // Reject `..` outright: `<vault>/new/..` doesn't `exists()` yet, so it
    // would skip every guard below, then `create_dir_all` resolves it back to
    // the vault and the clean/copy would hit real data.
    if out
        .components()
        .any(|c| matches!(c, std::path::Component::ParentDir))
    {
        return Err(format!("out dir {} must not contain `..`", out.display()));
    }
    if out.exists() {
        let out_c = std::fs::canonicalize(out)
            .map_err(|e| format!("resolve out dir {}: {e}", out.display()))?;
        let vault_c =
            std::fs::canonicalize(vault_root).unwrap_or_else(|_| vault_root.to_path_buf());
        if out_c.parent().is_none() || vault_c == out_c || vault_c.starts_with(&out_c) {
            return Err(format!(
                "refusing to publish into {} — it is the vault, an ancestor, or a filesystem root",
                out_c.display()
            ));
        }
        // Only clean an EMPTY dir or a prior publish site (marker present);
        // never blow away an arbitrary existing directory.
        let is_prior_site = out_c.join(".ovp-site").is_file();
        let is_empty = std::fs::read_dir(&out_c)
            .map(|mut d| d.next().is_none())
            .unwrap_or(false);
        if !is_prior_site && !is_empty {
            return Err(format!(
                "refusing to overwrite {} — not empty and not a prior publish output; \
                 remove it or choose an empty out dir",
                out_c.display()
            ));
        }
        std::fs::remove_dir_all(&out_c).map_err(|e| format!("clean {}: {e}", out_c.display()))?;
    }
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
/// token (e.g. sourced from `.ovp/daily.env` or providers.toml).
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
        &[
            "clone",
            "--depth",
            "1",
            "--branch",
            branch,
            repo,
            &work.display().to_string(),
        ],
    );
    if cloned.is_err() {
        // Branch (or repo history) doesn't exist yet — start it fresh.
        run_git(
            Path::new("."),
            &["clone", "--depth", "1", repo, &work.display().to_string()],
        )
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
    // (published_at/built_at/run_id change every run) — the deployed "as of"
    // stamps only advance when real content does.
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn config_missing_corrupt_and_unknown_keys() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        assert_eq!(load_publish_config(root).unwrap(), None);
        std::fs::create_dir_all(root.join(".ovp")).unwrap();
        std::fs::write(root.join(".ovp/publish.toml"), "not toml [").unwrap();
        assert!(load_publish_config(root).is_err());
        // Unknown keys fail loud — a typo must not silently mispublish.
        std::fs::write(root.join(".ovp/publish.toml"), "outt = \"site\"\n").unwrap();
        assert!(load_publish_config(root).is_err());
        std::fs::write(
            root.join(".ovp/publish.toml"),
            "out = \"site\"\nrepo = \"git@github.com:me/site.git\"\n",
        )
        .unwrap();
        let cfg = load_publish_config(root).unwrap().unwrap();
        assert_eq!(cfg.out.as_deref(), Some(Path::new("site")));
    }

    #[test]
    fn resolve_merges_overrides_over_config_and_requires_out() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        // No config, no override → clear error naming the config path.
        let err = resolve_publish(root, &RunOverrides::default()).unwrap_err();
        assert!(err.contains("publish.toml"), "{err}");

        std::fs::create_dir_all(root.join(".ovp")).unwrap();
        std::fs::write(
            root.join(".ovp/publish.toml"),
            "out = \"site\"\nrepo = \"git@github.com:me/site.git\"\nbranch = \"pages\"\nspa_dir = \"dist\"\n",
        )
        .unwrap();
        let r = resolve_publish(root, &RunOverrides::default()).unwrap();
        assert_eq!(
            r.out,
            root.join("site"),
            "relative config paths resolve against the vault"
        );
        assert_eq!(r.branch, "pages");
        assert_eq!(r.spa_dir.as_deref(), Some(root.join("dist").as_path()));

        // Overrides win field-by-field; branch default applies only when
        // neither side sets it.
        let r = resolve_publish(
            root,
            &RunOverrides {
                out: Some(PathBuf::from("/tmp/other")),
                repo: Some("git@github.com:me/other.git".into()),
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(r.out, PathBuf::from("/tmp/other"));
        assert_eq!(r.repo.as_deref(), Some("git@github.com:me/other.git"));
        std::fs::write(root.join(".ovp/publish.toml"), "out = \"site\"\n").unwrap();
        let r = resolve_publish(root, &RunOverrides::default()).unwrap();
        assert_eq!(r.branch, "gh-pages");
    }

    #[test]
    fn guard_rejects_dotdot_vault_and_nonempty_dirs() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path().join("vault");
        std::fs::create_dir_all(&vault).unwrap();
        assert!(guard_out_dir(&vault, &vault.join("x/..")).is_err());
        assert!(guard_out_dir(&vault, &vault).is_err());
        let nonempty = tmp.path().join("stuff");
        std::fs::create_dir_all(&nonempty).unwrap();
        std::fs::write(nonempty.join("keep.txt"), "x").unwrap();
        assert!(guard_out_dir(&vault, &nonempty).is_err());
        // A prior publish output (marker) is cleanable.
        let site = tmp.path().join("site");
        std::fs::create_dir_all(&site).unwrap();
        std::fs::write(site.join(".ovp-site"), "ovp.publish/v1\n").unwrap();
        guard_out_dir(&vault, &site).unwrap();
        assert!(!site.exists(), "prior site dir is cleaned");
    }
}
