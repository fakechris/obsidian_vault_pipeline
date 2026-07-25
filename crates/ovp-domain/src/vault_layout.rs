use ovp_core::VaultPath;

/// The single source of vault path conventions (PARA directory layout +
/// filename rules). Pure and root-agnostic: every method returns a
/// **vault-relative** `VaultPath`; the applier joins it under the vault
/// root. Lives in `ovp-domain`, not `ovp-core`, because the PARA layout
/// (`20-Areas/AI-Research`, `_深度解读.md`, ...) is Obsidian/domain
/// knowledge that invariant #1 keeps out of the kernel.
///
/// Before this type existed, the article sink built its path inline.
/// Centralizing here means paper notes (v1.2), evergreen writes (L3),
/// and MOC files (L4) all agree on the layout without re-deriving it.
#[derive(Debug, Clone, Default)]
pub struct VaultLayout;

impl VaultLayout {
    pub fn new() -> Self {
        Self
    }

    /// Map a PARA area key (`ai`, `tools`, ...) to its directory name.
    /// Unknown areas pass through unchanged so a new area doesn't silently
    /// vanish into a wrong directory.
    pub fn area_dir(&self, area: &str) -> String {
        match area {
            "ai" => "AI-Research",
            "tools" => "Tools",
            "investing" => "Investing",
            "programming" => "Programming",
            other => other,
        }
        .to_string()
    }

    /// Area deep-dive note (the article path):
    /// `20-Areas/<area-dir>/Topics/<YYYY-MM>/<YYYY-MM-DD>_<title>_深度解读.md`.
    /// Consumed by `ArticleVaultPlanSink`.
    pub fn area_topic_note(&self, area: &str, date: &str, title: &str) -> VaultPath {
        let month = year_month(date);
        VaultPath::new(format!(
            "20-Areas/{area_dir}/Topics/{month}/{date}_{title}_深度解读.md",
            area_dir = self.area_dir(area),
            month = month,
            date = date,
            title = sanitize_filename(title),
        ))
    }

    /// Paper deep-dive note:
    /// `20-Areas/AI-Research/Papers/<YYYY-MM-DD>_<arxiv-id>_<title>_深度解读.md`.
    /// Consumed by the paper sink (v1.2). Papers live under AI-Research
    /// regardless of area, matching the legacy `20-Areas/AI-Research/Papers/`.
    pub fn paper_note(&self, date: &str, arxiv_id: &str, title: &str) -> VaultPath {
        VaultPath::new(format!(
            "20-Areas/AI-Research/Papers/{date}_{id}_{title}_深度解读.md",
            date = date,
            id = sanitize_filename(arxiv_id),
            title = sanitize_filename(title),
        ))
    }

    /// Evergreen atomic note: `10-Knowledge/Evergreen/<slug>.md`.
    /// Consumed by the absorb/evergreen writer (L3).
    pub fn evergreen_note(&self, slug: &str) -> VaultPath {
        VaultPath::new(format!("{}/{}.md", self.evergreen_dir(), sanitize_filename(slug)))
    }

    /// Directory (vault-relative) holding evergreen notes. Used to recognize an
    /// evergreen `VaultCreate` for same-slug reconcile (L4).
    pub fn evergreen_dir(&self) -> &'static str {
        "10-Knowledge/Evergreen"
    }

    /// Atlas MOC file: `10-Knowledge/Atlas/<name>.md`.
    /// Consumed by the MOC materializer (L4).
    pub fn atlas_moc(&self, name: &str) -> VaultPath {
        VaultPath::new(format!("10-Knowledge/Atlas/{}.md", sanitize_filename(name)))
    }

    /// Inbox raw-capture directory (vault-relative). The directory a real
    /// intake source sweeps. Returned as a plain `&str` because it's a
    /// directory, not a note path.
    pub fn inbox_raw_dir(&self) -> &'static str {
        "50-Inbox/01-Raw"
    }

    /// Root of the processed-inbox tree (all months, vault-relative). The
    /// directory the index backfill sweeps when joining ledger-less corpus
    /// packs back to their source files.
    pub fn processed_root(&self) -> &'static str {
        "50-Inbox/03-Processed"
    }

    /// Processed-inbox directory for a given `YYYY-MM`.
    pub fn processed_dir(&self, month: &str) -> String {
        format!("{}/{month}", self.processed_root())
    }

    /// Where duplicate captures are parked (M31 intake): content/URL already
    /// known, so the file is moved out of the capture dirs but NEVER deleted
    /// (OVP_RULES). Month-bucketed like `processed_dir`.
    pub fn duplicates_dir(&self, month: &str) -> String {
        format!("50-Inbox/03-Processed/duplicates/{month}")
    }

    /// The capture directories the M31 intake sweep reads, vault-relative, in
    /// sweep order: Obsidian Web Clipper drops into `Clippings/`; manual
    /// captures into `50-Inbox/00-Capture`; pinboard-sync materializes into
    /// `50-Inbox/02-Pinboard`.
    pub fn capture_dirs(&self) -> [&'static str; 3] {
        ["Clippings", "50-Inbox/00-Capture", "50-Inbox/02-Pinboard"]
    }

    /// Where pinboard-sync materializes bookmark notes (vault-relative).
    pub fn pinboard_dir(&self) -> &'static str {
        "50-Inbox/02-Pinboard"
    }

    /// Normalized raw-inbox filename for an ingested capture:
    /// `<YYYY-MM-DD>_<title>-<hash8>.md` (date-stamped per OVP_RULES; the
    /// content-hash suffix keeps same-title captures distinct and ties the
    /// filename to the dedup identity).
    pub fn normalized_source_name(&self, date: &str, title: &str, content_hash8: &str) -> String {
        format!(
            "{date}_{title}-{content_hash8}.md",
            title = truncate_chars(&sanitize_filename(title), 60),
        )
    }

    /// Derived knowledge-index artifact (vault-relative). Lives under the
    /// logs tree because it is rebuildable, not authoritative.
    pub fn knowledge_index(&self) -> VaultPath {
        VaultPath::new("60-Logs/knowledge-index.json")
    }

    /// Root of the reader-pack product surface (vault-relative directory).
    pub fn reader_root(&self) -> &'static str {
        "40-Resources/Reader"
    }

    /// Daily reader-pack product directory (vault-relative, M30):
    /// `40-Resources/Reader/<YYYY-MM-DD>_<title>-<hash8>/`. Date-stamped per
    /// `OVP_RULES.md`; the content-hash suffix makes the directory stable per
    /// source content (same bytes → same dir) and collision-free across
    /// same-title sources. Returned as a `String` because it is a directory,
    /// not a note path.
    pub fn reader_pack_dir(&self, date: &str, title: &str, content_hash8: &str) -> String {
        format!(
            "{root}/{date}_{title}-{content_hash8}",
            root = self.reader_root(),
            title = truncate_chars(&sanitize_filename(title), 60),
        )
    }

    /// Durable daily-run ledger (vault-relative, append-only JSONL). The
    /// authoritative dedup + audit state for the `daily` loop; lives under
    /// `.ovp/` with the rest of the vault's operational state.
    pub fn daily_ledger(&self) -> &'static str {
        ".ovp/daily-runs.jsonl"
    }

    /// Append-only record of each `ovp2 publish` run (content hash, index
    /// provenance, published_at) — the change-detection authority that lets a
    /// scheduled publish skip a no-op push.
    pub fn publish_ledger(&self) -> &'static str {
        ".ovp/publish.jsonl"
    }

    /// The vault's write-operation log mandated by `OVP_RULES.md` ("Always log
    /// every write operation to 60-Logs/pipeline.jsonl").
    pub fn pipeline_log(&self) -> &'static str {
        "60-Logs/pipeline.jsonl"
    }

    /// Cassette root for the daily loop's model calls (vault-local, never in
    /// the repo).
    pub fn daily_cassette_dir(&self) -> &'static str {
        ".ovp/cassettes/daily"
    }

    /// Durable intake ledger (vault-relative, append-only JSONL, M31): one
    /// record per capture-file disposition (ingested / duplicate /
    /// needs_content / unparseable).
    pub fn intake_ledger(&self) -> &'static str {
        ".ovp/intake.jsonl"
    }

    /// Durable pinboard-sync ledger (vault-relative, append-only JSONL, M31):
    /// one record per bookmark materialized; the URL-dedup authority.
    pub fn pinboard_ledger(&self) -> &'static str {
        ".ovp/pinboard-sync.jsonl"
    }

    /// Per-run durable report directory (`<run_id>.json`, M31).
    pub fn reports_dir(&self) -> &'static str {
        ".ovp/reports"
    }

    /// The persisted read-model file the `find` command queries (M31).
    pub fn index_file(&self) -> &'static str {
        ".ovp/index/index.json"
    }

    /// Unconditional run-liveness heartbeat (OVP2 observability P0). Written at
    /// the START of every `daily` invocation (`status: running`) and overwritten
    /// with a terminal status (`completed` / `failed` / `aborted`) as the run
    /// ends — so an unattended run that crashes before its end-of-run report is
    /// still visible to the operator. Overwrite is CORRECT: it is a single
    /// liveness snapshot, not an append-only ledger.
    pub fn last_run_file(&self) -> &'static str {
        ".ovp/last-run.json"
    }

    /// The product console directory (static HTML over product state, M31).
    pub fn console_dir(&self) -> &'static str {
        ".ovp/console"
    }

    /// The vault-local durable Crystal store directory (M31 product home for
    /// what `crystal-write --store` produces: ledger.jsonl + crystal.md +
    /// review.json).
    pub fn crystal_store_dir(&self) -> &'static str {
        ".ovp/crystal"
    }

    /// The operator-owned tag alias table (alias → canonical), applied at
    /// projection build time only — raw frontmatter tags are never rewritten.
    pub fn tag_aliases_file(&self) -> &'static str {
        ".ovp/tags/aliases.toml"
    }

    /// Machine-inferred tags for untagged sources (`tags-suggest` output) —
    /// a rebuildable projection, kept strictly apart from operator tags.
    pub fn tags_inferred_file(&self) -> &'static str {
        ".ovp/tags/inferred.json"
    }

    /// The human-review report `tags-suggest` writes: merge candidates with
    /// evidence + a paste-ready `[aliases]` block. Never read by the pipeline.
    pub fn tags_proposals_file(&self) -> &'static str {
        ".ovp/tags/proposals.md"
    }

    /// The closed tag vocabulary the classifier picks from (`tags-bootstrap`
    /// rebuilds user/community entries; llm entries persist; operator-curable).
    pub fn tags_vocabulary_file(&self) -> &'static str {
        ".ovp/tags/vocabulary.toml"
    }

    /// UI-recorded curation decisions (accepted merges + rejected pairs).
    /// MACHINE-owned — the curation endpoints rewrite it freely — and merged
    /// with the operator-owned `aliases.toml` at load time, so accepting a
    /// proposal in the portal never rewrites (or eats the comments of) the
    /// hand-edited file.
    pub fn tags_decisions_file(&self) -> &'static str {
        ".ovp/tags/decisions.toml"
    }

    /// Machine-readable twin of `proposals.md` (same merge candidates) — the
    /// curation inbox reads this; the md stays the human-review artifact.
    pub fn tags_proposals_json_file(&self) -> &'static str {
        ".ovp/tags/proposals.json"
    }
}

/// Last path segment of a pack dir — the case_id that claim↔source↔theme
/// joins key on. One shared implementation (both separators) so every
/// consumer agrees; an inline `rsplit('/')` would silently miss Windows
/// paths and break the join.
pub fn pack_case_id(pack_dir: &str) -> &str {
    pack_dir.rsplit(['/', '\\']).next().unwrap_or(pack_dir)
}

/// Lifecycle-move fallback: `rel_path` often records the INTAKE location
/// (`50-Inbox/01-Raw/<month>/…`) while the daily lifecycle step has moved the
/// processed source to `50-Inbox/03-Processed/<month>/…` keeping the trailing
/// subpath. When the recorded path misses and sits under the raw inbox dir,
/// return the processed-dir candidate iff it exists. One implementation for
/// every reader/writer (`rel` must already be traversal-checked).
pub fn lifecycle_moved_path(
    vault_root: &std::path::Path,
    layout: &VaultLayout,
    rel: &str,
) -> Option<std::path::PathBuf> {
    let raw_prefix = format!("{}/", layout.inbox_raw_dir());
    let rest = rel.strip_prefix(&raw_prefix)?;
    let (month, file) = rest.split_once('/')?;
    let candidate = vault_root.join(layout.processed_dir(month)).join(file);
    candidate.is_file().then_some(candidate)
}

/// Truncate to at most `max` characters on a char boundary (titles can be
/// long and multi-byte; a byte slice could panic mid-codepoint). Public so
/// intake filename normalization (M31) agrees with pack-dir naming.
pub fn truncate_chars(s: &str, max: usize) -> String {
    s.chars().take(max).collect::<String>().trim_end().to_string()
}

/// Extract the `YYYY-MM` prefix from a `YYYY-MM-DD` date string. Falls
/// back to the whole string if it's shorter than 7 chars.
fn year_month(date: &str) -> &str {
    date.get(..7).unwrap_or(date)
}

/// Replace characters that are illegal or troublesome in vault filenames
/// with a space, then trim. Mirrors the legacy sink behavior so existing
/// paths are byte-identical. Public so intake normalization (M31) shares
/// the exact same rules as note/pack naming.
pub fn sanitize_filename(s: &str) -> String {
    s.chars()
        .map(|c| {
            if matches!(c, '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|') {
                ' '
            } else {
                c
            }
        })
        .collect::<String>()
        .trim()
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn area_topic_note_matches_legacy_convention() {
        let l = VaultLayout::new();
        let p = l.area_topic_note("ai", "2026-05-27", "Agent-native PM");
        assert_eq!(
            p.as_str(),
            "20-Areas/AI-Research/Topics/2026-05/2026-05-27_Agent-native PM_深度解读.md"
        );
    }

    #[test]
    fn area_dir_mapping() {
        let l = VaultLayout::new();
        assert_eq!(l.area_dir("ai"), "AI-Research");
        assert_eq!(l.area_dir("tools"), "Tools");
        assert_eq!(l.area_dir("investing"), "Investing");
        assert_eq!(l.area_dir("programming"), "Programming");
        assert_eq!(l.area_dir("custom"), "custom");
    }

    #[test]
    fn sanitizes_illegal_chars_in_title() {
        let l = VaultLayout::new();
        let p = l.area_topic_note("ai", "2026-05-27", "a/b:c?d");
        assert!(p.as_str().contains("a b c d"), "got {}", p.as_str());
        assert!(!p.as_str().contains("a/b"));
    }

    #[test]
    fn paper_note_under_ai_research_papers() {
        let l = VaultLayout::new();
        let p = l.paper_note("2026-01-16", "2601.11144", "Deep GraphRAG");
        assert_eq!(
            p.as_str(),
            "20-Areas/AI-Research/Papers/2026-01-16_2601.11144_Deep GraphRAG_深度解读.md"
        );
    }

    #[test]
    fn evergreen_and_atlas_paths() {
        let l = VaultLayout::new();
        assert_eq!(l.evergreen_note("ai-agent").as_str(), "10-Knowledge/Evergreen/ai-agent.md");
        assert_eq!(l.atlas_moc("MOC-AI-Research").as_str(), "10-Knowledge/Atlas/MOC-AI-Research.md");
    }

    #[test]
    fn inbox_dirs() {
        let l = VaultLayout::new();
        assert_eq!(l.inbox_raw_dir(), "50-Inbox/01-Raw");
        assert_eq!(l.processed_root(), "50-Inbox/03-Processed");
        assert_eq!(l.processed_dir("2026-05"), "50-Inbox/03-Processed/2026-05");
    }

    #[test]
    fn reader_pack_dir_is_dated_sanitized_and_hash_suffixed() {
        let l = VaultLayout::new();
        assert_eq!(
            l.reader_pack_dir("2026-06-09", "Agent Memory: A/B", "a1b2c3d4"),
            "40-Resources/Reader/2026-06-09_Agent Memory  A B-a1b2c3d4"
        );
    }

    #[test]
    fn reader_pack_dir_truncates_long_titles_on_char_boundary() {
        let l = VaultLayout::new();
        let long = "深".repeat(80);
        let dir = l.reader_pack_dir("2026-06-09", &long, "a1b2c3d4");
        assert!(dir.contains(&"深".repeat(60)));
        assert!(!dir.contains(&"深".repeat(61)));
    }

    #[test]
    fn daily_state_paths() {
        let l = VaultLayout::new();
        assert_eq!(l.daily_ledger(), ".ovp/daily-runs.jsonl");
        assert_eq!(l.pipeline_log(), "60-Logs/pipeline.jsonl");
        assert_eq!(l.daily_cassette_dir(), ".ovp/cassettes/daily");
    }
}
