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

    /// Processed-inbox directory for a given `YYYY-MM`.
    pub fn processed_dir(&self, month: &str) -> String {
        format!("50-Inbox/03-Processed/{month}")
    }

    /// Derived knowledge-index artifact (vault-relative). Lives under the
    /// logs tree because it is rebuildable, not authoritative.
    pub fn knowledge_index(&self) -> VaultPath {
        VaultPath::new("60-Logs/knowledge-index.json")
    }

    /// Daily reader-pack product directory (vault-relative, M30):
    /// `40-Resources/Reader/<YYYY-MM-DD>_<title>-<hash8>/`. Date-stamped per
    /// `OVP_RULES.md`; the content-hash suffix makes the directory stable per
    /// source content (same bytes → same dir) and collision-free across
    /// same-title sources. Returned as a `String` because it is a directory,
    /// not a note path.
    pub fn reader_pack_dir(&self, date: &str, title: &str, content_hash8: &str) -> String {
        format!(
            "40-Resources/Reader/{date}_{title}-{content_hash8}",
            title = truncate_chars(&sanitize_filename(title), 60),
        )
    }

    /// Durable daily-run ledger (vault-relative, append-only JSONL). The
    /// authoritative dedup + audit state for the `daily` loop; lives under
    /// `.ovp/` with the rest of the vault's operational state.
    pub fn daily_ledger(&self) -> &'static str {
        ".ovp/daily-runs.jsonl"
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
}

/// Truncate to at most `max` characters on a char boundary (titles can be
/// long and multi-byte; a byte slice could panic mid-codepoint).
fn truncate_chars(s: &str, max: usize) -> String {
    s.chars().take(max).collect::<String>().trim_end().to_string()
}

/// Extract the `YYYY-MM` prefix from a `YYYY-MM-DD` date string. Falls
/// back to the whole string if it's shorter than 7 chars.
fn year_month(date: &str) -> &str {
    date.get(..7).unwrap_or(date)
}

/// Replace characters that are illegal or troublesome in vault filenames
/// with a space, then trim. Mirrors the legacy sink behavior so existing
/// paths are byte-identical.
fn sanitize_filename(s: &str) -> String {
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
