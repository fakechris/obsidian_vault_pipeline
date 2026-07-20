//! `publish` — snapshot the public-safe API surface + SPA bundle into a static
//! site and (optionally) push it to a public GitHub Pages repo.
//!
//! Thin CLI shell: the whole run (config resolution over `.ovp/publish.toml`,
//! out-dir guards, assembly, SPA copy, git deploy, publish ledger) lives in
//! `ovp_publish::run` so the portal's `POST /api/publish` executes the exact
//! same path.

use std::path::PathBuf;

use ovp_publish::run::{RunOverrides, resolve_publish, run_publish};

use crate::CliError;

pub struct PublishCmd {
    pub vault_root: PathBuf,
    pub out: Option<PathBuf>,
    pub date: String,
    pub no_rebuild: bool,
    pub spa_dir: Option<PathBuf>,
    pub force: bool,
    pub repo: Option<String>,
    pub branch: Option<String>,
}

pub fn run(cmd: PublishCmd) -> Result<(), CliError> {
    let overrides = RunOverrides {
        out: cmd.out,
        repo: cmd.repo,
        branch: cmd.branch,
        spa_dir: cmd.spa_dir,
        force: cmd.force,
        no_rebuild: cmd.no_rebuild,
    };
    let resolved = resolve_publish(&cmd.vault_root, &overrides).map_err(CliError::Io)?;
    let summary = run_publish(
        &cmd.vault_root,
        &cmd.date,
        &resolved,
        overrides.force,
        overrides.no_rebuild,
    )
    .map_err(CliError::Io)?;

    println!(
        "publish: {} api file(s) → {} (sources={} durable={})",
        summary.file_count,
        std::path::Path::new(&summary.out_dir).join("api").display(),
        summary.sources,
        summary.claims
    );
    if summary.content_unchanged {
        println!("publish: durable content unchanged since last publish");
    }
    if summary.spa_copied {
        println!("publish: copied SPA bundle");
    } else {
        println!(
            "publish: no SPA bundle configured; wrote API JSON only \
             (build with VITE_OVP_STATIC=1 and set --spa-dir or `spa_dir` in .ovp/publish.toml)"
        );
    }
    match (&summary.deployed_to, summary.pushed) {
        (Some(repo), Some(true)) => println!("publish: pushed to {repo} ({})", resolved.branch),
        (Some(repo), _) => println!(
            "publish: no change to deploy for {repo} ({})",
            resolved.branch
        ),
        (None, _) => {}
    }
    Ok(())
}
