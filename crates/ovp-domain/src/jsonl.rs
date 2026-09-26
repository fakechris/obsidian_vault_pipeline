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
//!   last record. The next appender sees the ledger does not end in `"\n"`,
//!   closes the fragment with `"\n"` plus a [`TORN_MARKER`] line, and then
//!   writes its record. One torn record never corrupts the records after it,
//!   and the marker records WHERE the tear happened.
//!
//! What a reader does with a torn line is a per-ledger policy, [`TornLines`].
//! Under `Skip`, a truncated JSON prefix (serde `Category::Eof`) is skipped
//! ONLY when there is evidence it is a torn append: it is directly followed by
//! a marker line, or it is the final segment with no `"\n"` after it. An
//! EOF-classified line anywhere else may be real corruption and fails the read.

use std::fs::File;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::Path;

use serde::de::DeserializeOwned;

/// Line written by [`append_line`] right after a torn fragment it closed. Not
/// a record; every reader skips it.
pub const TORN_MARKER: &[u8] = br#"{"ovp_jsonl":"torn-line-above"}"#;

/// Append `line` plus `"\n"` to `file` (opened in append mode) in a single
/// write. If the ledger at `path` does not end in `"\n"` (a torn tail), the
/// write first closes the fragment with `"\n"` and a [`TORN_MARKER`] line. The
/// tail probe is a separate read. Racing appenders that both see the torn tail
/// each add a marker, and the second one lands after a complete record, where
/// it is harmless.
pub fn append_line(file: &mut File, path: &Path, line: &str) -> std::io::Result<()> {
    let mut buf = Vec::with_capacity(line.len() + TORN_MARKER.len() + 3);
    if !ends_with_newline_or_empty(path)? {
        buf.push(b'\n');
        buf.extend_from_slice(TORN_MARKER);
        buf.push(b'\n');
    }
    buf.extend_from_slice(line.as_bytes());
    buf.push(b'\n');
    write_once(file, &buf)
}

/// ONE `write(2)` of the whole buffer. Unlike `write_all`, a short write is
/// NOT retried: the retry would be a second write that a concurrent appender
/// can land in front of. The partial record is left as a torn tail (the next
/// append closes it with a marker) and the caller gets an error. Only
/// `Interrupted`, which writes nothing, is retried.
pub fn write_once(file: &mut File, buf: &[u8]) -> std::io::Result<()> {
    loop {
        match file.write(buf) {
            Ok(n) if n == buf.len() => return Ok(()),
            Ok(n) => {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::WriteZero,
                    format!("short write: {n} of {} bytes (disk full?)", buf.len()),
                ));
            }
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e),
        }
    }
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

/// How a reader treats a line that is a truncated JSON prefix.
pub enum TornLines<F: FnMut(usize)> {
    /// Machine-written, high-rate ledgers (intake, evolve): one torn append
    /// must not stop every run. The line is skipped and its 1-based number is
    /// reported through the callback.
    Skip(F),
    /// Ledgers of human input (crystal patches): fail loud, as for any other
    /// malformed line, rather than silently dropping a correction.
    Fail,
}

/// Parse a whole JSONL ledger from raw bytes. Blank lines are skipped, torn
/// lines follow `torn`, and any other malformed line fails the whole read.
/// Works on bytes, so a record torn mid-way through a multi-byte UTF-8
/// character is classified like any other torn record.
pub fn parse_ledger<T: DeserializeOwned, F: FnMut(usize)>(
    raw: &[u8],
    mut torn: TornLines<F>,
) -> Result<Vec<T>, BadLine> {
    let lines: Vec<&[u8]> = raw.split(|b| *b == b'\n').collect();
    let blank = |l: &[u8]| l.iter().all(u8::is_ascii_whitespace);
    let mut records = Vec::new();
    for (i, line) in lines.iter().enumerate() {
        if blank(line) || *line == TORN_MARKER {
            continue;
        }
        match serde_json::from_slice(line) {
            Ok(rec) => records.push(rec),
            Err(error) if error.classify() == serde_json::error::Category::Eof => {
                // `split` yields a final segment after the last "\n"; a
                // non-blank one is an unterminated tail.
                let unterminated_tail = i + 1 == lines.len();
                let closed_by_marker = lines.get(i + 1) == Some(&TORN_MARKER);
                match &mut torn {
                    TornLines::Skip(report) if unterminated_tail || closed_by_marker => {
                        report(i + 1)
                    }
                    _ => return Err(BadLine { line: i + 1, error }),
                }
            }
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
        let r = parse_ledger(raw, TornLines::Skip(|l| torn.push(l)));
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

        // Before the next append, the torn tail is the unterminated final segment.
        let (r, torn) = parse(&std::fs::read(&path).unwrap());
        assert_eq!(r.unwrap().len(), 1);
        assert_eq!(torn, [2]);

        append(&path, &rec("三"));
        let raw = std::fs::read(&path).unwrap();
        assert!(raw.windows(TORN_MARKER.len()).any(|w| w == TORN_MARKER));
        let (r, torn) = parse(&raw);
        let titles: Vec<_> = r.unwrap().into_iter().map(|r| r.title).collect();
        assert_eq!(titles, ["一", "三"]);
        assert_eq!(torn, [2]);
    }

    #[test]
    fn terminated_truncated_line_without_marker_still_fails() {
        // EOF-classified but newline-terminated and not closed by a marker:
        // no evidence of a torn append, so it may be real corruption.
        let raw = format!("{}\n{{\"title\":\n{}\n", rec("a"), rec("b"));
        let (r, torn) = parse(raw.as_bytes());
        assert_eq!(r.unwrap_err().line, 2);
        assert!(torn.is_empty());
    }

    #[test]
    fn fail_policy_rejects_a_torn_line() {
        let raw = format!("{}\n{{\"title\": \"x", rec("a"));
        let r: Result<Vec<Rec>, _> = parse_ledger(raw.as_bytes(), TornLines::<fn(usize)>::Fail);
        assert_eq!(r.unwrap_err().line, 2);
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
