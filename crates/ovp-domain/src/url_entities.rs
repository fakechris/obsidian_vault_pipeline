//! Tier-0 URL entities (docs/stage-tags-product.md §5b): machine-verifiable
//! referents whose identity IS a registry URL, so extraction is deterministic
//! string matching — no LLM, no network, ~100% precision. A GitHub repo, an
//! arXiv paper, a DOI, an npm/crates/PyPI package, a Hacker News item: the
//! author already did the "linking" by pasting the URL.
//!
//! Deliberately NOT here: open entity extraction (people/orgs/concepts) or
//! Wikidata linking — those stay behind the M34 navigation experiment.

use std::collections::BTreeMap;

/// The registry a URL entity belongs to. `as_str` is the id prefix.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum EntityKind {
    Github,
    Arxiv,
    Doi,
    Npm,
    Crates,
    Pypi,
    Hn,
}

impl EntityKind {
    pub fn as_str(self) -> &'static str {
        match self {
            EntityKind::Github => "github",
            EntityKind::Arxiv => "arxiv",
            EntityKind::Doi => "doi",
            EntityKind::Npm => "npm",
            EntityKind::Crates => "crates",
            EntityKind::Pypi => "pypi",
            EntityKind::Hn => "hn",
        }
    }
}

/// One extracted entity mention. `id` = `<kind>:<normalized-key>` (the stable
/// identity); `url` = a canonical https URL for the external link.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UrlEntity {
    pub id: String,
    pub kind: EntityKind,
    pub url: String,
}

/// Reconstruct the canonical external URL from an entity id (`kind:key`).
/// The whole reverse index (entity → sources) is derivable from the
/// `SourceRow.entities` forward lists PLUS this — no sidecar file needed.
pub fn url_for_id(id: &str) -> Option<String> {
    let (kind, key) = id.split_once(':')?;
    if key.is_empty() {
        return None;
    }
    Some(match kind {
        "github" => format!("https://github.com/{key}"),
        "arxiv" => format!("https://arxiv.org/abs/{key}"),
        "doi" => format!("https://doi.org/{key}"),
        "npm" => format!("https://www.npmjs.com/package/{key}"),
        "crates" => format!("https://crates.io/crates/{key}"),
        "pypi" => format!("https://pypi.org/project/{key}"),
        "hn" => format!("https://news.ycombinator.com/item?id={key}"),
        _ => return None,
    })
}

/// The kind prefix of an entity id (`github:owner/repo` → `github`).
pub fn kind_of_id(id: &str) -> Option<&str> {
    id.split_once(':').map(|(k, _)| k)
}

/// GitHub first-path segments that are site features, not repo owners.
const GITHUB_RESERVED: &[&str] = &[
    "about", "apps", "collections", "contact", "customer-stories", "dashboard", "enterprise",
    "explore", "features", "issues", "join", "login", "marketplace", "new", "notifications",
    "orgs", "pricing", "pulls", "readme", "security", "settings", "site", "sponsors", "stars",
    "team", "topics", "watching", "codespaces", "search", "trending",
    // Asset/CDN paths, not repo owners (the `github.com/user-attachments/assets/…`
    // image host that shows up all over pasted markdown).
    "user-attachments", "assets", "raw", "gist",
];

/// GitHub repo-name segment that is really a sub-route captured as the 2nd
/// segment (bare `github.com/owner` links sometimes trail one of these).
const GITHUB_NON_REPO: &[&str] = &["followers", "following", "repositories", "stars", "sponsors"];

/// Extract every URL entity mentioned by a source: its own `source_url` plus
/// every link/bare-URL/`arXiv:id` in the body. Deduped by id, sorted.
pub fn extract(source_url: &str, body: &str) -> Vec<UrlEntity> {
    let mut found: BTreeMap<String, UrlEntity> = BTreeMap::new();
    for raw in candidate_urls(source_url).chain(candidate_urls(body)) {
        if let Some(e) = parse_url(raw) {
            found.entry(e.id.clone()).or_insert(e);
        }
    }
    for id in arxiv_inline_ids(source_url).chain(arxiv_inline_ids(body)) {
        let e = UrlEntity {
            url: format!("https://arxiv.org/abs/{id}"),
            id: format!("arxiv:{id}"),
            kind: EntityKind::Arxiv,
        };
        found.entry(e.id.clone()).or_insert(e);
    }
    found.into_values().collect()
}

/// Yield each `http(s)://…` substring, trimmed at the first delimiter and of
/// trailing sentence punctuation. Deterministic, allocation-light.
fn candidate_urls(text: &str) -> impl Iterator<Item = &str> {
    let mut rest = text;
    std::iter::from_fn(move || {
        loop {
            let start = rest.find("http")?;
            let after = &rest[start..];
            if after.starts_with("http://") || after.starts_with("https://") {
                let end = after
                    .find(|c: char| c.is_whitespace() || "<>()[]{}\"'`|\\".contains(c))
                    .unwrap_or(after.len());
                let url = after[..end].trim_end_matches(['.', ',', ';', ':', '!', '?']);
                rest = &after[end..];
                if !url.is_empty() {
                    return Some(url);
                }
            } else {
                // "http" not followed by a scheme — advance past it.
                rest = &after[4..];
            }
        }
    })
}

/// Inline `arXiv:NNNN.NNNNN` mentions (case-insensitive), version suffix
/// stripped — the paper id even when no arxiv.org URL is present. Scans the
/// ORIGINAL bytes (the `arxiv:` needle is pure ASCII, so a case-insensitive
/// byte match yields offsets into `text` directly — no lowercased-copy
/// offsets that would misalign after a non-ASCII char).
fn arxiv_inline_ids(text: &str) -> impl Iterator<Item = String> + '_ {
    const NEEDLE: &[u8] = b"arxiv:";
    let bytes = text.as_bytes();
    let mut positions = Vec::new();
    let mut i = 0;
    while i + NEEDLE.len() <= bytes.len() {
        if bytes[i..i + NEEDLE.len()].eq_ignore_ascii_case(NEEDLE) {
            positions.push(i + NEEDLE.len());
            i += NEEDLE.len();
        } else {
            i += 1;
        }
    }
    positions
        .into_iter()
        .filter_map(move |at| normalize_arxiv_id(text.get(at..)?))
}

/// Parse one already-trimmed URL into an entity, or `None`.
fn parse_url(url: &str) -> Option<UrlEntity> {
    let rest = url
        .strip_prefix("https://")
        .or_else(|| url.strip_prefix("http://"))?;
    let (host_raw, path) = rest.split_once('/').unwrap_or((rest, ""));
    // Hostnames are case-insensitive; lowercase before matching so
    // `GitHub.com` / `WWW.ARXIV.ORG` resolve like their canonical forms.
    let host_lc = host_raw.to_lowercase();
    let host = host_lc.strip_prefix("www.").unwrap_or(&host_lc);
    // Strip query + fragment from the whole path.
    let path = path.split(['?', '#']).next().unwrap_or(path);
    let segs: Vec<&str> = path.split('/').filter(|s| !s.is_empty()).collect();

    match host {
        "github.com" => {
            let owner = segs.first()?.to_lowercase();
            let repo = segs.get(1)?.to_lowercase();
            let repo = repo.strip_suffix(".git").unwrap_or(&repo).to_string();
            if GITHUB_RESERVED.contains(&owner.as_str())
                || GITHUB_NON_REPO.contains(&repo.as_str())
                || owner.is_empty()
                || repo.is_empty()
            {
                return None;
            }
            Some(UrlEntity {
                url: format!("https://github.com/{owner}/{repo}"),
                id: format!("github:{owner}/{repo}"),
                kind: EntityKind::Github,
            })
        }
        "arxiv.org" => {
            // /abs/<id> or /pdf/<id>(.pdf)
            let idx = segs.iter().position(|s| *s == "abs" || *s == "pdf")?;
            let raw = segs.get(idx + 1)?.strip_suffix(".pdf").unwrap_or(segs[idx + 1]);
            let id = normalize_arxiv_id(raw)?;
            Some(UrlEntity {
                url: format!("https://arxiv.org/abs/{id}"),
                id: format!("arxiv:{id}"),
                kind: EntityKind::Arxiv,
            })
        }
        "doi.org" | "dx.doi.org" => {
            let key = path.trim_start_matches('/');
            if !key.starts_with("10.") {
                return None;
            }
            let key = key.to_lowercase();
            Some(UrlEntity {
                url: format!("https://doi.org/{key}"),
                id: format!("doi:{key}"),
                kind: EntityKind::Doi,
            })
        }
        "npmjs.com" => {
            let idx = segs.iter().position(|s| *s == "package")?;
            let pkg = pkg_key(&segs[idx + 1..])?;
            Some(UrlEntity {
                url: format!("https://www.npmjs.com/package/{pkg}"),
                id: format!("npm:{pkg}"),
                kind: EntityKind::Npm,
            })
        }
        "crates.io" => {
            let idx = segs.iter().position(|s| *s == "crates")?;
            let pkg = pkg_key(&segs[idx + 1..])?;
            Some(UrlEntity {
                url: format!("https://crates.io/crates/{pkg}"),
                id: format!("crates:{pkg}"),
                kind: EntityKind::Crates,
            })
        }
        "pypi.org" => {
            let idx = segs.iter().position(|s| *s == "project")?;
            let pkg = pkg_key(&segs[idx + 1..])?;
            Some(UrlEntity {
                url: format!("https://pypi.org/project/{pkg}"),
                id: format!("pypi:{pkg}"),
                kind: EntityKind::Pypi,
            })
        }
        "news.ycombinator.com" => {
            // Only the /item route names a story/comment; /user?id=… is a
            // profile, not an item entity.
            if segs.first() != Some(&"item") {
                return None;
            }
            let id = url.split("id=").nth(1)?;
            let id: String = id.chars().take_while(|c| c.is_ascii_digit()).collect();
            if id.is_empty() {
                return None;
            }
            Some(UrlEntity {
                url: format!("https://news.ycombinator.com/item?id={id}"),
                id: format!("hn:{id}"),
                kind: EntityKind::Hn,
            })
        }
        _ => None,
    }
}

/// npm/crates/pypi package key: first segment (scoped `@org/pkg` keeps both),
/// lowercased. `None` when empty.
fn pkg_key(segs: &[&str]) -> Option<String> {
    let first = segs.first()?;
    if first.starts_with('@') {
        let scope = first.to_lowercase();
        let name = segs.get(1).map(|s| s.to_lowercase());
        return name.map(|n| format!("{scope}/{n}"));
    }
    (!first.is_empty()).then(|| first.to_lowercase())
}

/// Normalize an arXiv id: `2504.19413`, `2504.19413v2` → `2504.19413`.
/// Requires the modern `NNNN.NNNNN` shape (4 digits · dot · 4–5 digits).
fn normalize_arxiv_id(raw: &str) -> Option<String> {
    let head: String = raw
        .chars()
        .take_while(|c| c.is_ascii_digit() || *c == '.')
        .collect();
    let (a, b) = head.split_once('.')?;
    if a.len() == 4 && (4..=5).contains(&b.len()) && b.chars().all(|c| c.is_ascii_digit()) {
        Some(format!("{a}.{b}"))
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ids(source_url: &str, body: &str) -> Vec<String> {
        extract(source_url, body).into_iter().map(|e| e.id).collect()
    }

    #[test]
    fn github_variants_normalize_to_one_id() {
        let body = "see [repo](https://github.com/AI4Finance-Foundation/FinGPT/tree/main) \
                    and https://github.com/ai4finance-foundation/fingpt.git and \
                    <https://www.github.com/AI4Finance-Foundation/FinGPT?tab=readme>.";
        assert_eq!(ids("", body), vec!["github:ai4finance-foundation/fingpt"]);
    }

    #[test]
    fn github_reserved_and_bare_paths_are_rejected() {
        let body = "https://github.com/features/actions https://github.com/sponsors/x \
                    https://github.com/torvalds https://github.com/orgs/rust-lang \
                    https://github.com/user-attachments/assets/abc-123";
        assert!(ids("", body).is_empty());
    }

    #[test]
    fn arxiv_url_and_inline_and_version_collapse() {
        let body = "https://arxiv.org/pdf/2504.19413v2.pdf and arXiv:2504.19413 and \
                    https://arxiv.org/abs/2501.13956";
        assert_eq!(ids("", body), vec!["arxiv:2501.13956", "arxiv:2504.19413"]);
    }

    #[test]
    fn packages_doi_hn_and_scoped() {
        let body = "https://www.npmjs.com/package/@openai/agents \
                    https://crates.io/crates/serde \
                    https://pypi.org/project/requests/ \
                    https://doi.org/10.1145/3583780.3615036 \
                    https://news.ycombinator.com/item?id=42007321";
        assert_eq!(
            ids("", body),
            vec![
                "crates:serde",
                "doi:10.1145/3583780.3615036",
                "hn:42007321",
                "npm:@openai/agents",
                "pypi:requests",
            ]
        );
    }

    #[test]
    fn source_url_self_entity_and_non_entities_ignored() {
        let entities = extract("https://github.com/owner/repo", "a plain https://example.com/x link");
        assert_eq!(entities.len(), 1);
        assert_eq!(entities[0].id, "github:owner/repo");
        assert_eq!(entities[0].url, "https://github.com/owner/repo");
    }

    #[test]
    fn hn_requires_item_route_and_arxiv_inline_after_non_ascii() {
        // /user is a profile, not an item.
        assert!(ids("", "https://news.ycombinator.com/user?id=12345").is_empty());
        assert_eq!(ids("", "https://news.ycombinator.com/item?id=42"), vec!["hn:42"]);
        // Inline arXiv mention preceded by a multi-byte char: the byte offset
        // must land on the original string, not a lowercased copy.
        assert_eq!(ids("", "论文 arXiv:2504.19413 见"), vec!["arxiv:2504.19413"]);
    }

    #[test]
    fn markdown_paren_boundary_and_trailing_punct() {
        let body = "(https://github.com/a/b), then https://arxiv.org/abs/1234.56789.";
        assert_eq!(ids("", body), vec!["arxiv:1234.56789", "github:a/b"]);
    }
}
