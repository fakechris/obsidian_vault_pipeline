//! Raise the `ovp2` main-thread stack on Windows.
//!
//! Windows reserves **1 MB** of stack for a process's main thread (it is baked
//! into the PE header at link time); Linux and macOS give it 8 MB. `ovp2`'s
//! clap command tree is one function per `augment_subcommands` expansion, and
//! in a DEBUG build that frame alone overruns 1 MB — the binary dies with
//! `thread 'main' has overflowed its stack` before `main` runs a single line.
//!
//! This is how it showed up: every `ovp-cli` integration test that spawns the
//! CLI failed on `windows-latest` with an EMPTY stdout and a non-zero exit —
//! indistinguishable, from the caller's side, from a command that ran and did
//! nothing. Unit tests all passed, because libtest runs each test on a spawned
//! thread whose stack std sizes independently of the main thread's.
//!
//! Reserve is address space, not committed memory, so 16 MB costs nothing until
//! it is touched. 16 rather than 8 because the overflow is a debug-build
//! phenomenon and debug frames are the fat ones — matching Unix's 8 MB exactly
//! would leave the configuration that actually broke with no headroom.
//!
//! `rustc-link-arg-bins` scopes this to binary targets; nothing about the
//! library builds or the non-Windows targets changes.
fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    stamp_provenance();

    // The TARGET, not the host — this file also runs when cross-compiling.
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }

    const STACK_BYTES: usize = 16 * 1024 * 1024;
    if std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc") {
        // link.exe
        println!("cargo:rustc-link-arg-bins=/STACK:{STACK_BYTES}");
    } else {
        // gnu targets link through the gcc driver, so ld needs -Wl.
        println!("cargo:rustc-link-arg-bins=-Wl,--stack,{STACK_BYTES}");
    }
}

/// Stamp the binary with the commit it was built from.
///
/// This repo ships FOUR independently-built copies of the same code — the app
/// sidecar, the desktop shell, the vault's portal copy, and a dev
/// `target/release` build — and CLAUDE.md names "changed A but only rebuilt B"
/// as the most expensive time sink here: the symptom is "my change did
/// nothing" while the code, the tests and the build are all green. A binary
/// that cannot say which commit it came from makes that undiagnosable.
///
/// Absent or unusable git is NOT an error: a release tarball has no `.git`,
/// and the build must still work. It stamps `unknown` and the reader reports
/// that honestly rather than guessing.
fn stamp_provenance() {
    let sha = git(&["rev-parse", "--short=12", "HEAD"]).unwrap_or_else(|| "unknown".into());
    // Dirty means the artifact contains code that is in NO commit, so its sha
    // is a lower bound on what is in it, not an identity.
    let dirty = match git(&["status", "--porcelain", "--untracked-files=no"]) {
        Some(s) if !s.is_empty() => "1",
        Some(_) => "0",
        None => "unknown",
    };
    println!("cargo:rustc-env=OVP2_GIT_SHA={sha}");
    println!("cargo:rustc-env=OVP2_GIT_DIRTY={dirty}");

    // Rebuild when HEAD moves. Without these a `cargo build` after a commit
    // reuses the cached crate and stamps the PREVIOUS sha — a provenance that
    // lies is worse than none.
    if let Some(dir) = git(&["rev-parse", "--git-dir"]) {
        let dir = std::path::PathBuf::from(dir);
        for p in ["HEAD", "refs"] {
            let path = dir.join(p);
            if path.exists() {
                println!("cargo:rerun-if-changed={}", path.display());
            }
        }
    }
}

fn git(args: &[&str]) -> Option<String> {
    let out = std::process::Command::new("git").args(args).output().ok()?;
    if !out.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&out.stdout).trim().to_string())
}
