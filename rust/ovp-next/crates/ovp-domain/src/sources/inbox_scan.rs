use std::path::{Path, PathBuf};

use ovp_core::{
    FilterError, Record, RecordId, RecordMeta, RunId, Source, SourceOutput, StepId,
};

use crate::body::DomainBody;

use super::markdown_inbox::{read_source_doc, record_id_for};

/// Real intake: sweeps a directory for `*.md` clippings and emits one
/// `Record<DomainBody::Source>` per file, one per `produce()` tick, in
/// deterministic (sorted-by-filename) order. This is the directory-level
/// counterpart to `MarkdownInboxSource` (single file).
///
/// The whole-directory scan happens once on the first `produce()` call;
/// after that each tick pops the next file. When the queue drains the
/// source reports `Exhausted`.
///
/// Error semantics (consistent with `MarkdownInboxSource`): a read/parse
/// failure on any file surfaces as `SourceOutput::Error`, which the runner
/// logs and treats as the end of production. v1 is fail-fast on a
/// malformed inbox; a skip-and-continue variant is a future refinement
/// (it needs a per-file skip event the `SourceOutput` enum doesn't carry
/// yet).
pub struct InboxScanSource {
    step: StepId,
    run_id: RunId,
    dir: PathBuf,
    /// Remaining files, reverse-sorted so `pop()` yields ascending order.
    pending: Vec<PathBuf>,
    scanned: bool,
    next_seq: u64,
}

impl InboxScanSource {
    pub fn new(step: impl Into<String>, run_id: RunId, dir: impl Into<PathBuf>) -> Self {
        Self {
            step: StepId::new(step.into()),
            run_id,
            dir: dir.into(),
            pending: Vec::new(),
            scanned: false,
            next_seq: 0,
        }
    }

    fn scan(dir: &Path) -> Result<Vec<PathBuf>, FilterError> {
        let entries = std::fs::read_dir(dir).map_err(|e| {
            FilterError::new(
                "source.inbox_scan.io",
                format!("read_dir {}: {e}", dir.display()),
            )
        })?;
        let mut files: Vec<PathBuf> = Vec::new();
        for entry in entries {
            let entry = entry.map_err(|e| {
                FilterError::new("source.inbox_scan.io", format!("dir entry: {e}"))
            })?;
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) == Some("md") {
                files.push(path);
            }
        }
        // Sort ascending by full path for determinism, then reverse so
        // `pop()` returns the lowest-sorted file first.
        files.sort();
        files.reverse();
        Ok(files)
    }
}

impl Source<DomainBody> for InboxScanSource {
    fn step_id(&self) -> &StepId {
        &self.step
    }

    fn produce(&mut self) -> SourceOutput<DomainBody> {
        if !self.scanned {
            self.scanned = true;
            match Self::scan(&self.dir) {
                Ok(files) => self.pending = files,
                Err(e) => return SourceOutput::Error(e),
            }
        }

        let path = match self.pending.pop() {
            Some(p) => p,
            None => return SourceOutput::Exhausted,
        };

        match read_source_doc(&path) {
            Ok(doc) => {
                let seq = self.next_seq;
                self.next_seq += 1;
                let rec = Record::new(
                    RecordId::new(record_id_for(&path)),
                    DomainBody::Source(Box::new(doc)),
                    RecordMeta { run_id: self.run_id.clone(), seq },
                )
                .with_step(self.step.clone(), "ingested (scan)");
                SourceOutput::Records(vec![rec])
            }
            Err(e) => SourceOutput::Error(e),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(dir: &Path, name: &str, body: &str) {
        std::fs::write(dir.join(name), body).unwrap();
    }

    fn clipping(title: &str) -> String {
        format!("---\ntitle: \"{title}\"\nsource: \"https://example.com/{title}\"\n---\n\nbody of {title}\n")
    }

    fn collect(src: &mut InboxScanSource) -> (Vec<String>, bool) {
        // Drive the source to exhaustion, returning the record ids in
        // emission order and whether it ended cleanly (Exhausted).
        let mut ids = Vec::new();
        loop {
            match src.produce() {
                SourceOutput::Records(rs) => {
                    for r in rs {
                        ids.push(r.id.as_str().to_string());
                    }
                }
                SourceOutput::Exhausted => return (ids, true),
                SourceOutput::Error(_) => return (ids, false),
            }
        }
    }

    #[test]
    fn sweeps_md_files_in_sorted_order_one_per_tick() {
        let tmp = tempfile::tempdir().unwrap();
        write(tmp.path(), "b.md", &clipping("Bee"));
        write(tmp.path(), "a.md", &clipping("Ay"));
        write(tmp.path(), "c.md", &clipping("Cee"));
        // A non-md file is ignored.
        write(tmp.path(), "notes.txt", "ignore me");

        let mut src = InboxScanSource::new("inbox_scan", RunId::new("r"), tmp.path());

        // First tick yields exactly one record (the lowest-sorted: a.md).
        match src.produce() {
            SourceOutput::Records(rs) => {
                assert_eq!(rs.len(), 1);
                assert_eq!(rs[0].id.as_str(), "src-a");
            }
            other => panic!("expected one record, got {other:?}"),
        }

        // Remaining ticks: b then c, then Exhausted.
        let (rest, clean) = collect(&mut src);
        assert!(clean);
        assert_eq!(rest, vec!["src-b", "src-c"]);
    }

    #[test]
    fn empty_dir_is_immediately_exhausted() {
        let tmp = tempfile::tempdir().unwrap();
        let mut src = InboxScanSource::new("inbox_scan", RunId::new("r"), tmp.path());
        assert!(matches!(src.produce(), SourceOutput::Exhausted));
    }

    #[test]
    fn missing_dir_errors() {
        let mut src = InboxScanSource::new(
            "inbox_scan",
            RunId::new("r"),
            "/this/does/not/exist",
        );
        match src.produce() {
            SourceOutput::Error(e) => assert_eq!(e.code.as_str(), "source.inbox_scan.io"),
            other => panic!("expected Error, got {other:?}"),
        }
    }

    #[test]
    fn seq_increments_across_files() {
        let tmp = tempfile::tempdir().unwrap();
        write(tmp.path(), "a.md", &clipping("Ay"));
        write(tmp.path(), "b.md", &clipping("Bee"));
        let mut src = InboxScanSource::new("inbox_scan", RunId::new("r"), tmp.path());

        let first = match src.produce() {
            SourceOutput::Records(rs) => rs[0].meta.seq,
            other => panic!("got {other:?}"),
        };
        let second = match src.produce() {
            SourceOutput::Records(rs) => rs[0].meta.seq,
            other => panic!("got {other:?}"),
        };
        assert_eq!(first, 0);
        assert_eq!(second, 1);
    }
}
