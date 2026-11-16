//! Claim propagation (INV-1882): within ONE durable claim, which of its cited
//! sources published first, when the others followed, and whether they say
//! where they got it.
//!
//! This is a projection, not ledger state. It is derived at index build from
//! the claim's own `source_cases` and each source's publication date, so the
//! append-only crystal ledger never carries it and it can be recomputed at
//! any time.
//!
//! Two deliberate refusals:
//! - The date is the source's PUBLICATION date. When OVP happened to
//!   crystallize the claim says nothing about who said it first.
//! - When two distinct sources tie for the earliest date there is no origin.
//!   Picking one (by id, by capture order) would invent an answer.
//!
//! Explicit cross-crediting between captured sources is rare (on the dogfood
//! vault ~2% of later sources link the origin's URL, ~2% name its author), so
//! a `credit_rate` of 0 is the expected, truthful common case.

use serde::{Deserialize, Serialize};

use crate::citation_support::CitationType;
use crate::crystal::source_identity::canonical_source_url;

/// What the caller knows about one cited source.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SourceFacts {
    pub case_id: String,
    /// Content identity (sha256). Two cases with the same identity, or the
    /// same normalized URL, are ONE source captured twice.
    pub identity: String,
    /// Publication date, `YYYY-MM-DD`. Anything else counts as undated.
    pub published: Option<String>,
    pub url: Option<String>,
    pub author: Option<String>,
}

/// One later source that carries the claim.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Adoption {
    pub case_id: String,
    pub published: String,
    /// Calendar days after the origin's publication (>= 0).
    pub lag_days: i64,
    /// How this source relates to the origin: `exact_link` (contains its
    /// URL), `credit` (names its author or site), `inferred` (only OVP's
    /// claim matching connects them), `unknown` (text unreadable).
    pub citation_type: CitationType,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Propagation {
    pub origin_case_id: String,
    pub origin_published: String,
    /// Later sources, earliest first.
    pub adoptions: Vec<Adoption>,
    /// Share of classified later sources that link or credit the origin.
    /// `None` when no later source's text could be read.
    pub credit_rate: Option<f64>,
}

/// Compute propagation for one claim. `adopter_text(case_id)` returns the
/// later source's full text (only called for later sources); `None` means it
/// could not be read, which yields `CitationType::Unknown` for that source.
///
/// Returns `None` when fewer than two distinct dated sources exist or the
/// earliest date is shared.
pub fn compute_propagation(
    sources: &[SourceFacts],
    mut adopter_text: impl FnMut(&str) -> Option<String>,
) -> Option<Propagation> {
    // Distinct dated sources, earliest first; a duplicate capture keeps its
    // earliest-dated case.
    let mut dated: Vec<(&SourceFacts, i64, String)> = Vec::new();
    for s in sources {
        let Some(day) = s.published.as_deref().and_then(day_number) else {
            continue;
        };
        let url_key = s.url.as_deref().and_then(canonical_source_url);
        let dup = dated.iter_mut().find(|(d, _, d_url)| {
            d.identity == s.identity || (url_key.is_some() && url_key.as_deref() == Some(d_url))
        });
        match dup {
            Some(entry) if day < entry.1 => *entry = (s, day, url_key.unwrap_or_default()),
            Some(_) => {}
            None => dated.push((s, day, url_key.unwrap_or_default())),
        }
    }
    if dated.len() < 2 {
        return None;
    }
    dated.sort_by(|a, b| a.1.cmp(&b.1).then_with(|| a.0.case_id.cmp(&b.0.case_id)));
    if dated[0].1 == dated[1].1 {
        return None;
    }

    let (origin, origin_day, _) = dated[0];
    let probe = CreditProbe::new(origin);
    let adoptions: Vec<Adoption> = dated[1..]
        .iter()
        .map(|(s, day, _)| Adoption {
            case_id: s.case_id.clone(),
            published: s.published.clone().unwrap_or_default(),
            lag_days: day - origin_day,
            citation_type: adopter_text(&s.case_id)
                .map_or(CitationType::Unknown, |text| probe.classify(&text)),
        })
        .collect();

    let classified = adoptions
        .iter()
        .filter(|a| a.citation_type != CitationType::Unknown)
        .count();
    let credited = adoptions
        .iter()
        .filter(|a| {
            matches!(
                a.citation_type,
                CitationType::ExactLink | CitationType::Credit
            )
        })
        .count();
    Some(Propagation {
        origin_case_id: origin.case_id.clone(),
        origin_published: origin.published.clone().unwrap_or_default(),
        adoptions,
        credit_rate: (classified > 0).then(|| credited as f64 / classified as f64),
    })
}

/// Hosts that carry everyone's content. Their name appearing in a text says
/// nothing about crediting a particular source on them (on the dogfood vault
/// `x.com` / `github.com` mentions were ~97% of all host matches).
const PLATFORM_HOSTS: &[&str] = &[
    "x.com",
    "twitter.com",
    "github.com",
    "gist.github.com",
    "youtube.com",
    "youtu.be",
    "medium.com",
    "substack.com",
    "mp.weixin.qq.com",
    "weixin.qq.com",
    "zhihu.com",
    "zhuanlan.zhihu.com",
    "bilibili.com",
    "arxiv.org",
    "reddit.com",
    "news.ycombinator.com",
    "linkedin.com",
    "huggingface.co",
];

struct CreditProbe {
    /// Canonical origin URL, only when it names a document (has a path or an
    /// identity query) — a bare domain is a site, so linking it is a credit.
    exact: Option<String>,
    host: Option<String>,
    names: Vec<String>,
}

impl CreditProbe {
    fn new(origin: &SourceFacts) -> Self {
        let canon = origin.url.as_deref().and_then(canonical_source_url);
        let host_of = |c: &str| c.split(['/', '?']).next().unwrap_or("").to_string();
        let exact = canon.clone().filter(|c| c.len() > host_of(c).len());
        let host = canon
            .map(|c| host_of(&c))
            .filter(|h| h.contains('.') && !PLATFORM_HOSTS.contains(&h.as_str()));
        let names = origin
            .author
            .as_deref()
            .unwrap_or("")
            .split([',', '|', ';'])
            .map(|n| n.trim().to_lowercase())
            .filter(|n| is_distinctive_name(n))
            .collect();
        Self { exact, host, names }
    }

    fn classify(&self, text: &str) -> CitationType {
        // Whole URLs only, compared by canonical identity: `/post` must not
        // match `/postscript`, nor one status id a longer one.
        if let Some(exact) = self.exact.as_deref()
            && url_tokens(text).any(|u| canonical_source_url(u).as_deref() == Some(exact))
        {
            return CitationType::ExactLink;
        }
        let lower = text.to_lowercase();
        let names_origin = self.names.iter().any(|n| contains_bounded(&lower, n));
        if names_origin || self.host.as_deref().is_some_and(|h| contains_bounded(&lower, h)) {
            return CitationType::Credit;
        }
        CitationType::Inferred
    }
}

/// A single short word ("openai", "admin") matches far too much prose to
/// count as crediting a person; a full name or an @handle does not.
fn is_distinctive_name(n: &str) -> bool {
    let n = n.trim();
    if n.starts_with('@') {
        return n.len() >= 4;
    }
    n.split_whitespace().count() >= 2 && n.len() >= 6
}

/// Every `http(s)://…` token in `text`, cut at whitespace / markdown and
/// HTML delimiters, with trailing sentence punctuation dropped.
fn url_tokens(text: &str) -> impl Iterator<Item = &str> {
    // ASCII lowercasing keeps byte offsets, so indices slice `text` directly.
    let lower = text.to_ascii_lowercase();
    let starts: Vec<usize> = lower
        .match_indices("http")
        .filter(|(i, _)| lower[*i..].starts_with("http://") || lower[*i..].starts_with("https://"))
        .map(|(i, _)| i)
        .collect();
    starts.into_iter().map(move |i| {
        let rest = &text[i..];
        let end = rest
            .find(|c: char| c.is_whitespace() || matches!(c, ')' | ']' | '>' | '<' | '"' | '\'' | '`'))
            .unwrap_or(rest.len());
        rest[..end].trim_end_matches(['.', ',', ';', ':', '!', '?'])
    })
}

/// `needle` occurs in `hay` not glued to a longer word or host on either
/// side (`blog.a.io` must not match `sub.blog.a.io` or `blog.a.iox`).
fn contains_bounded(hay: &str, needle: &str) -> bool {
    let glue = |c: char| c.is_alphanumeric() || matches!(c, '.' | '-' | '_' | '@');
    hay.match_indices(needle).any(|(i, m)| {
        let before = hay[..i].chars().next_back();
        let after = hay[i + m.len()..].chars().next();
        // A trailing '.' ends a sentence, not a host.
        let after_glued = match after {
            Some('.') => hay[i + m.len() + 1..].chars().next().is_some_and(|c| c.is_alphanumeric()),
            Some(c) => glue(c),
            None => false,
        };
        !before.is_some_and(glue) && !after_glued
    })
}

/// Days since 1970-01-01 for a strict `YYYY-MM-DD`; `None` otherwise.
fn day_number(s: &str) -> Option<i64> {
    let b = s.as_bytes();
    if b.len() != 10 || b[4] != b'-' || b[7] != b'-' {
        return None;
    }
    let y: i64 = s[..4].parse().ok()?;
    let m: i64 = s[5..7].parse().ok()?;
    let d: i64 = s[8..10].parse().ok()?;
    let leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
    let month_len = match m {
        2 if leap => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        1..=12 => 31,
        _ => return None,
    };
    if !(1..=month_len).contains(&d) {
        return None; // 2026-02-31 is undated, not silently 2026-03-03
    }
    // Howard Hinnant's days_from_civil.
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    Some(era * 146_097 + doe - 719_468)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn src(case: &str, published: Option<&str>, url: &str, author: Option<&str>) -> SourceFacts {
        SourceFacts {
            case_id: case.into(),
            identity: format!("sha-{case}"),
            published: published.map(str::to_string),
            url: Some(url.into()),
            author: author.map(str::to_string),
        }
    }

    fn run(sources: &[SourceFacts], texts: &[(&str, &str)]) -> Option<Propagation> {
        let texts: HashMap<&str, &str> = texts.iter().copied().collect();
        compute_propagation(sources, |c| texts.get(c).map(|t| (*t).to_string()))
    }

    #[test]
    fn origin_is_earliest_publication_and_adoptions_sorted_with_calendar_lag() {
        let sources = vec![
            src("c", Some("2026-03-01"), "https://c.dev/post", None),
            src(
                "a",
                Some("2026-02-28"),
                "https://blog.a.io/memory",
                Some("Ada Lovelace"),
            ),
            src("b", Some("2026-03-10"), "https://b.dev/x", None),
        ];
        let p = run(&sources, &[("b", "plain"), ("c", "plain")]).unwrap();
        assert_eq!(p.origin_case_id, "a");
        assert_eq!(p.origin_published, "2026-02-28");
        let order: Vec<(&str, i64)> = p
            .adoptions
            .iter()
            .map(|a| (a.case_id.as_str(), a.lag_days))
            .collect();
        // Feb 28 → Mar 1 is ONE day (the old y*372+m*31+d made it 4).
        assert_eq!(order, vec![("c", 1), ("b", 10)]);
    }

    #[test]
    fn classifies_exact_link_credit_and_inferred_and_computes_credit_rate() {
        let sources = vec![
            src(
                "o",
                Some("2026-01-01"),
                "https://www.blog.a.io/memory/?utm_source=x",
                Some("Ada Lovelace"),
            ),
            src("link", Some("2026-01-02"), "https://l.dev/1", None),
            src("credit", Some("2026-01-03"), "https://cr.dev/1", None),
            src("site", Some("2026-01-04"), "https://s.dev/1", None),
            src("none", Some("2026-01-05"), "https://n.dev/1", None),
            src("unread", Some("2026-01-06"), "https://u.dev/1", None),
        ];
        let p = run(
            &sources,
            &[
                ("link", "see HTTPS://blog.a.io/memory for details"),
                ("credit", "as Ada Lovelace argued last week"),
                ("site", "a post on blog.a.io said so"),
                ("none", "memory is scarce"),
            ],
        )
        .unwrap();
        let types: Vec<CitationType> = p.adoptions.iter().map(|a| a.citation_type).collect();
        assert_eq!(
            types,
            vec![
                CitationType::ExactLink,
                CitationType::Credit,
                CitationType::Credit,
                CitationType::Inferred,
                CitationType::Unknown,
            ]
        );
        // 3 credited of 4 classified; the unreadable one is not counted.
        assert_eq!(p.credit_rate, Some(0.75));
    }

    #[test]
    fn platform_hosts_and_single_word_authors_do_not_count_as_credit() {
        let sources = vec![
            src("o", Some("2026-01-01"), "https://x.com", Some("openai")),
            src("later", Some("2026-01-02"), "https://l.dev/1", None),
        ];
        let p = run(&sources, &[("later", "openai posted this on x.com")]).unwrap();
        assert_eq!(p.adoptions[0].citation_type, CitationType::Inferred);
        assert_eq!(p.credit_rate, Some(0.0));
    }

    #[test]
    fn tie_for_earliest_or_too_few_dated_sources_yields_none() {
        let tie = vec![
            src("a", Some("2026-01-01"), "https://a.dev/1", None),
            src("b", Some("2026-01-01"), "https://b.dev/1", None),
            src("c", Some("2026-01-09"), "https://c.dev/1", None),
        ];
        assert!(run(&tie, &[]).is_none());

        let undated = vec![
            src("a", Some("2026-01-01"), "https://a.dev/1", None),
            src("b", None, "https://b.dev/1", None),
            src("c", Some("not-a-date"), "https://c.dev/1", None),
        ];
        assert!(run(&undated, &[]).is_none());
        assert!(run(&[], &[]).is_none());
    }

    #[test]
    fn duplicate_captures_of_one_source_are_one_source() {
        // Same URL captured twice (different content hash): not an adoption.
        let mut dup = src("a2", Some("2026-01-05"), "https://a.dev/post/", None);
        dup.identity = "sha-other".into();
        let sources = vec![
            src("a1", Some("2026-01-01"), "https://a.dev/post", None),
            dup,
        ];
        assert!(run(&sources, &[("a2", "https://a.dev/post")]).is_none());
    }

    #[test]
    fn exact_link_compares_whole_canonical_urls() {
        let sources = vec![
            src("o", Some("2026-01-01"), "https://Example.com/post/?utm_source=x", None),
            src("prefix", Some("2026-01-02"), "https://p.dev/1", None),
            src("same", Some("2026-01-03"), "https://s.dev/1", None),
        ];
        let p = run(
            &sources,
            &[
                ("prefix", "see https://example.com/postscript for more"),
                ("same", "via [this](https://www.example.com/post?utm_medium=y)."),
            ],
        )
        .unwrap();
        let types: Vec<CitationType> = p.adoptions.iter().map(|a| a.citation_type).collect();
        // `prefix` names the origin's site (a credit), but never its document.
        assert_eq!(types, vec![CitationType::Credit, CitationType::ExactLink]);
    }

    #[test]
    fn identity_query_parameters_keep_distinct_documents_apart() {
        let sources = vec![
            src("v1", Some("2026-01-01"), "https://youtube.com/watch?v=AAA", None),
            src("v2", Some("2026-01-02"), "https://youtube.com/watch?v=BBB", None),
        ];
        let p = run(&sources, &[("v2", "https://youtube.com/watch?v=BBB")]).unwrap();
        assert_eq!(p.adoptions.len(), 1, "different videos are different sources");
        assert_eq!(p.adoptions[0].citation_type, CitationType::Inferred);
    }

    #[test]
    fn host_and_name_matches_need_word_boundaries() {
        assert!(contains_bounded("a post on blog.a.io said", "blog.a.io"));
        assert!(contains_bounded("ends with blog.a.io.", "blog.a.io"));
        assert!(!contains_bounded("see sub.blog.a.io", "blog.a.io"));
        assert!(!contains_bounded("see blog.a.iox", "blog.a.io"));
        assert!(contains_bounded("as ada lovelace said", "ada lovelace"));
        assert!(!contains_bounded("as ada lovelacey said", "ada lovelace"));
    }

    #[test]
    fn day_number_is_a_real_calendar() {
        assert_eq!(day_number("1970-01-01"), Some(0));
        assert_eq!(
            day_number("2024-03-01").unwrap() - day_number("2024-02-28").unwrap(),
            2
        );
        assert_eq!(
            day_number("2026-01-01").unwrap() - day_number("2025-12-31").unwrap(),
            1
        );
        assert_eq!(day_number("2026-13-01"), None);
        assert_eq!(day_number("2026-02-31"), None);
        assert_eq!(day_number("2026-02-29"), None);
        assert!(day_number("2024-02-29").is_some());
        assert_eq!(day_number("1900-02-29"), None);
        assert!(day_number("2000-02-29").is_some());
        assert_eq!(day_number("20260101"), None);
    }
}
