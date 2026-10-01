//! Source identity for the durable gate's "≥2 distinct sources" rule.
//!
//! A `case_id` is a reader-pack directory, not a source. The live vault has
//! two pack-naming layouts and re-captures of the same link, so one article
//! can sit behind two case_ids — and counting case_ids let 47 durable claims
//! through on a single source each (INV-915). This maps every case to the
//! identity of the thing it was read from.
//!
//! Only DECIDABLE identities are used: the captured content's sha256 and its
//! canonical URL. Titles and authors are deliberately not: two people publish
//! "Anatomy of an Agent Harness", and collapsing them would hide real
//! corroboration. Whether two different documents rest on the same underlying
//! study is a judgment call and stays out of the gate.
//!
//! The mapping is read from the built read-model (`.ovp/index/index.json`),
//! which already joins packs to their source sha and URL. A case it does not
//! know falls back to its own case_id and is reported as unresolved — the
//! count can only stay where it was, never go up.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

/// `case_id` → source identity key (`url:<canonical>` or `sha:<sha256>`).
pub type SourceIdentities = BTreeMap<String, String>;

/// Query parameters that only record HOW a link was shared. Anything else is
/// kept: on YouTube `v`/`list` ARE the identity, and dropping every query
/// would fold every video into `youtube.com/watch`.
fn is_tracking_param(key: &str) -> bool {
    let k = key.to_ascii_lowercase();
    k.starts_with("utm_")
        || matches!(
            k.as_str(),
            "fbclid" | "gclid" | "igshid" | "si" | "ref_src" | "ref_url" | "mc_cid" | "mc_eid"
        )
}

/// Canonical form of a source URL, or `None` when it is not an absolute URL.
///
/// Lowercases scheme and host, drops `www.`, fragments, tracking parameters
/// and a trailing slash, and folds every form of a tweet link
/// (`twitter.com/<User>/status/<id>?s=46&t=…`) to `x.com/i/status/<id>`,
/// since the status id alone identifies it and handles change case freely.
pub fn canonical_source_url(raw: &str) -> Option<String> {
    let raw = raw.trim();
    let (scheme, rest) = raw.split_once("://")?;
    let scheme = scheme.to_ascii_lowercase();
    if scheme != "http" && scheme != "https" {
        return None;
    }
    let rest = rest.split('#').next().unwrap_or("");
    let (host_path, query) = match rest.split_once('?') {
        Some((hp, q)) => (hp, Some(q)),
        None => (rest, None),
    };
    let (host, path) = match host_path.find('/') {
        Some(i) => (&host_path[..i], &host_path[i..]),
        None => (host_path, ""),
    };
    let mut host = host.to_ascii_lowercase();
    if let Some(h) = host.strip_prefix("www.") {
        host = h.to_string();
    }
    if host.is_empty() {
        return None;
    }
    if matches!(
        host.as_str(),
        "twitter.com" | "mobile.twitter.com" | "x.com" | "mobile.x.com"
    ) {
        let segs: Vec<&str> = path.split('/').filter(|s| !s.is_empty()).collect();
        if let Some(i) = segs.iter().position(|s| *s == "status")
            && let Some(id) = segs.get(i + 1)
            && !id.is_empty()
            && id.bytes().all(|b| b.is_ascii_digit())
        {
            return Some(format!("x.com/i/status/{id}"));
        }
        host = "x.com".into();
    }
    let path = path.trim_end_matches('/');
    let mut kept: Vec<&str> = query
        .unwrap_or("")
        .split('&')
        .filter(|kv| !kv.is_empty())
        .filter(|kv| !is_tracking_param(kv.split('=').next().unwrap_or("")))
        .collect();
    kept.sort_unstable();
    let mut out = format!("{host}{path}");
    if !kept.is_empty() {
        out.push('?');
        out.push_str(&kept.join("&"));
    }
    Some(out)
}

/// Build the case → identity map from the text of `.ovp/index/index.json`.
///
/// A pack's identity is its source's canonical URL when the source has one,
/// else its sha256. A pack with no joined source is left out (unresolved).
pub fn identities_from_index_json(text: &str) -> Result<SourceIdentities, String> {
    let v: serde_json::Value =
        serde_json::from_str(text).map_err(|e| format!("parsing index: {e}"))?;
    let mut url_of_sha: BTreeMap<&str, String> = BTreeMap::new();
    for s in v
        .get("sources")
        .and_then(|s| s.as_array())
        .into_iter()
        .flatten()
    {
        let (Some(sha), Some(url)) = (
            s.get("sha256").and_then(|x| x.as_str()),
            s.get("url").and_then(|x| x.as_str()),
        ) else {
            continue;
        };
        if let Some(canon) = canonical_source_url(url) {
            url_of_sha.insert(sha, canon);
        }
    }
    let mut out = SourceIdentities::new();
    for p in v
        .get("packs")
        .and_then(|p| p.as_array())
        .into_iter()
        .flatten()
    {
        let (Some(dir), Some(sha)) = (
            p.get("pack_dir").and_then(|x| x.as_str()),
            p.get("source_sha256").and_then(|x| x.as_str()),
        ) else {
            continue;
        };
        let Some(case_id) = dir
            .trim_end_matches('/')
            .rsplit('/')
            .next()
            .filter(|s| !s.is_empty())
        else {
            continue;
        };
        let key = match url_of_sha.get(sha) {
            Some(url) => format!("url:{url}"),
            None => format!("sha:{sha}"),
        };
        out.insert(case_id.to_string(), key);
    }
    Ok(out)
}

/// Legacy corpus packs are named `<hash8>-<date>_<title>…`; packs written by
/// the current reader are `<date>_<title>-<hash8>`.
fn is_legacy_layout(case_id: &str) -> bool {
    let b = case_id.as_bytes();
    b.len() > 9 && b[8] == b'-' && b[..8].iter().all(u8::is_ascii_hexdigit)
}

/// Packs that duplicate another pack of the SAME source and should be left
/// out of new synthesis (INV-930).
///
/// One per source is kept: a current-layout pack over a legacy one (the
/// 2026-06-15 regeneration is the current extraction), then the
/// lexicographically first name — date-first names make that the earliest
/// capture, the same copy intake's legacy dedup keeps. Deterministic, and a
/// pure projection: nothing on disk is marked, so dropping this rule restores
/// the old behaviour.
///
/// The shadowed packs are NOT gone: existing claims cite them and citations
/// keep resolving against them. Only the choice of what new claims may cite
/// changes.
pub fn shadowed_cases(ids: &SourceIdentities) -> BTreeSet<String> {
    let mut by_source: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
    for (case, key) in ids {
        by_source
            .entry(key.as_str())
            .or_default()
            .push(case.as_str());
    }
    let mut out = BTreeSet::new();
    for cases in by_source.into_values().filter(|c| c.len() > 1) {
        let keep = cases
            .iter()
            .copied()
            .min_by_key(|c| (is_legacy_layout(c), *c))
            .expect("non-empty group");
        out.extend(cases.into_iter().filter(|c| *c != keep).map(String::from));
    }
    out
}

/// Load the identity map for a vault. A vault with no built index yet yields
/// an empty map (every case unresolved → the gate counts case_ids exactly as
/// before); an index that exists but cannot be read is an error.
pub fn load_source_identities(vault_root: &Path) -> Result<SourceIdentities, String> {
    let path = vault_root.join(crate::VaultLayout::new().index_file());
    match std::fs::read_to_string(&path) {
        Ok(text) => {
            identities_from_index_json(&text).map_err(|e| format!("{}: {e}", path.display()))
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(SourceIdentities::new()),
        Err(e) => Err(format!("reading {}: {e}", path.display())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tweet_links_fold_to_the_status_id() {
        // Both forms are verbatim from the live vault (one pair of "two sources").
        let a = canonical_source_url("https://x.com/Vtrivedy10/status/2041927488918413589");
        let b =
            canonical_source_url("https://x.com/vtrivedy10/status/2041927488918413589?s=46&t=xyz");
        let c = canonical_source_url("https://twitter.com/vtrivedy10/status/2041927488918413589/");
        assert_eq!(a.as_deref(), Some("x.com/i/status/2041927488918413589"));
        assert_eq!(a, b);
        assert_eq!(a, c);
    }

    #[test]
    fn tracking_params_fragment_case_and_slash_are_dropped() {
        let a = canonical_source_url("https://WWW.Example.com/post/?utm_source=x&fbclid=1#top");
        assert_eq!(a.as_deref(), Some("example.com/post"));
    }

    #[test]
    fn identity_bearing_query_params_are_kept() {
        let v1 = canonical_source_url("https://www.youtube.com/watch?v=AAA&si=share");
        let v2 = canonical_source_url("https://www.youtube.com/watch?v=BBB");
        assert_eq!(v1.as_deref(), Some("youtube.com/watch?v=AAA"));
        assert_ne!(v1, v2);
        // Parameter order does not create a new source.
        assert_eq!(
            canonical_source_url("https://e.com/p?b=2&a=1"),
            canonical_source_url("https://e.com/p?a=1&b=2")
        );
    }

    #[test]
    fn non_urls_have_no_canonical_form() {
        for raw in [
            "",
            "not a url",
            "file:///tmp/x.md",
            "https://",
            "mailto:a@b.c",
        ] {
            assert_eq!(canonical_source_url(raw), None, "{raw}");
        }
    }

    #[test]
    fn index_maps_packs_to_url_then_sha() {
        let text = r#"{
          "sources": [
            {"sha256": "s1", "url": "https://x.com/a/status/1?s=46"},
            {"sha256": "s2", "url": "https://x.com/A/status/1"},
            {"sha256": "s3"}
          ],
          "packs": [
            {"pack_dir": "40-Resources/Reader/old-s1", "source_sha256": "s1"},
            {"pack_dir": "40-Resources/Reader/new-s1", "source_sha256": "s1"},
            {"pack_dir": "40-Resources/Reader/recapture", "source_sha256": "s2"},
            {"pack_dir": "40-Resources/Reader/clipping", "source_sha256": "s3"},
            {"pack_dir": "40-Resources/Reader/orphan"}
          ]
        }"#;
        let ids = identities_from_index_json(text).unwrap();
        assert_eq!(ids["old-s1"], "url:x.com/i/status/1");
        assert_eq!(ids["new-s1"], ids["old-s1"]);
        assert_eq!(ids["recapture"], ids["old-s1"]);
        assert_eq!(ids["clipping"], "sha:s3");
        assert!(!ids.contains_key("orphan"));
    }

    #[test]
    fn one_pack_per_source_prefers_current_layout_then_earliest() {
        let ids: SourceIdentities = [
            // 06-15 regeneration: legacy + current layout of one capture.
            (
                "108a8866-2026-06-13_how_to_design_agent_UIs-108a8866_",
                "sha:a",
            ),
            ("2026-06-15_how to design agent UIs-108a8866", "sha:a"),
            // Two current-layout captures of one URL: earliest is kept.
            ("2026-04-09_Better Harness-14b0759f", "url:x.com/i/status/1"),
            ("2026-04-10_Better Harness-717e8484", "url:x.com/i/status/1"),
            // Only legacy copies: earliest legacy is kept.
            ("aaaa0000-2026-01-01_X_", "sha:c"),
            ("bbbb0000-2026-01-02_X_", "sha:c"),
            // A unique source is never shadowed.
            ("2026-05-01_Unique-cccc1111", "sha:d"),
        ]
        .into_iter()
        .map(|(c, k)| (c.to_string(), k.to_string()))
        .collect();
        let shadowed: Vec<String> = shadowed_cases(&ids).into_iter().collect();
        assert_eq!(
            shadowed,
            vec![
                "108a8866-2026-06-13_how_to_design_agent_UIs-108a8866_",
                "2026-04-10_Better Harness-717e8484",
                "bbbb0000-2026-01-02_X_",
            ]
        );
        assert!(shadowed_cases(&SourceIdentities::new()).is_empty());
    }

    #[test]
    fn a_vault_without_an_index_is_empty_but_a_corrupt_one_errors() {
        let dir = tempfile::tempdir().unwrap();
        assert!(load_source_identities(dir.path()).unwrap().is_empty());
        let idx = dir.path().join(".ovp/index");
        std::fs::create_dir_all(&idx).unwrap();
        std::fs::write(idx.join("index.json"), "{not json").unwrap();
        assert!(load_source_identities(dir.path()).is_err());
    }
}
