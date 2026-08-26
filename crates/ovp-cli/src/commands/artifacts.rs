//! `artifacts` — which build is each installed copy actually running?
//!
//! This repo ships FOUR independently-built copies of the same code:
//!
//! | artifact | rebuilt by |
//! |---|---|
//! | app sidecar (`OVP2.app/Contents/MacOS/ovp2`) | `build-desktop-sidecar.sh` |
//! | desktop shell (`ovp2-desktop`) | full `tauri build` |
//! | vault portal copy (`<vault>/.ovp/console/app`) | `deploy-portal.sh` |
//! | dev build (`target/release/ovp2`) | `cargo build --release` |
//!
//! CLAUDE.md names "changed A but only rebuilt B" the most expensive time sink
//! in this repo: the symptom is that a change did nothing, while the code, the
//! tests and the build are all green. The scheduler runs the SIDECAR, the
//! portal API is inside the DESKTOP SHELL, and the portal HTML may come from
//! the vault copy — so the thing you rebuilt is very often not the thing you
//! are looking at.
//!
//! This reports what each copy says it was built from. It never rebuilds
//! anything and never writes: knowing which one is behind is the whole job.

use std::path::{Path, PathBuf};

use crate::CliError;

/// The commit THIS binary was stamped with at build time (see `build.rs`).
pub const GIT_SHA: &str = env!("OVP2_GIT_SHA");
pub const GIT_DIRTY: &str = env!("OVP2_GIT_DIRTY");

/// Budget for one `--version` probe.
const PROBE_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(3);

pub struct ArtifactsArgs {
    pub vault_root: PathBuf,
    /// Where the installed app lives. Defaults per-platform.
    pub app: Option<PathBuf>,
    pub json: bool,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct ArtifactReport {
    /// Human label, e.g. "app sidecar".
    pub name: String,
    pub path: String,
    /// `present` | `missing` | `unreadable`
    pub state: String,
    /// Commit the artifact reports, when it can report one.
    pub sha: Option<String>,
    pub dirty: Option<bool>,
    /// For the portal copies: the entry asset the HTML actually references.
    pub entry: Option<String>,
}

fn default_app_dir() -> Option<PathBuf> {
    #[cfg(target_os = "macos")]
    {
        Some(PathBuf::from("/Applications/OVP2.app"))
    }
    #[cfg(target_os = "windows")]
    {
        // "OVP2 Desktop" — what the installer creates and what
        // build-desktop-sidecar.ps1 documents, not the bare "OVP2" in CLAUDE.md.
        std::env::var_os("LOCALAPPDATA").map(|p| PathBuf::from(p).join("OVP2 Desktop"))
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        None
    }
}

/// Presence only — never executed.
fn probe_presence(name: &str, path: &Path) -> ArtifactReport {
    ArtifactReport {
        name: name.into(),
        path: path.display().to_string(),
        state: if path.exists() { "present" } else { "missing" }.into(),
        sha: None,
        dirty: None,
        entry: None,
    }
}

/// Ask a binary what it was built from. `--version` is the stable surface; a
/// binary too old to carry provenance answers without it, which is reported as
/// `present` with no sha rather than an error.
///
/// ONLY for CLI binaries. Running a GUI binary's `--version` opens a window
/// and never returns — use [`probe_presence`] for those.
fn probe_binary(name: &str, path: &Path) -> ArtifactReport {
    let mut r = ArtifactReport {
        name: name.into(),
        path: path.display().to_string(),
        state: "missing".into(),
        sha: None,
        dirty: None,
        entry: None,
    };
    if !path.exists() {
        return r;
    }
    r.state = "present".into();
    let Some(text) = version_output(path) else {
        r.state = "unreadable".into();
        return r;
    };
    let (sha, dirty) = parse_version(&text);
    r.sha = sha;
    r.dirty = dirty;
    r
}

/// Pull the provenance out of `ovp2 <version> (<sha>[, dirty])`.
///
/// The contract between `main.rs`'s clap `version` string and this reader.
/// Loose on purpose: a format change must degrade to "no sha" rather than
/// yield a WRONG one, because a wrong sha reports all-clear on a stale copy.
pub fn parse_version(text: &str) -> (Option<String>, Option<bool>) {
    let Some(open) = text.find('(') else {
        return (None, None);
    };
    let inner = &text[open + 1..];
    let Some(close) = inner.find(')') else {
        return (None, None);
    };
    let inner = &inner[..close];
    let candidate = inner.split(',').next().map(str::trim).unwrap_or_default();
    // Validate the GRAMMAR, do not just take the first parenthesised text.
    // `ovp2 2.0.1 (release build)` previously yielded the sha "release build",
    // which is precisely the wrong-sha-reports-all-clear failure this contract
    // exists to prevent.
    let looks_like_stamp = candidate == "unknown"
        || (!candidate.is_empty()
            && candidate.len() <= 40
            && candidate.chars().all(|c| c.is_ascii_hexdigit()));
    if !looks_like_stamp {
        // No recognisable stamp means no opinion at all — including on dirty.
        return (None, None);
    }
    (
        Some(candidate.to_string()),
        Some(inner.contains("dirty")),
    )
}

/// Run `<path> --version` with a real deadline.
///
/// A wedged or interactive binary must not hang this command — the whole
/// point is to be runnable when something is already wrong. `wait_timeout` is
/// not in the dependency tree, so this polls `try_wait` and kills on expiry.
fn version_output(path: &Path) -> Option<String> {
    use std::process::{Command, Stdio};
    let mut child = Command::new(path)
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    // Read on a thread and bound the WHOLE operation. Polling `try_wait` and
    // then calling `wait_with_output` bounds only the child's exit: a
    // surviving grandchild that inherited the stdout pipe keeps it open, and
    // the read after the loop blocks forever with the deadline already spent.
    let mut stdout = child.stdout.take()?;
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        use std::io::Read;
        let mut buf = String::new();
        let _ = stdout.read_to_string(&mut buf);
        let _ = tx.send(buf);
    });
    let text = rx.recv_timeout(PROBE_TIMEOUT).ok();
    // Kill regardless: on the timeout path the child is still running, and on
    // the success path it has exited and this is a no-op that also reaps it.
    let _ = child.kill();
    let _ = child.wait();
    text
}

/// The entry asset an `index.html` references — the same signal
/// `deploy-portal.sh` verifies against a running server. Two portal copies
/// exist and the VAULT one wins per-file, so a rebuilt app bundle changes
/// nothing the operator sees.
fn probe_portal(name: &str, dir: &Path) -> ArtifactReport {
    let index = dir.join("index.html");
    let mut r = ArtifactReport {
        name: name.into(),
        path: dir.display().to_string(),
        state: "missing".into(),
        sha: None,
        dirty: None,
        entry: None,
    };
    if !index.exists() {
        return r;
    }
    r.state = "present".into();
    match std::fs::read_to_string(&index) {
        Ok(html) => r.entry = entry_asset(&html),
        Err(_) => r.state = "unreadable".into(),
    }
    r
}

/// First `assets/<name>.js` referenced by the HTML.
pub fn entry_asset(html: &str) -> Option<String> {
    let start = html.find("assets/")?;
    let rest = &html[start..];
    let end = rest.find(".js")? + 3;
    Some(rest[..end].to_string())
}

pub fn collect(vault_root: &Path, app: Option<PathBuf>) -> Vec<ArtifactReport> {
    let mut out = Vec::new();
    let app_dir = app.or_else(default_app_dir);

    if let Some(app_dir) = &app_dir {
        #[cfg(target_os = "macos")]
        let (sidecar, shell, bundled_portal) = (
            app_dir.join("Contents/MacOS/ovp2"),
            app_dir.join("Contents/MacOS/ovp2-desktop"),
            app_dir.join("Contents/Resources/console-ui/dist"),
        );
        // `ovp2-desktop.exe`, NOT `OVP2.exe`: tauri.conf.json sets no
        // `mainBinaryName`, so the shell keeps its Cargo bin name — and
        // Windows filenames are case-insensitive, so an `OVP2.exe` shell could
        // not coexist with the `ovp2.exe` sidecar in one directory. Checking
        // for `OVP2.exe` would have matched the SIDECAR and reported a shell
        // that is not there. See scripts/build-desktop-sidecar.ps1.
        #[cfg(not(target_os = "macos"))]
        let (sidecar, shell, bundled_portal) = (
            app_dir.join("ovp2.exe"),
            app_dir.join("ovp2-desktop.exe"),
            app_dir.join("console-ui/dist"),
        );
        out.push(probe_binary("app sidecar", &sidecar));
        // Presence ONLY. The first version of this called probe_binary here
        // and hung: `ovp2-desktop --version` is a Tauri app, so it opens a
        // window and never exits. The comment said presence-only while the
        // code spawned it anyway.
        out.push(probe_presence("desktop shell", &shell));
        out.push(probe_portal("portal (app bundle)", &bundled_portal));
    }

    out.push(probe_portal(
        "portal (vault copy — WINS)",
        &vault_root.join(".ovp/console/app"),
    ));
    // Anchored at the tree this binary was COMPILED from, not the caller's
    // cwd. A relative path missed the real dev build whenever `artifacts` ran
    // from anywhere else — and could have probed an unrelated
    // `target/release/ovp2` that happened to sit under it.
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../target/release")
        .join(if cfg!(windows) { "ovp2.exe" } else { "ovp2" });
    out.push(probe_binary("dev build", &dev));
    out
}

pub fn run(args: ArtifactsArgs) -> Result<(), CliError> {
    let reports = collect(&args.vault_root, args.app);
    let dirty = GIT_DIRTY == "1";

    if args.json {
        let body = serde_json::json!({
            "schema": "ovp.artifacts/v1",
            "this_binary": { "sha": GIT_SHA, "dirty": dirty },
            "artifacts": reports,
        });
        println!(
            "{}",
            serde_json::to_string_pretty(&body).map_err(|e| CliError::Io(e.to_string()))?
        );
        return Ok(());
    }

    println!(
        "this binary: {GIT_SHA}{}",
        if dirty { " (dirty)" } else { "" }
    );
    for r in &reports {
        let detail = match (&r.sha, &r.entry) {
            (Some(sha), _) => format!(
                "{sha}{}",
                if r.dirty == Some(true) { " (dirty)" } else { "" }
            ),
            (None, Some(entry)) => entry.clone(),
            _ => "—".into(),
        };
        println!("  {:<28} {:<10} {detail}", r.name, r.state);
        println!("  {:<28} {}", "", r.path);
    }

    // The comparison is the point. Anything that reports a sha and disagrees
    // with the running binary is a copy that will behave like an older commit.
    let behind: Vec<&ArtifactReport> = reports
        .iter()
        .filter(|r| r.sha.as_deref().is_some_and(|s| s != GIT_SHA))
        .collect();
    let portals: Vec<&ArtifactReport> = reports
        .iter()
        .filter(|r| r.entry.is_some())
        .collect();

    if !behind.is_empty() {
        println!();
        for r in &behind {
            println!(
                "  DIFFERENT BUILD: {} is {}, this binary is {GIT_SHA}",
                r.name,
                r.sha.as_deref().unwrap_or("—")
            );
        }
    }
    if portals.len() > 1 {
        let first = portals[0].entry.as_deref();
        if portals.iter().any(|p| p.entry.as_deref() != first) {
            println!();
            println!("  DIFFERENT BUILD: the two portal copies serve different entry assets.");
            println!("  The VAULT copy wins per-file, so rebuilding the app bundle changes");
            println!("  nothing you see. Run `scripts/deploy-portal.sh <vault>`.");
        }
    }
    if dirty {
        println!();
        println!("  NOTE: this binary was built from a dirty tree — its sha is a lower");
        println!("  bound on what is in it, not an identity.");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn entry_asset_pulls_the_first_js_reference() {
        let html = r#"<html><script type="module" src="/assets/index-Bjqc7rfs.js"></script>"#;
        assert_eq!(entry_asset(html).as_deref(), Some("assets/index-Bjqc7rfs.js"));
    }

    #[test]
    fn entry_asset_is_none_when_the_html_references_no_bundle() {
        // A placeholder or half-written index must read as "no entry" rather
        // than matching some unrelated substring.
        assert_eq!(entry_asset("<html><body>nothing</body></html>"), None);
        assert_eq!(entry_asset("assets/style.css"), None);
    }

    #[test]
    fn a_missing_artifact_is_reported_missing_not_skipped() {
        // Silence would read as "fine"; the whole point is naming the copy
        // that is not there.
        let r = probe_binary("x", Path::new("/no/such/ovp2"));
        assert_eq!(r.state, "missing");
        assert!(r.sha.is_none());
    }

    #[test]
    fn a_portal_dir_without_index_html_is_missing() {
        let d = tempfile::tempdir().unwrap();
        assert_eq!(probe_portal("p", d.path()).state, "missing");
    }

    #[test]
    fn a_portal_copy_reports_the_entry_it_actually_references() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(
            d.path().join("index.html"),
            r#"<script src="/assets/index-DEADBEEF.js"></script>"#,
        )
        .unwrap();
        let r = probe_portal("p", d.path());
        assert_eq!(r.state, "present");
        assert_eq!(r.entry.as_deref(), Some("assets/index-DEADBEEF.js"));
    }

    #[test]
    fn parse_version_reads_the_sha_and_the_dirty_flag() {
        assert_eq!(
            parse_version("ovp2 2.0.1 (805600800aa2)\n"),
            (Some("805600800aa2".into()), Some(false))
        );
        assert_eq!(
            parse_version("ovp2 2.0.1 (805600800aa2, dirty)\n"),
            (Some("805600800aa2".into()), Some(true))
        );
    }

    #[test]
    fn an_unstamped_or_reshaped_version_yields_no_sha_rather_than_a_wrong_one() {
        // A binary predating the stamp, and a hypothetical format change. A
        // WRONG sha would compare equal to nothing and report all-clear on a
        // stale copy — strictly worse than admitting ignorance.
        for text in [
            "ovp2 2.0.1\n",
            "ovp2 2.0.1 (\n",
            "",
            "ovp2 2.0.1 ()",
            // The one that mattered: any parenthesised text used to become a
            // "sha", and a wrong sha compares equal to nothing — reporting
            // all-clear on a stale copy.
            "ovp2 2.0.1 (release build)",
            "ovp2 2.0.1 (built by someone)",
        ] {
            assert_eq!(parse_version(text).0, None, "{text:?}");
        }
        // `unknown` is a real stamp: a build with no git says so.
        assert_eq!(parse_version("ovp2 2.0.1 (unknown)").0.as_deref(), Some("unknown"));
    }

    #[test]
    fn this_binary_carries_a_provenance_stamp() {
        // If build.rs stops stamping, every comparison below silently becomes
        // "unknown vs unknown" — which compares equal and reports all clear.
        assert!(!GIT_SHA.is_empty());
        assert!(matches!(GIT_DIRTY, "0" | "1" | "unknown"));
    }
}
