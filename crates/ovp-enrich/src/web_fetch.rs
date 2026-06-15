//! Web Fetcher: resolves `needs_content` bookmarks into full article text.
//!
//! Architecture follows the PinboardFetch pattern:
//! - `WebFetch` trait = effect boundary
//! - `FixtureWebFetch` = compile-time fixture for tests
//! - `LiveWebFetch` = behind `web-fetch-live` feature, uses reqwest blocking

use std::path::PathBuf;
use std::time::Duration;

/// Result of fetching a single URL.
#[derive(Debug, Clone)]
pub struct FetchResult {
    pub url: String,
    pub content: Option<String>,
    pub title: Option<String>,
    pub error: Option<String>,
    pub fetched_at: String,
}

/// Configuration for web fetching safety limits.
#[derive(Debug, Clone)]
pub struct WebFetchConfig {
    pub timeout: Duration,
    pub max_content_bytes: usize,
    pub rate_limit_ms: u64,
}

impl Default for WebFetchConfig {
    fn default() -> Self {
        Self {
            timeout: Duration::from_secs(30),
            max_content_bytes: 2 * 1024 * 1024, // 2 MB
            rate_limit_ms: 1000,
        }
    }
}

/// The fetch effect boundary: where web content comes from.
pub trait WebFetch {
    /// Fetch the readable text content from a URL.
    /// Returns `FetchResult` with content on success, error message on failure.
    fn fetch_readable(&mut self, url: &str) -> FetchResult;

    /// Human-readable origin description for run reports.
    fn origin(&self) -> String;
}

/// Offline fixture fetcher: serves pre-recorded responses from a directory.
///
/// Directory layout: `<fixture_dir>/<url_hash>.json` where each file is a
/// serialized `FetchResult`.
pub struct FixtureWebFetch {
    fixture_dir: PathBuf,
}

impl FixtureWebFetch {
    pub fn new(fixture_dir: impl Into<PathBuf>) -> Self {
        Self {
            fixture_dir: fixture_dir.into(),
        }
    }

    /// Create a fixture fetcher with a single pre-loaded response (for tests).
    pub fn with_response(fixture_dir: impl Into<PathBuf>, url: &str, content: &str) -> Self {
        let dir = fixture_dir.into();
        std::fs::create_dir_all(&dir).expect("create fixture dir");
        let hash = url_hash(url);
        let result = serde_json::json!({
            "url": url,
            "content": content,
            "title": null,
            "error": null,
            "fetched_at": "2025-01-01T00:00:00Z"
        });
        let path = dir.join(format!("{hash}.json"));
        std::fs::write(&path, serde_json::to_string_pretty(&result).unwrap())
            .expect("write fixture");
        Self { fixture_dir: dir }
    }
}

impl WebFetch for FixtureWebFetch {
    fn fetch_readable(&mut self, url: &str) -> FetchResult {
        let hash = url_hash(url);
        let path = self.fixture_dir.join(format!("{hash}.json"));
        if path.exists() {
            let raw = std::fs::read_to_string(&path).unwrap_or_default();
            if let Ok(val) = serde_json::from_str::<serde_json::Value>(&raw) {
                return FetchResult {
                    url: url.to_string(),
                    content: val["content"].as_str().map(|s| s.to_string()),
                    title: val["title"].as_str().map(|s| s.to_string()),
                    error: val["error"].as_str().map(|s| s.to_string()),
                    fetched_at: val["fetched_at"]
                        .as_str()
                        .unwrap_or("fixture")
                        .to_string(),
                };
            }
        }
        FetchResult {
            url: url.to_string(),
            content: None,
            title: None,
            error: Some(format!("no fixture for {url} (hash={hash})")),
            fetched_at: "fixture".to_string(),
        }
    }

    fn origin(&self) -> String {
        format!("fixture dir {}", self.fixture_dir.display())
    }
}

/// Live web fetcher. Compiled only with `--features web-fetch-live`.
#[cfg(feature = "web-fetch-live")]
pub struct LiveWebFetch {
    config: WebFetchConfig,
    client: reqwest::blocking::Client,
    last_fetch: Option<std::time::Instant>,
}

#[cfg(feature = "web-fetch-live")]
impl LiveWebFetch {
    pub fn new(config: WebFetchConfig) -> Result<Self, String> {
        let client = reqwest::blocking::Client::builder()
            .timeout(config.timeout)
            .user_agent("OVP-Next/1.0 (personal knowledge pipeline)")
            .redirect(reqwest::redirect::Policy::limited(5))
            .build()
            .map_err(|e| format!("building HTTP client: {e}"))?;
        Ok(Self {
            config,
            client,
            last_fetch: None,
        })
    }

    pub fn with_defaults() -> Result<Self, String> {
        Self::new(WebFetchConfig::default())
    }

    fn rate_limit_wait(&mut self) {
        if let Some(last) = self.last_fetch {
            let elapsed = last.elapsed();
            let min_gap = Duration::from_millis(self.config.rate_limit_ms);
            if elapsed < min_gap {
                std::thread::sleep(min_gap - elapsed);
            }
        }
    }
}

#[cfg(feature = "web-fetch-live")]
impl WebFetch for LiveWebFetch {
    fn fetch_readable(&mut self, url: &str) -> FetchResult {
        self.rate_limit_wait();
        self.last_fetch = Some(std::time::Instant::now());

        let now = chrono_now_iso();

        let response = match self.client.get(url).send() {
            Ok(r) => r,
            Err(e) => {
                return FetchResult {
                    url: url.to_string(),
                    content: None,
                    title: None,
                    error: Some(format!("request failed: {e}")),
                    fetched_at: now,
                };
            }
        };

        if !response.status().is_success() {
            return FetchResult {
                url: url.to_string(),
                content: None,
                title: None,
                error: Some(format!("HTTP {}", response.status())),
                fetched_at: now,
            };
        }

        let content_type = response
            .headers()
            .get("content-type")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();

        if !content_type.contains("text/html") && !content_type.contains("text/plain") {
            return FetchResult {
                url: url.to_string(),
                content: None,
                title: None,
                error: Some(format!("non-text content-type: {content_type}")),
                fetched_at: now,
            };
        }

        let body = match response.text() {
            Ok(t) => t,
            Err(e) => {
                return FetchResult {
                    url: url.to_string(),
                    content: None,
                    title: None,
                    error: Some(format!("reading body: {e}")),
                    fetched_at: now,
                };
            }
        };

        if body.len() > self.config.max_content_bytes {
            return FetchResult {
                url: url.to_string(),
                content: None,
                title: None,
                error: Some(format!(
                    "content too large: {} bytes (limit {})",
                    body.len(),
                    self.config.max_content_bytes
                )),
                fetched_at: now,
            };
        }

        let (title, readable) = extract_readable(&body);

        FetchResult {
            url: url.to_string(),
            content: Some(readable),
            title,
            error: None,
            fetched_at: now,
        }
    }

    fn origin(&self) -> String {
        "live web fetch".to_string()
    }
}

/// MVP readability extraction: strip HTML to plain text.
///
/// Current implementation: tag stripping + whitespace collapse + `<title>`
/// extraction. Sufficient for bare-bookmark→text capture; does NOT produce
/// article-quality extraction (no boilerplate removal, no content scoring).
///
/// Upgrade path: integrate `dom_smoothie` (or equivalent Rust readability
/// library) once dogfood confirms the need for higher-fidelity extraction.
pub fn extract_readable(html: &str) -> (Option<String>, String) {
    let title = extract_title(html);
    let text = strip_tags(html);
    (title, text)
}

pub fn extract_title(html: &str) -> Option<String> {
    let lower = html.to_lowercase();
    let start = lower.find("<title")?;
    let after_tag = html[start..].find('>')?;
    let content_start = start + after_tag + 1;
    let end = lower[content_start..].find("</title>")?;
    let title = html[content_start..content_start + end].trim();
    if title.is_empty() {
        None
    } else {
        Some(title.to_string())
    }
}

pub fn strip_tags(html: &str) -> String {
    let mut result = String::with_capacity(html.len());
    let mut in_tag = false;
    let mut in_script = false;
    let mut in_style = false;
    let lower = html.to_lowercase();
    let chars: Vec<char> = html.chars().collect();
    let lower_chars: Vec<char> = lower.chars().collect();

    let mut i = 0;
    while i < chars.len() {
        if !in_tag && chars[i] == '<' {
            in_tag = true;
            let remaining: String = lower_chars[i..].iter().take(10).collect();
            if remaining.starts_with("<script") {
                in_script = true;
            } else if remaining.starts_with("<style") {
                in_style = true;
            } else if remaining.starts_with("</script") {
                in_script = false;
            } else if remaining.starts_with("</style") {
                in_style = false;
            }
        } else if in_tag && chars[i] == '>' {
            in_tag = false;
            if !in_script && !in_style {
                result.push(' ');
            }
        } else if !in_tag && !in_script && !in_style {
            result.push(chars[i]);
        }
        i += 1;
    }

    collapse_whitespace(&result)
}

pub fn collapse_whitespace(s: &str) -> String {
    let mut result = String::with_capacity(s.len());
    let mut last_was_space = true;
    for c in s.chars() {
        if c.is_whitespace() {
            if !last_was_space {
                result.push(' ');
                last_was_space = true;
            }
        } else {
            result.push(c);
            last_was_space = false;
        }
    }
    result.trim().to_string()
}

/// Deterministic hash for a URL (used as fixture filename).
pub fn url_hash(url: &str) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(url.as_bytes());
    let result = hasher.finalize();
    format!("{:x}", result)[..16].to_string()
}

#[cfg(feature = "web-fetch-live")]
fn chrono_now_iso() -> String {
    use std::time::SystemTime;
    let now = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    format!("{now}")
}

/// One enrichment disposition: what happened to a needs-content source.
#[derive(Debug, Clone)]
pub struct EnrichResult {
    pub file_path: String,
    pub url: String,
    pub fetch: FetchResult,
    /// Whether the file was successfully updated with the fetched content.
    pub updated: bool,
}

/// Enrich needs-content sources by fetching their URLs and writing the content
/// back into the markdown files (as the body, preserving frontmatter).
///
/// `sources`: `(vault-relative path, absolute path, url)` tuples from
/// `IntakeRecord.from` + resolved absolute path + `IntakeRecord.url`.
///
/// Returns a list of enrichment dispositions. Files that were successfully
/// enriched now have enough body content for the reader pipeline.
pub fn enrich_needs_content(
    fetcher: &mut dyn WebFetch,
    vault_root: &std::path::Path,
    sources: &[(String, String)], // (vault-relative path, url)
) -> Vec<EnrichResult> {
    sources
        .iter()
        .filter_map(|(rel_path, url)| {
            if url.is_empty() {
                return None;
            }
            let abs_path = vault_root.join(rel_path);
            let fetch = fetcher.fetch_readable(url);
            let updated = if let Some(content) = &fetch.content {
                if !content.trim().is_empty() {
                    update_source_body(&abs_path, content, fetch.title.as_deref()).is_ok()
                } else {
                    false
                }
            } else {
                false
            };
            Some(EnrichResult {
                file_path: rel_path.clone(),
                url: url.clone(),
                fetch,
                updated,
            })
        })
        .collect()
}

/// Update a markdown source file's body with fetched web content.
/// Preserves existing frontmatter (between `---` fences). Replaces everything
/// after the frontmatter with the fetched content.
fn update_source_body(
    path: &std::path::Path,
    content: &str,
    title: Option<&str>,
) -> Result<(), String> {
    let existing =
        std::fs::read_to_string(path).map_err(|e| format!("read {}: {e}", path.display()))?;

    let (frontmatter, _old_body) = split_frontmatter(&existing);

    let mut new_content = String::new();
    if let Some(fm) = frontmatter {
        new_content.push_str("---\n");
        new_content.push_str(fm);
        if !fm.ends_with('\n') {
            new_content.push('\n');
        }
        new_content.push_str("---\n\n");
    }
    if let Some(t) = title {
        if !content.starts_with('#') {
            new_content.push_str(&format!("# {t}\n\n"));
        }
    }
    new_content.push_str(content);
    if !new_content.ends_with('\n') {
        new_content.push('\n');
    }

    std::fs::write(path, &new_content)
        .map_err(|e| format!("write {}: {e}", path.display()))
}

fn split_frontmatter(text: &str) -> (Option<&str>, &str) {
    if !text.starts_with("---") {
        return (None, text);
    }
    let rest = &text[3..];
    let rest = rest.trim_start_matches(['\r', '\n']);
    if let Some(pos) = rest.find("\n---") {
        let fm_content = &rest[..pos];
        let after_close = &rest[pos + 4..];
        let body = after_close.trim_start_matches(['\r', '\n']);
        (Some(fm_content), body)
    } else {
        (None, text)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fixture_fetch_returns_content() {
        let dir = tempfile::tempdir().unwrap();
        let mut fetcher =
            FixtureWebFetch::with_response(dir.path(), "https://example.com", "Hello world");
        let result = fetcher.fetch_readable("https://example.com");
        assert_eq!(result.content.as_deref(), Some("Hello world"));
        assert!(result.error.is_none());
    }

    #[test]
    fn fixture_fetch_missing_url_returns_error() {
        let dir = tempfile::tempdir().unwrap();
        let mut fetcher = FixtureWebFetch::new(dir.path());
        let result = fetcher.fetch_readable("https://missing.example.com");
        assert!(result.content.is_none());
        assert!(result.error.is_some());
    }

    #[test]
    fn url_hash_is_deterministic() {
        let h1 = url_hash("https://example.com/foo");
        let h2 = url_hash("https://example.com/foo");
        assert_eq!(h1, h2);
        assert_eq!(h1.len(), 16);
    }

    #[test]
    fn strip_tags_extracts_text() {
        let html = "<html><body><h1>Title</h1><p>Hello world</p></body></html>";
        let text = strip_tags(html);
        assert!(text.contains("Title"));
        assert!(text.contains("Hello world"));
        assert!(!text.contains("<"));
    }

    #[test]
    fn strip_tags_removes_script_and_style() {
        let html = "<html><script>var x=1;</script><style>.a{}</style><p>Content</p></html>";
        let text = strip_tags(html);
        assert!(!text.contains("var x"));
        assert!(!text.contains(".a{}"));
        assert!(text.contains("Content"));
    }

    #[test]
    fn extract_title_works() {
        let html = "<html><head><title>My Page</title></head><body></body></html>";
        assert_eq!(extract_title(html), Some("My Page".to_string()));
    }

    #[test]
    fn split_frontmatter_extracts_yaml() {
        let text = "---\ntitle: Test\nurl: http://x.com\n---\n\nBody here.";
        let (fm, body) = split_frontmatter(text);
        assert_eq!(fm, Some("title: Test\nurl: http://x.com"));
        assert_eq!(body, "Body here.");
    }

    #[test]
    fn split_frontmatter_no_fence() {
        let text = "Just a body.";
        let (fm, body) = split_frontmatter(text);
        assert!(fm.is_none());
        assert_eq!(body, text);
    }

    #[test]
    fn enrich_updates_file_with_fetched_content() {
        let vault = tempfile::tempdir().unwrap();
        let fixture_dir = tempfile::tempdir().unwrap();

        let source_dir = vault.path().join("50-Inbox/02-Pinboard");
        std::fs::create_dir_all(&source_dir).unwrap();
        let source_file = source_dir.join("bookmark.md");
        std::fs::write(
            &source_file,
            "---\ntitle: Example\nurl: https://example.com/article\n---\n\nShort.\n",
        )
        .unwrap();

        let mut fetcher = FixtureWebFetch::with_response(
            fixture_dir.path(),
            "https://example.com/article",
            "This is a long article with enough content to pass the minimum body threshold for the reader pipeline. It has multiple paragraphs of meaningful text that would normally be extracted from the web page.",
        );

        let sources = vec![(
            "50-Inbox/02-Pinboard/bookmark.md".to_string(),
            "https://example.com/article".to_string(),
        )];
        let results = enrich_needs_content(&mut fetcher, vault.path(), &sources);

        assert_eq!(results.len(), 1);
        assert!(results[0].updated);

        let updated = std::fs::read_to_string(&source_file).unwrap();
        assert!(updated.contains("title: Example"));
        assert!(updated.contains("enough content to pass"));
    }
}
