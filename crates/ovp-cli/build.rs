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
