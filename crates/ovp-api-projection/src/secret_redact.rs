//! Deterministic secret / high-entropy token redaction for public projections.
//!
//! Applied at the publish choke-point so vault authority is never rewritten,
//! while static sites cannot leak provider keys or high-entropy tokens that
//! rode in via source quotes or claim text.
//!
//! Same secret value → same placeholder `«redacted:<sha256-8>»` (stable across
//! fields). Pure-Rust scanners (no regex crate).

use sha2::{Digest, Sha256};
use std::collections::HashMap;

/// Stable placeholder for a redacted secret value.
pub fn redacted_placeholder(secret: &str) -> String {
    let mut h = Sha256::new();
    h.update(secret.as_bytes());
    let dig = format!("{:x}", h.finalize());
    format!("«redacted:{}»", &dig[..8])
}

/// Result of scanning one string.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RedactReport {
    pub text: String,
    pub hits: usize,
}

/// Redact known secret shapes and high-entropy tokens in `input`.
pub fn redact_secrets(input: &str) -> RedactReport {
    if input.is_empty() {
        return RedactReport {
            text: String::new(),
            hits: 0,
        };
    }
    let mut cache: HashMap<String, String> = HashMap::new();
    let mut hits = 0usize;
    let mut out = String::with_capacity(input.len());
    let bytes = input.as_bytes();
    let mut i = 0usize;

    while i < bytes.len() {
        // PEM private key blocks (may span lines).
        if let Some((end, secret)) = match_pem_private_key(&input[i..]) {
            hits += 1;
            let ph = cache
                .entry(secret.to_string())
                .or_insert_with(|| redacted_placeholder(secret))
                .clone();
            out.push_str(&ph);
            i += end;
            continue;
        }

        // Token-shaped secrets starting at a word boundary.
        if is_token_start(bytes, i) {
            if let Some((len, secret)) = match_known_secret(&input[i..]) {
                hits += 1;
                let ph = cache
                    .entry(secret.to_string())
                    .or_insert_with(|| redacted_placeholder(secret))
                    .clone();
                out.push_str(&ph);
                i += len;
                continue;
            }
            if let Some((len, secret)) = match_env_assignment(&input[i..]) {
                hits += 1;
                let ph = cache
                    .entry(secret.to_string())
                    .or_insert_with(|| redacted_placeholder(secret))
                    .clone();
                out.push_str(&ph);
                i += len;
                continue;
            }
            if let Some((len, secret)) = match_high_entropy_token(&input[i..]) {
                hits += 1;
                let ph = cache
                    .entry(secret.to_string())
                    .or_insert_with(|| redacted_placeholder(secret))
                    .clone();
                out.push_str(&ph);
                i += len;
                continue;
            }
        }

        // Advance one char.
        let ch = input[i..].chars().next().unwrap();
        out.push(ch);
        i += ch.len_utf8();
    }

    RedactReport { text: out, hits }
}

/// Convenience: redact and return only the text.
pub fn redact_text(input: &str) -> String {
    redact_secrets(input).text
}

/// Scrub every public-facing string field on a durable record.
pub fn scrub_durable_record(record: &mut ovp_domain::crystal::DurableRecord) -> usize {
    let mut hits = 0usize;
    let r = redact_secrets(&record.claim);
    hits += r.hits;
    record.claim = r.text;
    let r = redact_secrets(&record.strength_rationale);
    hits += r.hits;
    record.strength_rationale = r.text;
    for c in record.citations.iter_mut() {
        let r = redact_secrets(&c.quote);
        hits += r.hits;
        c.quote = r.text;
    }
    hits
}

/// Scrub claim text and titles on an already-PublicView index model.
pub fn scrub_index_model(model: &mut ovp_index::IndexModel) -> usize {
    let mut hits = 0usize;
    for c in model.claims.iter_mut() {
        let r = redact_secrets(&c.claim);
        hits += r.hits;
        c.claim = r.text;
    }
    for s in model.sources.iter_mut() {
        if let Some(title) = s.title.as_mut() {
            let r = redact_secrets(title);
            hits += r.hits;
            *title = r.text;
        }
    }
    for p in model.packs.iter_mut() {
        let r = redact_secrets(&p.title);
        hits += r.hits;
        p.title = r.text;
    }
    hits
}

fn is_token_start(bytes: &[u8], i: usize) -> bool {
    if i == 0 {
        return true;
    }
    let prev = bytes[i - 1];
    !prev.is_ascii_alphanumeric() && prev != b'_' && prev != b'-'
}

fn match_pem_private_key(s: &str) -> Option<(usize, &str)> {
    const BEGIN: &str = "-----BEGIN ";
    const END_MARK: &str = "PRIVATE KEY-----";
    if !s.starts_with(BEGIN) {
        return None;
    }
    let first_line_end = s.find('\n').unwrap_or(s.len());
    let header = &s[..first_line_end];
    if !header.contains("PRIVATE KEY") {
        return None;
    }
    // Find the matching END … PRIVATE KEY----- after the header.
    let mut search_from = first_line_end;
    while let Some(rel) = s[search_from..].find("-----END ") {
        let start = search_from + rel;
        let rest = &s[start..];
        if let Some(end_rel) = rest.find(END_MARK) {
            let end = start + end_rel + END_MARK.len();
            return Some((end, &s[..end]));
        }
        search_from = start + "-----END ".len();
    }
    None
}

fn match_known_secret(s: &str) -> Option<(usize, &str)> {
    // Bearer <token>
    if let Some(rest) = strip_prefix_ci(s, "Bearer ") {
        let tok = take_while(rest, |c| {
            c.is_ascii_alphanumeric() || matches!(c, '-' | '.' | '_' | '~' | '+' | '/')
        });
        if tok.len() >= 12 {
            let full_len = "Bearer ".len() + tok.len();
            // optional trailing =
            let mut full_len = full_len;
            let after = &s[full_len..];
            let pads = after.chars().take_while(|c| *c == '=').count();
            full_len += pads;
            return Some((full_len, &s[..full_len]));
        }
    }

    // Prefix-keyed secrets.
    let prefixes: &[(&str, usize, fn(char) -> bool)] = &[
        ("sk-ant-api03-", 20, is_secret_body),
        ("sk-proj-", 16, is_secret_body),
        ("sk-live-", 16, is_secret_body),
        ("sk-test-", 16, is_secret_body),
        ("sk-", 20, is_secret_body),
        ("ghp_", 20, is_secret_body),
        ("gho_", 20, is_secret_body),
        ("ghu_", 20, is_secret_body),
        ("ghs_", 20, is_secret_body),
        ("ghr_", 20, is_secret_body),
        ("github_pat_", 20, is_secret_body),
        ("xoxb-", 10, is_slack_body),
        ("xoxp-", 10, is_slack_body),
        ("xoxa-", 10, is_slack_body),
        ("xoxr-", 10, is_slack_body),
        ("xoxs-", 10, is_slack_body),
        ("AIza", 20, is_secret_body),
        ("AKIA", 16, |c| c.is_ascii_uppercase() || c.is_ascii_digit()),
        ("sk_live_", 16, is_secret_body),
        ("sk_test_", 16, is_secret_body),
        ("rk_live_", 16, is_secret_body),
        ("rk_test_", 16, is_secret_body),
        ("pk_live_", 16, is_secret_body),
        ("pk_test_", 16, is_secret_body),
    ];

    for (prefix, min_body, ok) in prefixes {
        if let Some(rest) = s.strip_prefix(prefix) {
            let body = take_while(rest, ok);
            if body.len() >= *min_body {
                let len = prefix.len() + body.len();
                return Some((len, &s[..len]));
            }
        }
    }
    None
}

fn is_secret_body(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_' || c == '-'
}

fn is_slack_body(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '-'
}

fn match_env_assignment(s: &str) -> Option<(usize, &str)> {
    const KEYS: &[&str] = &[
        "api_key",
        "api-key",
        "apikey",
        "secret_key",
        "secret-key",
        "access_token",
        "access-token",
        "auth_token",
        "auth-token",
        "private_key",
        "private-key",
        "password",
        "passwd",
        "client_secret",
        "client-secret",
    ];
    let lower = s.to_ascii_lowercase();
    for key in KEYS {
        if !lower.starts_with(key) {
            continue;
        }
        let after_key = &s[key.len()..];
        let trimmed = after_key.trim_start();
        let ws = after_key.len() - trimmed.len();
        if !trimmed.starts_with('=') {
            continue;
        }
        let after_eq = trimmed[1..].trim_start();
        let ws2 = trimmed[1..].len() - after_eq.len();
        let val = take_while(after_eq, |c| !c.is_whitespace() && c != '#' && c != '\'' && c != '"');
        if val.len() >= 8 {
            let len = key.len() + ws + 1 + ws2 + val.len();
            return Some((len, &s[..len]));
        }
    }
    None
}

fn match_high_entropy_token(s: &str) -> Option<(usize, &str)> {
    let tok = take_while(s, |c| c.is_ascii_alphanumeric() || matches!(c, '+' | '/' | '=' | '_' | '-'));
    if tok.len() < 20 {
        return None;
    }
    if shannon_entropy(tok) <= 4.5 {
        return None;
    }
    if !looks_like_secret_token(tok) {
        return None;
    }
    Some((tok.len(), tok))
}

fn take_while(s: &str, mut pred: impl FnMut(char) -> bool) -> &str {
    let end = s
        .char_indices()
        .find(|(_, c)| !pred(*c))
        .map(|(i, _)| i)
        .unwrap_or(s.len());
    &s[..end]
}

fn strip_prefix_ci<'a>(s: &'a str, prefix: &str) -> Option<&'a str> {
    if s.len() < prefix.len() {
        return None;
    }
    if s[..prefix.len()].eq_ignore_ascii_case(prefix) {
        Some(&s[prefix.len()..])
    } else {
        None
    }
}

fn looks_like_secret_token(tok: &str) -> bool {
    let has_digit = tok.chars().any(|c| c.is_ascii_digit());
    let has_upper = tok.chars().any(|c| c.is_ascii_uppercase());
    let has_lower = tok.chars().any(|c| c.is_ascii_lowercase());
    let has_special = tok.chars().any(|c| matches!(c, '+' | '/' | '=' | '_' | '-'));
    (has_digit && (has_upper || has_lower)) || (has_special && has_digit)
}

fn shannon_entropy(s: &str) -> f64 {
    if s.is_empty() {
        return 0.0;
    }
    let mut counts = [0u32; 256];
    let mut n = 0u32;
    for b in s.bytes() {
        counts[b as usize] += 1;
        n += 1;
    }
    let n = f64::from(n);
    counts
        .iter()
        .filter(|&&c| c > 0)
        .map(|&c| {
            let p = f64::from(c) / n;
            -p * p.log2()
        })
        .sum()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacts_openai_style_key() {
        let s = "use sk-abcdefghijklmnopqrstuvwxyz012345 for auth";
        let r = redact_secrets(s);
        assert!(r.hits >= 1, "hits={} text={}", r.hits, r.text);
        assert!(!r.text.contains("sk-abcdefghijklmnopqrstuvwxyz012345"));
        assert!(r.text.contains("«redacted:"));
    }

    #[test]
    fn redacts_github_pat() {
        let s = "token ghp_abcdefghijklmnopqrstuvwx";
        let r = redact_secrets(s);
        assert!(r.hits >= 1);
        assert!(!r.text.contains("ghp_abcdefghijklmnopqrstuvwx"));
    }

    #[test]
    fn redacts_aws_akia() {
        let s = "key AKIAIOSFODNN7EXAMPLE here";
        let r = redact_secrets(s);
        assert!(r.hits >= 1);
        assert!(!r.text.contains("AKIAIOSFODNN7EXAMPLE"));
    }

    #[test]
    fn redacts_private_key_block() {
        let s = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----";
        let r = redact_secrets(s);
        assert!(r.hits >= 1, "{}", r.text);
        assert!(!r.text.contains("BEGIN OPENSSH"));
    }

    #[test]
    fn same_secret_same_placeholder() {
        let a = redact_secrets("sk-abcdefghijklmnopqrstuvwxyz012345").text;
        let b = redact_secrets("prefix sk-abcdefghijklmnopqrstuvwxyz012345 suffix").text;
        let ph = a.trim();
        assert!(b.contains(ph), "a={a} b={b}");
    }

    #[test]
    fn leaves_normal_prose() {
        let s = "OVP keeps evidence chains intact and never compacts quotes.";
        let r = redact_secrets(s);
        assert_eq!(r.hits, 0, "{}", r.text);
        assert_eq!(r.text, s);
    }

    #[test]
    fn env_assignment_redacted() {
        let s = "API_KEY=supersecretvalue12345";
        let r = redact_secrets(s);
        assert!(r.hits >= 1, "{}", r.text);
        assert!(!r.text.contains("supersecretvalue12345"));
    }
}
