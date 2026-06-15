//! Image download: download images referenced in reader packs.
//!
//! Only modifies derived reader pack link references (never touches source
//! file bytes or sha256 hashes). Downloads to `attachments/<hash>.<ext>`.
//!
//! Safety limits (per plan 1c):
//! - Only download `image/*` content types
//! - Single image ≤ 10 MB
//! - Total per source ≤ 50 MB
//! - `--no-images` CLI flag skips entirely

use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

/// Configuration for image download limits.
#[derive(Debug, Clone)]
pub struct ImageDownloadConfig {
    pub max_single_bytes: usize,
    pub max_total_bytes: usize,
    pub attachments_dir: PathBuf,
}

impl Default for ImageDownloadConfig {
    fn default() -> Self {
        Self {
            max_single_bytes: 10 * 1024 * 1024,
            max_total_bytes: 50 * 1024 * 1024,
            attachments_dir: PathBuf::from("attachments"),
        }
    }
}

/// A single image reference found in markdown.
#[derive(Debug, Clone)]
pub struct ImageRef {
    pub alt: String,
    pub url: String,
    pub line_number: usize,
}

/// Result of downloading one image.
#[derive(Debug, Clone)]
pub struct ImageDownloadResult {
    pub url: String,
    pub local_path: Option<String>,
    pub error: Option<String>,
    pub bytes_downloaded: usize,
}

/// The image download effect boundary.
pub trait ImageDownloader {
    /// Download an image from `url`, save to the attachments directory.
    /// Returns the relative path within the vault on success.
    fn download_image(&mut self, url: &str, config: &ImageDownloadConfig) -> ImageDownloadResult;
    fn origin(&self) -> String;
}

/// Offline fixture downloader: pre-staged image files in a directory.
///
/// Layout: `<fixture_dir>/<url_hash>.<ext>` (no actual download).
pub struct FixtureImageDownloader {
    fixture_dir: PathBuf,
}

impl FixtureImageDownloader {
    pub fn new(fixture_dir: impl Into<PathBuf>) -> Self {
        Self {
            fixture_dir: fixture_dir.into(),
        }
    }
}

impl ImageDownloader for FixtureImageDownloader {
    fn download_image(&mut self, url: &str, config: &ImageDownloadConfig) -> ImageDownloadResult {
        let hash = url_content_hash(url.as_bytes());
        let ext = guess_extension_from_url(url);
        let filename = format!("{hash}.{ext}");
        let fixture_path = self.fixture_dir.join(&filename);

        if !fixture_path.exists() {
            return ImageDownloadResult {
                url: url.to_string(),
                local_path: None,
                error: Some(format!("fixture not found: {filename}")),
                bytes_downloaded: 0,
            };
        }

        let bytes = match std::fs::read(&fixture_path) {
            Ok(b) => b,
            Err(e) => {
                return ImageDownloadResult {
                    url: url.to_string(),
                    local_path: None,
                    error: Some(format!("fixture read error: {e}")),
                    bytes_downloaded: 0,
                };
            }
        };

        if bytes.len() > config.max_single_bytes {
            return ImageDownloadResult {
                url: url.to_string(),
                local_path: None,
                error: Some(format!(
                    "image too large: {} bytes > {} limit",
                    bytes.len(),
                    config.max_single_bytes
                )),
                bytes_downloaded: 0,
            };
        }

        let content_hash = url_content_hash(&bytes);
        let dest_filename = format!("{content_hash}.{ext}");
        let dest_rel = config.attachments_dir.join(&dest_filename);

        ImageDownloadResult {
            url: url.to_string(),
            local_path: Some(dest_rel.to_string_lossy().to_string()),
            error: None,
            bytes_downloaded: bytes.len(),
        }
    }

    fn origin(&self) -> String {
        format!("fixture:{}", self.fixture_dir.display())
    }
}

#[cfg(feature = "web-fetch-live")]
pub struct LiveImageDownloader {
    client: reqwest::blocking::Client,
    last_fetch: Option<std::time::Instant>,
}

#[cfg(feature = "web-fetch-live")]
impl LiveImageDownloader {
    pub fn new() -> Result<Self, String> {
        let client = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(60))
            .user_agent("ovp-enrich/0.1")
            .build()
            .map_err(|e| format!("HTTP client build error: {e}"))?;
        Ok(Self {
            client,
            last_fetch: None,
        })
    }

    fn rate_limit_wait(&mut self) {
        if let Some(last) = self.last_fetch {
            let elapsed = last.elapsed().as_millis() as u64;
            if elapsed < 200 {
                std::thread::sleep(std::time::Duration::from_millis(200 - elapsed));
            }
        }
        self.last_fetch = Some(std::time::Instant::now());
    }
}

#[cfg(feature = "web-fetch-live")]
impl ImageDownloader for LiveImageDownloader {
    fn download_image(&mut self, url: &str, config: &ImageDownloadConfig) -> ImageDownloadResult {
        self.rate_limit_wait();

        let resp = match self.client.get(url).send() {
            Ok(r) => r,
            Err(e) => {
                return ImageDownloadResult {
                    url: url.to_string(),
                    local_path: None,
                    error: Some(format!("request failed: {e}")),
                    bytes_downloaded: 0,
                };
            }
        };

        if !resp.status().is_success() {
            return ImageDownloadResult {
                url: url.to_string(),
                local_path: None,
                error: Some(format!("HTTP {}", resp.status())),
                bytes_downloaded: 0,
            };
        }

        let content_type = resp
            .headers()
            .get("content-type")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();

        if !content_type.starts_with("image/") {
            return ImageDownloadResult {
                url: url.to_string(),
                local_path: None,
                error: Some(format!("not an image: content-type={content_type}")),
                bytes_downloaded: 0,
            };
        }

        let bytes = match resp.bytes() {
            Ok(b) => b.to_vec(),
            Err(e) => {
                return ImageDownloadResult {
                    url: url.to_string(),
                    local_path: None,
                    error: Some(format!("body read error: {e}")),
                    bytes_downloaded: 0,
                };
            }
        };

        if bytes.len() > config.max_single_bytes {
            return ImageDownloadResult {
                url: url.to_string(),
                local_path: None,
                error: Some(format!(
                    "image too large: {} bytes > {} limit",
                    bytes.len(),
                    config.max_single_bytes
                )),
                bytes_downloaded: 0,
            };
        }

        let content_hash = url_content_hash(&bytes);
        let ext = extension_from_content_type(&content_type)
            .unwrap_or_else(|| guess_extension_from_url(url));
        let dest_filename = format!("{content_hash}.{ext}");
        let dest_path = config.attachments_dir.join(&dest_filename);

        if let Some(parent) = dest_path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }

        if let Err(e) = std::fs::write(&dest_path, &bytes) {
            return ImageDownloadResult {
                url: url.to_string(),
                local_path: None,
                error: Some(format!("write error: {e}")),
                bytes_downloaded: 0,
            };
        }

        ImageDownloadResult {
            url: url.to_string(),
            local_path: Some(dest_path.to_string_lossy().to_string()),
            error: None,
            bytes_downloaded: bytes.len(),
        }
    }

    fn origin(&self) -> String {
        "live-http".to_string()
    }
}

// --- Pack rewriting ---

/// Summary of image processing for a single pack file.
#[derive(Debug)]
pub struct PackImageResult {
    pub pack_file: String,
    pub images_found: usize,
    pub images_downloaded: usize,
    pub images_failed: usize,
    pub total_bytes: usize,
}

/// Find all remote image references in markdown content.
pub fn find_image_refs(content: &str) -> Vec<ImageRef> {
    let mut refs = Vec::new();
    for (line_idx, line) in content.lines().enumerate() {
        let mut pos = 0;
        while pos < line.len() {
            if let Some(start) = line[pos..].find("![") {
                let abs_start = pos + start;
                let after_bang = abs_start + 2;
                if let Some(close_bracket) = line[after_bang..].find(']') {
                    let alt_end = after_bang + close_bracket;
                    let alt = &line[after_bang..alt_end];
                    let paren_start = alt_end + 1;
                    if paren_start < line.len() && line.as_bytes()[paren_start] == b'(' {
                        if let Some(close_paren) = line[paren_start..].find(')') {
                            let url = &line[paren_start + 1..paren_start + close_paren];
                            if url.starts_with("http://") || url.starts_with("https://") {
                                refs.push(ImageRef {
                                    alt: alt.to_string(),
                                    url: url.to_string(),
                                    line_number: line_idx + 1,
                                });
                            }
                            pos = paren_start + close_paren + 1;
                            continue;
                        }
                    }
                }
                pos = abs_start + 2;
            } else {
                break;
            }
        }
    }
    refs
}

/// Rewrite image URLs in markdown content, replacing remote URLs with local paths.
/// Returns the rewritten content.
pub fn rewrite_image_urls(content: &str, replacements: &[(String, String)]) -> String {
    let mut result = content.to_string();
    for (old_url, new_path) in replacements {
        result = result.replace(old_url, new_path);
    }
    result
}

/// Process a single reader pack directory: find images, download, rewrite links.
pub fn process_pack_images(
    pack_dir: &Path,
    vault_root: &Path,
    downloader: &mut dyn ImageDownloader,
    config: &ImageDownloadConfig,
) -> Vec<PackImageResult> {
    let mut results = Vec::new();

    let md_files = match collect_md_files(pack_dir) {
        Ok(files) => files,
        Err(_) => return results,
    };

    let abs_attachments = vault_root.join(&config.attachments_dir);
    let _ = std::fs::create_dir_all(&abs_attachments);

    for md_file in md_files {
        let content = match std::fs::read_to_string(&md_file) {
            Ok(c) => c,
            Err(_) => continue,
        };

        let image_refs = find_image_refs(&content);
        if image_refs.is_empty() {
            continue;
        }

        let mut downloaded = 0;
        let mut failed = 0;
        let mut total_bytes = 0usize;
        let mut replacements = Vec::new();

        let real_config = ImageDownloadConfig {
            attachments_dir: abs_attachments.clone(),
            ..*config
        };

        for img_ref in &image_refs {
            if total_bytes >= config.max_total_bytes {
                failed += image_refs.len() - downloaded - failed;
                break;
            }

            let result = downloader.download_image(&img_ref.url, &real_config);
            if let Some(local_path) = &result.local_path {
                let vault_rel = format!(
                    "{}",
                    config.attachments_dir.join(
                        Path::new(local_path)
                            .file_name()
                            .unwrap_or_default()
                    ).display()
                );
                replacements.push((img_ref.url.clone(), vault_rel));
                total_bytes += result.bytes_downloaded;
                downloaded += 1;
            } else {
                failed += 1;
            }
        }

        if !replacements.is_empty() {
            let rewritten = rewrite_image_urls(&content, &replacements);
            let _ = std::fs::write(&md_file, rewritten);
        }

        let rel_file = md_file.strip_prefix(vault_root).unwrap_or(&md_file);
        results.push(PackImageResult {
            pack_file: rel_file.to_string_lossy().to_string(),
            images_found: image_refs.len(),
            images_downloaded: downloaded,
            images_failed: failed,
            total_bytes,
        });
    }

    results
}

// --- Helpers ---

fn collect_md_files(dir: &Path) -> Result<Vec<PathBuf>, String> {
    let mut files = Vec::new();
    if !dir.is_dir() {
        return Err("not a directory".into());
    }
    let entries = std::fs::read_dir(dir).map_err(|e| e.to_string())?;
    for entry in entries {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();
        if path.extension().is_some_and(|e| e == "md") {
            files.push(path);
        }
    }
    files.sort();
    Ok(files)
}

pub fn url_content_hash(bytes: &[u8]) -> String {
    let hash = Sha256::digest(bytes);
    format!("{:x}", hash)[..16].to_string()
}

fn guess_extension_from_url(url: &str) -> String {
    let path = url.split('?').next().unwrap_or(url);
    if let Some(dot) = path.rfind('.') {
        let ext = &path[dot + 1..];
        let ext = ext.to_lowercase();
        match ext.as_str() {
            "jpg" | "jpeg" | "png" | "gif" | "webp" | "svg" | "ico" | "avif" => ext,
            _ => "png".to_string(),
        }
    } else {
        "png".to_string()
    }
}

#[cfg(feature = "web-fetch-live")]
fn extension_from_content_type(ct: &str) -> Option<String> {
    let ct = ct.split(';').next().unwrap_or(ct).trim();
    match ct {
        "image/jpeg" => Some("jpg".into()),
        "image/png" => Some("png".into()),
        "image/gif" => Some("gif".into()),
        "image/webp" => Some("webp".into()),
        "image/svg+xml" => Some("svg".into()),
        "image/avif" => Some("avif".into()),
        "image/x-icon" | "image/vnd.microsoft.icon" => Some("ico".into()),
        _ => None,
    }
}

// --- Tests ---

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn find_image_refs_basic() {
        let md = r#"# Title

Some text ![alt](https://example.com/image.png) more text.

![](https://cdn.site.io/photo.jpg)

Not an image: [link](https://example.com)
"#;
        let refs = find_image_refs(md);
        assert_eq!(refs.len(), 2);
        assert_eq!(refs[0].url, "https://example.com/image.png");
        assert_eq!(refs[0].alt, "alt");
        assert_eq!(refs[1].url, "https://cdn.site.io/photo.jpg");
    }

    #[test]
    fn find_image_refs_skips_local() {
        let md = "![local](./images/foo.png)\n![remote](https://x.com/img.png)\n";
        let refs = find_image_refs(md);
        assert_eq!(refs.len(), 1);
        assert_eq!(refs[0].url, "https://x.com/img.png");
    }

    #[test]
    fn rewrite_image_urls_basic() {
        let content = "![img](https://cdn.example.com/pic.png) text";
        let replacements = vec![(
            "https://cdn.example.com/pic.png".to_string(),
            "attachments/abc123.png".to_string(),
        )];
        let result = rewrite_image_urls(content, &replacements);
        assert_eq!(result, "![img](attachments/abc123.png) text");
    }

    #[test]
    fn url_content_hash_deterministic() {
        let h1 = url_content_hash(b"hello world");
        let h2 = url_content_hash(b"hello world");
        assert_eq!(h1, h2);
        assert_eq!(h1.len(), 16);
    }

    #[test]
    fn guess_extension_common_types() {
        assert_eq!(guess_extension_from_url("https://x.com/foo.jpg"), "jpg");
        assert_eq!(guess_extension_from_url("https://x.com/bar.PNG"), "png");
        assert_eq!(
            guess_extension_from_url("https://x.com/img.webp?w=100"),
            "webp"
        );
        assert_eq!(guess_extension_from_url("https://x.com/no-ext"), "png");
    }

    #[test]
    fn fixture_downloader_missing() {
        let tmp = tempfile::tempdir().unwrap();
        let mut dl = FixtureImageDownloader::new(tmp.path());
        let cfg = ImageDownloadConfig::default();
        let result = dl.download_image("https://example.com/img.png", &cfg);
        assert!(result.error.is_some());
        assert!(result.local_path.is_none());
    }

    #[test]
    fn fixture_downloader_success() {
        let tmp = tempfile::tempdir().unwrap();
        let url = "https://example.com/pic.jpg";
        let hash = url_content_hash(url.as_bytes());
        let fixture_file = tmp.path().join(format!("{hash}.jpg"));
        std::fs::write(&fixture_file, b"fake image bytes").unwrap();

        let mut dl = FixtureImageDownloader::new(tmp.path());
        let cfg = ImageDownloadConfig::default();
        let result = dl.download_image(url, &cfg);
        assert!(result.error.is_none());
        assert!(result.local_path.is_some());
        assert_eq!(result.bytes_downloaded, 16);
    }

    #[test]
    fn process_pack_images_rewrites() {
        let tmp = tempfile::tempdir().unwrap();
        let vault_root = tmp.path();

        let pack_dir = vault_root.join("40-Resources/Reader/test-pack");
        std::fs::create_dir_all(&pack_dir).unwrap();

        let md_content = "# Test\n\n![img](https://example.com/pic.jpg)\n";
        std::fs::write(pack_dir.join("content.md"), md_content).unwrap();

        let fixture_dir = vault_root.join(".image-fixtures");
        std::fs::create_dir_all(&fixture_dir).unwrap();
        let url = "https://example.com/pic.jpg";
        let hash = url_content_hash(url.as_bytes());
        std::fs::write(fixture_dir.join(format!("{hash}.jpg")), b"JFIF fake").unwrap();

        let mut downloader = FixtureImageDownloader::new(&fixture_dir);
        let config = ImageDownloadConfig {
            attachments_dir: PathBuf::from("attachments"),
            ..Default::default()
        };

        let results = process_pack_images(&pack_dir, vault_root, &mut downloader, &config);
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].images_found, 1);
        assert_eq!(results[0].images_downloaded, 1);

        let rewritten = std::fs::read_to_string(pack_dir.join("content.md")).unwrap();
        assert!(!rewritten.contains("https://example.com/pic.jpg"));
        assert!(rewritten.contains("attachments/"));
    }
}
