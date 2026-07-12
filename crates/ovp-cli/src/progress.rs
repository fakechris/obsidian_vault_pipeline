//! Flushed progress output for long-running CLI loops.
//!
//! Daily / crystal / index runs are watched through nohup, pipes, or launchd,
//! where stdout is **block-buffered** — a `println!` inside a multi-minute
//! per-item loop is invisible until the process exits (or its buffer fills).
//! A healthy 46-minute run was once killed as "hung" because nothing showed.
//!
//! The fix is a write-then-flush say/sayln pair. These macros began life as a
//! local macro in `daily.rs`; they now live here so every command (and the
//! per-iteration streaming below) shares one blessed shape.
//!
//! stdout vs stderr: under launchd both are captured, but stderr is
//! line-buffered and reaches the log sooner, so warnings / heartbeats that
//! must survive a hard kill use the `*_err` variants.
//!
//! Leaf/domain crates must NOT print (see `check_architecture.sh` layering).
//! When a long loop lives inside a library crate, thread a progress **callback**
//! from the CLI — `run_daily_with_progress(on_source)` is the blessed shape —
//! and let the CLI render the flushed `[i/total]` line via `sayln!` in the
//! callback closure. `build_index_with_progress` and `AutoRun::sweep_with_progress`
//! follow that pattern.

use std::io::Write;

/// `print!` + explicit stdout flush. No trailing newline (use for in-place
/// status that a later [`sayln!`] completes).
#[macro_export]
macro_rules! say {
    ($($arg:tt)*) => {{
        print!($($arg)*);
        let _ = std::io::Write::flush(&mut std::io::stdout());
    }};
}

/// `println!` + explicit stdout flush. The workhorse for phase headers and
/// per-item `[i/total] …` lines that must hit the log the moment they print.
#[macro_export]
macro_rules! sayln {
    ($($arg:tt)*) => {{
        println!($($arg)*);
        let _ = std::io::Write::flush(&mut std::io::stdout());
    }};
}

/// `eprint!` + explicit stderr flush. stderr is line-buffered under launchd, so
/// this reaches the log soonest — use for heartbeats that must survive a kill.
#[macro_export]
macro_rules! say_err {
    ($($arg:tt)*) => {{
        eprint!($($arg)*);
        let _ = std::io::Write::flush(&mut std::io::stderr());
    }};
}

/// `eprintln!` + explicit stderr flush. For warnings and progress that must be
/// visible even when stdout is buffered away by nohup/launchd.
#[macro_export]
macro_rules! sayln_err {
    ($($arg:tt)*) => {{
        eprintln!($($arg)*);
        let _ = std::io::Write::flush(&mut std::io::stderr());
    }};
}

/// Write `line` + newline to `w` and flush. This is the write-then-flush shape
/// the say/sayln macros expand to, factored out so it can be unit-tested against
/// an in-memory buffer (the macros themselves always target the real std
/// streams and cannot be asserted directly). Exercised only by tests today.
#[cfg_attr(not(test), allow(dead_code))]
pub fn writeln_flushed<W: Write>(w: &mut W, line: &str) -> std::io::Result<()> {
    writeln!(w, "{line}")?;
    w.flush()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn writeln_flushed_writes_and_terminates() {
        let mut buf: Vec<u8> = Vec::new();
        writeln_flushed(&mut buf, "hello world").unwrap();
        assert_eq!(String::from_utf8(buf).unwrap(), "hello world\n");
    }

    #[test]
    fn writeln_flushed_flushes_a_tracking_writer() {
        // A writer that records whether flush() was actually called — proves the
        // helper does not merely buffer (the whole point under nohup/launchd).
        struct Tracking {
            bytes: Vec<u8>,
            flushed: bool,
        }
        impl Write for Tracking {
            fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
                self.bytes.extend_from_slice(b);
                Ok(b.len())
            }
            fn flush(&mut self) -> std::io::Result<()> {
                self.flushed = true;
                Ok(())
            }
        }
        let mut w = Tracking { bytes: Vec::new(), flushed: false };
        writeln_flushed(&mut w, "line").unwrap();
        assert!(w.flushed, "writeln_flushed must flush the writer");
        assert_eq!(String::from_utf8(w.bytes).unwrap(), "line\n");
    }
}
