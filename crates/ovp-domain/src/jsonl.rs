//! Crash- and concurrency-safe JSONL append/read primitives shared by every
//! ledger (`ovp_intake::vaultops::append_jsonl`, the crystal patch ledger).
//!
//! Two rules, checked by `docs/tla/LedgerAppend.tla` (`LedgerAppendPrefix*.cfg`):
//!
//! - **One write per record.** `writeln!(f, "{line}")` on an unbuffered `File`
//!   issues TWO `write(2)` calls, the record and then `"\n"`. A SIGKILL between
//!   them, or a concurrent appender landing in between, leaves `}{` or blank
//!   lines behind. Empirically, 3 processes × 20k appends produced 7,146 joined
//!   lines. A single `O_APPEND` write lands whole.
//! - **A torn tail is never glued to.** Power loss can persist a prefix of the
//!   last record. The next appender starts its record on a fresh line, and the
//!   reader skips a line that is a truncated JSON prefix (serde `Category::Eof`).
//!   Only a torn, never-acknowledged write produces one. Any other malformed
//!   line is still a hard error.

use std::fs::File;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::Path;

use serde::de::DeserializeOwned;

/// Append `line` plus `"\n"` to `file` (opened in append mode) in a single
/// write. If the ledger at `path` does not end in `"\n"` (a torn tail), the
/// record is prefixed with `"\n"` so it starts on its own line. The tail probe
/// is a separate read. Racing appenders that both see a torn tail each add a
/// newline, which leaves only a harmless blank line.
pub fn append_line(file: &mut File, path: &Path, line: &str) -> std::io::Result<()> {
    let mut buf = Vec::with_capacity(line.len() + 2);
    if !ends_with_newline_or_empty(path)? {
        buf.push(b'\n');
    }
    buf.extend_from_slice(line.as_bytes());
    buf.push(b'\n');
    file.write_all(&buf)
}

fn ends_with_newline_or_empty(path: &Path) -> std::io::Result<bool> {
    let mut f = File::open(path)?;
    if f.metadata()?.len() == 0 {
        return Ok(true);
    }
    f.seek(SeekFrom::End(-1))?;
    let mut last = [0u8; 1];
    f.read_exact(&mut last)?;
    Ok(last[0] == b'\n')
}

/// A ledger line that could not be parsed.
#[derive(Debug)]
pub struct BadLine {
    /// 1-based line number.
    pub line: usize,
    pub error: serde_json::Error,
}

/// Parse a whole JSONL ledger from raw bytes. Blank lines are skipped. A line
/// that is a truncated JSON prefix (a torn, never-acknowledged append) is
/// skipped and reported through `on_torn`. Any other malformed line fails the
/// whole read. Works on bytes, so a record torn mid-way through a multi-byte
/// UTF-8 character cannot fail the read of the whole file.
pub fn parse_ledger<T: DeserializeOwned>(
    raw: &[u8],
    mut on_torn: impl FnMut(usize),
) -> Result<Vec<T>, BadLine> {
    let mut records = Vec::new();
    for (i, line) in raw.split(|b| *b == b'\n').enumerate() {
        if line.iter().all(u8::is_ascii_whitespace) {
            continue;
        }
        match serde_json::from_slice(line) {
            Ok(rec) => records.push(rec),
            Err(e) if e.classify() == serde_json::error::Category::Eof => on_torn(i + 1),
            Err(error) => return Err(BadLine { line: i + 1, error }),
        }
    }
    Ok(records)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs::OpenOptions;

    #[derive(Debug, PartialEq, serde::Deserialize, serde::Serialize)]
    struct Rec {
        title: String,
    }

    fn rec(t: &str) -> String {
        serde_json::to_string(&Rec { title: t.into() }).unwrap()
    }

    fn append(path: &Path, line: &str) {
        let mut f = OpenOptions::new().create(true).append(true).open(path).unwrap();
        append_line(&mut f, path, line).unwrap();
    }

    fn parse(raw: &[u8]) -> (Result<Vec<Rec>, BadLine>, Vec<usize>) {
        let mut torn = Vec::new();
        let r = parse_ledger(raw, |l| torn.push(l));
        (r, torn)
    }

    #[test]
    fn torn_tail_is_skipped_and_never_glued_to() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("l.jsonl");
        append(&path, &rec("一"));
        // Power loss mid-record: a prefix cut inside a multi-byte character.
        let full = rec("二二二");
        let cut = full.len() - 4;
        let mut f = OpenOptions::new().append(true).open(&path).unwrap();
        f.write_all(&full.as_bytes()[..cut]).unwrap();
        drop(f);
        append(&path, &rec("三"));

        let raw = std::fs::read(&path).unwrap();
        let (r, torn) = parse(&raw);
        let titles: Vec<_> = r.unwrap().into_iter().map(|r| r.title).collect();
        assert_eq!(titles, ["一", "三"]);
        assert_eq!(torn, [2]);
    }

    #[test]
    fn corrupt_line_that_is_not_a_prefix_still_fails() {
        let raw = format!("{}\n{{\"title\":1}}{}\n", rec("a"), rec("b"));
        let (r, torn) = parse(raw.as_bytes());
        assert_eq!(r.unwrap_err().line, 2);
        assert!(torn.is_empty());
    }

    #[test]
    fn concurrent_appenders_never_produce_malformed_lines() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("l.jsonl");
        std::fs::write(&path, "").unwrap();
        let threads: Vec<_> = (0..4)
            .map(|t| {
                let path = path.clone();
                std::thread::spawn(move || {
                    let mut f = OpenOptions::new().append(true).open(&path).unwrap();
                    for i in 0..2000 {
                        append_line(&mut f, &path, &rec(&format!("{t}-{i}"))).unwrap();
                    }
                })
            })
            .collect();
        for t in threads {
            t.join().unwrap();
        }
        let raw = std::fs::read(&path).unwrap();
        let (r, torn) = parse(&raw);
        assert_eq!(r.unwrap().len(), 8000);
        assert!(torn.is_empty());
    }
}
