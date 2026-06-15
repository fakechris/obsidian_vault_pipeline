//! GitHub enrichment: fetch repository metadata + README for GitHub URLs.
//!
//! Follows the same trait pattern as web_fetch and PinboardFetch:
//! - `GitHubFetch` trait = effect boundary
//! - `FixtureGitHubFetch` = offline fixture for tests
//! - `LiveGitHubFetch` = behind `github-live` feature, uses reqwest blocking

use std::path::PathBuf;

/// Metadata fetched from a GitHub repository.
#[derive(Debug, Clone)]
pub struct RepoMetadata {
    pub owner: String,
    pub repo: String,
    pub full_name: String,
    pub description: Option<String>,
    pub language: Option<String>,
    pub stars: u64,
    pub topics: Vec<String>,
    pub default_branch: String,
    pub html_url: String,
}

/// Result of fetching a single GitHub repo.
#[derive(Debug, Clone)]
pub struct GitHubFetchResult {
    pub url: String,
    pub metadata: Option<RepoMetadata>,
    pub readme_content: Option<String>,
    pub error: Option<String>,
    pub fetched_at: String,
}

/// The GitHub fetch effect boundary.
pub trait GitHubFetch {
    fn fetch_repo(&mut self, owner: &str, repo: &str) -> GitHubFetchResult;
    fn origin(&self) -> String;
}

/// Offline fixture fetcher: serves pre-recorded responses from a directory.
///
/// Layout: `<fixture_dir>/<owner>__<repo>.json` where each file is a
/// JSON object with `metadata` and `readme_content` fields.
pub struct FixtureGitHubFetch {
    fixture_dir: PathBuf,
}

impl FixtureGitHubFetch {
    pub fn new(fixture_dir: impl Into<PathBuf>) -> Self {
        Self {
            fixture_dir: fixture_dir.into(),
        }
    }
}

impl GitHubFetch for FixtureGitHubFetch {
    fn fetch_repo(&mut self, owner: &str, repo: &str) -> GitHubFetchResult {
        let filename = format!("{}__{}.json", owner, repo);
        let path = self.fixture_dir.join(&filename);
        let now = chrono_now_iso();

        if !path.exists() {
            return GitHubFetchResult {
                url: format!("https://github.com/{owner}/{repo}"),
                metadata: None,
                readme_content: None,
                error: Some(format!("fixture not found: {filename}")),
                fetched_at: now,
            };
        }

        match std::fs::read_to_string(&path) {
            Ok(json) => match serde_json::from_str::<FixturePayload>(&json) {
                Ok(payload) => GitHubFetchResult {
                    url: format!("https://github.com/{owner}/{repo}"),
                    metadata: Some(RepoMetadata {
                        owner: owner.to_string(),
                        repo: repo.to_string(),
                        full_name: format!("{owner}/{repo}"),
                        description: payload.description,
                        language: payload.language,
                        stars: payload.stars.unwrap_or(0),
                        topics: payload.topics.unwrap_or_default(),
                        default_branch: payload.default_branch.unwrap_or_else(|| "main".into()),
                        html_url: format!("https://github.com/{owner}/{repo}"),
                    }),
                    readme_content: payload.readme_content,
                    error: None,
                    fetched_at: now,
                },
                Err(e) => GitHubFetchResult {
                    url: format!("https://github.com/{owner}/{repo}"),
                    metadata: None,
                    readme_content: None,
                    error: Some(format!("fixture parse error: {e}")),
                    fetched_at: now,
                },
            },
            Err(e) => GitHubFetchResult {
                url: format!("https://github.com/{owner}/{repo}"),
                metadata: None,
                readme_content: None,
                error: Some(format!("fixture read error: {e}")),
                fetched_at: now,
            },
        }
    }

    fn origin(&self) -> String {
        format!("fixture:{}", self.fixture_dir.display())
    }
}

#[cfg(feature = "github-live")]
pub struct LiveGitHubFetch {
    client: reqwest::blocking::Client,
    token: Option<String>,
    last_fetch: Option<std::time::Instant>,
    rate_limit_ms: u64,
}

#[cfg(feature = "github-live")]
impl LiveGitHubFetch {
    pub fn from_env() -> Result<Self, String> {
        let token = std::env::var("GITHUB_TOKEN").ok();
        let client = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(30))
            .user_agent("ovp-enrich/0.1")
            .build()
            .map_err(|e| format!("failed to build HTTP client: {e}"))?;
        Ok(Self {
            client,
            token,
            last_fetch: None,
            rate_limit_ms: 1000,
        })
    }

    fn rate_limit_wait(&mut self) {
        if let Some(last) = self.last_fetch {
            let elapsed = last.elapsed().as_millis() as u64;
            if elapsed < self.rate_limit_ms {
                std::thread::sleep(std::time::Duration::from_millis(
                    self.rate_limit_ms - elapsed,
                ));
            }
        }
        self.last_fetch = Some(std::time::Instant::now());
    }

    fn api_get(&self, endpoint: &str) -> Result<String, String> {
        let mut req = self.client.get(endpoint);
        req = req.header("Accept", "application/vnd.github.v3+json");
        if let Some(token) = &self.token {
            req = req.header("Authorization", format!("Bearer {token}"));
        }
        let resp = req.send().map_err(|e| format!("request failed: {e}"))?;
        if !resp.status().is_success() {
            return Err(format!("HTTP {}: {}", resp.status(), endpoint));
        }
        resp.text().map_err(|e| format!("body read error: {e}"))
    }
}

#[cfg(feature = "github-live")]
impl GitHubFetch for LiveGitHubFetch {
    fn fetch_repo(&mut self, owner: &str, repo: &str) -> GitHubFetchResult {
        self.rate_limit_wait();
        let now = chrono_now_iso();
        let api_url = format!("https://api.github.com/repos/{owner}/{repo}");

        let metadata = match self.api_get(&api_url) {
            Ok(body) => match serde_json::from_str::<serde_json::Value>(&body) {
                Ok(v) => Some(RepoMetadata {
                    owner: owner.to_string(),
                    repo: repo.to_string(),
                    full_name: v["full_name"].as_str().unwrap_or("").to_string(),
                    description: v["description"].as_str().map(|s| s.to_string()),
                    language: v["language"].as_str().map(|s| s.to_string()),
                    stars: v["stargazers_count"].as_u64().unwrap_or(0),
                    topics: v["topics"]
                        .as_array()
                        .map(|arr| {
                            arr.iter()
                                .filter_map(|t| t.as_str().map(String::from))
                                .collect()
                        })
                        .unwrap_or_default(),
                    default_branch: v["default_branch"]
                        .as_str()
                        .unwrap_or("main")
                        .to_string(),
                    html_url: v["html_url"].as_str().unwrap_or("").to_string(),
                }),
                Err(e) => {
                    return GitHubFetchResult {
                        url: format!("https://github.com/{owner}/{repo}"),
                        metadata: None,
                        readme_content: None,
                        error: Some(format!("JSON parse error: {e}")),
                        fetched_at: now,
                    };
                }
            },
            Err(e) => {
                return GitHubFetchResult {
                    url: format!("https://github.com/{owner}/{repo}"),
                    metadata: None,
                    readme_content: None,
                    error: Some(e),
                    fetched_at: now,
                };
            }
        };

        self.rate_limit_wait();
        let readme_url = format!(
            "https://api.github.com/repos/{owner}/{repo}/readme"
        );
        let readme_content = match self.api_get(&readme_url) {
            Ok(body) => {
                match serde_json::from_str::<serde_json::Value>(&body) {
                    Ok(v) => {
                        if let Some(encoded) = v["content"].as_str() {
                            let cleaned: String =
                                encoded.chars().filter(|c| !c.is_whitespace()).collect();
                            use sha2::Digest;
                            // GitHub returns base64-encoded content
                            let _ = sha2::Sha256::new(); // ensure import used
                            decode_base64(&cleaned).ok()
                        } else {
                            None
                        }
                    }
                    Err(_) => None,
                }
            }
            Err(_) => None,
        };

        GitHubFetchResult {
            url: format!("https://github.com/{owner}/{repo}"),
            metadata,
            readme_content,
            error: None,
            fetched_at: now,
        }
    }

    fn origin(&self) -> String {
        if self.token.is_some() {
            "github-api:authenticated".to_string()
        } else {
            "github-api:anonymous".to_string()
        }
    }
}

// --- Enrichment orchestration ---

/// Result of enriching a single GitHub source.
#[derive(Debug)]
pub struct GitHubEnrichResult {
    pub url: String,
    pub owner: String,
    pub repo: String,
    pub fetch: GitHubFetchResult,
    /// Whether a vault note was written.
    pub written: bool,
    /// Path of the written note (if any).
    pub note_path: Option<String>,
}

/// Parse a GitHub repo URL into (owner, repo). Returns None for non-repo URLs
/// (issues, PRs, blob paths, gist, etc.).
pub fn parse_github_repo_url(url: &str) -> Option<(String, String)> {
    let url = url.trim().trim_end_matches('/');
    let path = if let Some(rest) = url.strip_prefix("https://github.com/") {
        rest
    } else if let Some(rest) = url.strip_prefix("http://github.com/") {
        rest
    } else {
        return None;
    };

    let segments: Vec<&str> = path.split('/').filter(|s| !s.is_empty()).collect();
    if segments.len() != 2 {
        return None;
    }

    let owner = segments[0];
    let repo = segments[1];

    if owner.is_empty()
        || repo.is_empty()
        || owner.starts_with('.')
        || repo.starts_with('.')
    {
        return None;
    }

    Some((owner.to_string(), repo.to_string()))
}

/// Enrich GitHub repo URLs: fetch metadata + README, write vault notes.
///
/// `sources` is a list of `(vault-relative-path, url)` tuples from `needs_content`
/// items that have been identified as GitHub repo URLs.
///
/// Returns results for each attempted enrichment.
pub fn enrich_github_repos(
    fetcher: &mut dyn GitHubFetch,
    vault_root: &std::path::Path,
    sources: &[(String, String)],
) -> Vec<GitHubEnrichResult> {
    sources
        .iter()
        .filter_map(|(rel_path, url)| {
            let (owner, repo) = parse_github_repo_url(url)?;
            let result = fetcher.fetch_repo(&owner, &repo);

            let (written, note_path) = if result.error.is_none() {
                match write_github_note(vault_root, rel_path, &result) {
                    Ok(path) => (true, Some(path)),
                    Err(_) => (false, None),
                }
            } else {
                (false, None)
            };

            Some(GitHubEnrichResult {
                url: url.clone(),
                owner,
                repo,
                fetch: result,
                written,
                note_path,
            })
        })
        .collect()
}

/// Write/update the source file with GitHub repo content.
/// Preserves frontmatter, replaces body with README + metadata header.
fn write_github_note(
    vault_root: &std::path::Path,
    rel_path: &str,
    result: &GitHubFetchResult,
) -> Result<String, String> {
    let abs_path = vault_root.join(rel_path);
    let existing = std::fs::read_to_string(&abs_path)
        .map_err(|e| format!("read error: {e}"))?;

    let (frontmatter, _old_body) = split_frontmatter(&existing);
    let meta = result.metadata.as_ref().ok_or("no metadata")?;

    let mut body = String::new();

    // Title
    body.push_str(&format!("# {}\n\n", meta.full_name));

    // Metadata block
    if let Some(desc) = &meta.description {
        body.push_str(&format!("> {desc}\n\n"));
    }
    let mut info_parts = Vec::new();
    if let Some(lang) = &meta.language {
        info_parts.push(format!("Language: {lang}"));
    }
    info_parts.push(format!("Stars: {}", meta.stars));
    if !meta.topics.is_empty() {
        info_parts.push(format!("Topics: {}", meta.topics.join(", ")));
    }
    if !info_parts.is_empty() {
        body.push_str(&info_parts.join(" | "));
        body.push_str("\n\n---\n\n");
    }

    // README content
    if let Some(readme) = &result.readme_content {
        let truncated = if readme.len() > 8000 {
            &readme[..8000]
        } else {
            readme.as_str()
        };
        body.push_str(truncated);
        if readme.len() > 8000 {
            body.push_str("\n\n*(README truncated)*");
        }
    }
    body.push('\n');

    // Reassemble with updated frontmatter
    let mut output = String::new();
    if let Some(fm) = frontmatter {
        let updated_fm = update_frontmatter_github(fm, &result.fetched_at, meta);
        output.push_str("---\n");
        output.push_str(&updated_fm);
        output.push_str("\n---\n\n");
    } else {
        output.push_str(&format!(
            "---\nurl: {}\nfetched_at: {}\nsource_type: github_repo\n---\n\n",
            meta.html_url, result.fetched_at
        ));
    }
    output.push_str(&body);

    std::fs::write(&abs_path, &output)
        .map_err(|e| format!("write error: {e}"))?;

    Ok(rel_path.to_string())
}

fn update_frontmatter_github(fm: &str, fetched_at: &str, meta: &RepoMetadata) -> String {
    let mut lines: Vec<String> = fm.lines().map(|l| l.to_string()).collect();
    let mut has_fetched = false;
    let mut has_source_type = false;
    for line in &mut lines {
        if line.starts_with("fetched_at:") {
            *line = format!("fetched_at: {fetched_at}");
            has_fetched = true;
        }
        if line.starts_with("source_type:") {
            *line = "source_type: github_repo".to_string();
            has_source_type = true;
        }
    }
    if !has_fetched {
        lines.push(format!("fetched_at: {fetched_at}"));
    }
    if !has_source_type {
        lines.push("source_type: github_repo".to_string());
    }
    if !lines.iter().any(|l| l.starts_with("github_stars:")) {
        lines.push(format!("github_stars: {}", meta.stars));
    }
    if !lines.iter().any(|l| l.starts_with("github_language:")) {
        if let Some(lang) = &meta.language {
            lines.push(format!("github_language: {lang}"));
        }
    }
    lines.join("\n")
}

// --- Helpers ---

fn split_frontmatter(content: &str) -> (Option<&str>, &str) {
    if !content.starts_with("---") {
        return (None, content);
    }
    let after_first = &content[3..];
    let close = after_first.find("\n---");
    match close {
        Some(idx) => {
            let fm = after_first[..idx].trim_start_matches('\n');
            let body_start = 3 + idx + 4; // "---" + \n + "---"
            let body = if body_start < content.len() {
                &content[body_start..]
            } else {
                ""
            };
            (Some(fm), body.trim_start_matches('\n'))
        }
        None => (None, content),
    }
}

fn chrono_now_iso() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    format!("{now}")
}

#[cfg(feature = "github-live")]
fn decode_base64(input: &str) -> Result<String, String> {
    // Simple base64 decoder (GitHub README content)
    let alphabet = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut bytes = Vec::new();
    let chars: Vec<u8> = input.bytes().filter(|b| *b != b'=').collect();

    for chunk in chars.chunks(4) {
        let mut buf = 0u32;
        let mut count = 0;
        for &b in chunk {
            if let Some(pos) = alphabet.iter().position(|&a| a == b) {
                buf = (buf << 6) | pos as u32;
                count += 1;
            }
        }
        buf <<= (4 - count) * 6;
        let out_bytes = count * 6 / 8;
        for i in 0..out_bytes {
            bytes.push(((buf >> (16 - i * 8)) & 0xFF) as u8);
        }
    }

    String::from_utf8(bytes).map_err(|e| format!("utf8 decode error: {e}"))
}

/// Fixture payload format for `FixtureGitHubFetch`.
#[derive(serde::Deserialize)]
struct FixturePayload {
    description: Option<String>,
    language: Option<String>,
    stars: Option<u64>,
    topics: Option<Vec<String>>,
    default_branch: Option<String>,
    readme_content: Option<String>,
}

// --- Tests ---

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn parse_github_repo_url_valid() {
        assert_eq!(
            parse_github_repo_url("https://github.com/rust-lang/rust"),
            Some(("rust-lang".into(), "rust".into()))
        );
        assert_eq!(
            parse_github_repo_url("https://github.com/tokio-rs/tokio/"),
            Some(("tokio-rs".into(), "tokio".into()))
        );
    }

    #[test]
    fn parse_github_repo_url_rejects_non_repo() {
        assert_eq!(
            parse_github_repo_url("https://github.com/rust-lang/rust/issues/123"),
            None
        );
        assert_eq!(
            parse_github_repo_url("https://github.com/rust-lang/rust/pull/456"),
            None
        );
        assert_eq!(
            parse_github_repo_url("https://github.com/rust-lang"),
            None
        );
        assert_eq!(
            parse_github_repo_url("https://gitlab.com/foo/bar"),
            None
        );
    }

    #[test]
    fn fixture_github_fetch_missing_file() {
        let tmp = tempfile::tempdir().unwrap();
        let mut fetcher = FixtureGitHubFetch::new(tmp.path());
        let result = fetcher.fetch_repo("foo", "bar");
        assert!(result.error.is_some());
        assert!(result.metadata.is_none());
    }

    #[test]
    fn fixture_github_fetch_success() {
        let tmp = tempfile::tempdir().unwrap();
        let payload = r##"{
            "description": "A test repo",
            "language": "Rust",
            "stars": 42,
            "topics": ["cli", "tool"],
            "default_branch": "main",
            "readme_content": "# Hello\n\nThis is a test README."
        }"##;
        fs::write(tmp.path().join("foo__bar.json"), payload).unwrap();

        let mut fetcher = FixtureGitHubFetch::new(tmp.path());
        let result = fetcher.fetch_repo("foo", "bar");
        assert!(result.error.is_none());
        let meta = result.metadata.unwrap();
        assert_eq!(meta.stars, 42);
        assert_eq!(meta.language.as_deref(), Some("Rust"));
        assert_eq!(meta.topics, vec!["cli", "tool"]);
        assert_eq!(result.readme_content.as_deref(), Some("# Hello\n\nThis is a test README."));
    }

    #[test]
    fn enrich_github_repos_writes_note() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path();

        // Create fixture dir
        let fixture_dir = vault.join(".fixtures");
        fs::create_dir_all(&fixture_dir).unwrap();
        let payload = r##"{
            "description": "Blazing fast",
            "language": "Rust",
            "stars": 1000,
            "topics": ["async"],
            "default_branch": "main",
            "readme_content": "# My Project\n\nHello world"
        }"##;
        fs::write(fixture_dir.join("owner__repo.json"), payload).unwrap();

        // Create source file (needs-content)
        let source_dir = vault.join("50-Inbox");
        fs::create_dir_all(&source_dir).unwrap();
        let source_file = source_dir.join("test.md");
        fs::write(
            &source_file,
            "---\nurl: https://github.com/owner/repo\ntitle: owner/repo\n---\n\nhttps://github.com/owner/repo\n",
        ).unwrap();

        let mut fetcher = FixtureGitHubFetch::new(&fixture_dir);
        let results = enrich_github_repos(
            &mut fetcher,
            vault,
            &[("50-Inbox/test.md".into(), "https://github.com/owner/repo".into())],
        );

        assert_eq!(results.len(), 1);
        assert!(results[0].written);

        let content = fs::read_to_string(&source_file).unwrap();
        assert!(content.contains("# owner/repo"));
        assert!(content.contains("Blazing fast"));
        assert!(content.contains("Stars: 1000"));
        assert!(content.contains("# My Project"));
        assert!(content.contains("source_type: github_repo"));
    }

    #[test]
    fn enrich_skips_non_github_urls() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path();
        let fixture_dir = vault.join(".fixtures");
        fs::create_dir_all(&fixture_dir).unwrap();

        let mut fetcher = FixtureGitHubFetch::new(&fixture_dir);
        let results = enrich_github_repos(
            &mut fetcher,
            vault,
            &[("some/file.md".into(), "https://example.com/article".into())],
        );

        assert!(results.is_empty());
    }
}
