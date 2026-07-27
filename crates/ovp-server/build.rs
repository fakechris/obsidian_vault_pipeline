//! Embed the git hash + build instant so every running surface can answer
//! "which version am I?" (operator finding: undiagnosable stale builds).
fn main() {
    let git = std::process::Command::new("git")
        .args(["rev-parse", "--short=9", "HEAD"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|| "unknown".into());
    let dirty = std::process::Command::new("git")
        .args(["status", "--porcelain"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| !o.stdout.is_empty())
        .unwrap_or(false);
    println!(
        "cargo:rustc-env=OVP_GIT_HASH={git}{}",
        if dirty { "+dirty" } else { "" }
    );
    let built = std::process::Command::new("date")
        .args(["-u", "+%Y-%m-%dT%H:%M:%SZ"])
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|| "unknown".into());
    println!("cargo:rustc-env=OVP_BUILD_TIME={built}");
    // rerun-if-changed REPLACES Cargo's default tracking — list the crate
    // sources back in, plus the git state the stamp derives from (HEAD for
    // branch switches, the RESOLVED branch ref for commits — commits move
    // the ref, not HEAD — and the index for staged/dirty transitions).
    println!("cargo:rerun-if-changed=src");
    println!("cargo:rerun-if-changed=Cargo.toml");
    println!("cargo:rerun-if-changed=../../.git/HEAD");
    println!("cargo:rerun-if-changed=../../.git/index");
    if let Ok(head) = std::fs::read_to_string("../../.git/HEAD")
        && let Some(reference) = head.trim().strip_prefix("ref: ")
    {
        println!("cargo:rerun-if-changed=../../.git/{reference}");
    }
}
