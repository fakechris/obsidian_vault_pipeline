use std::collections::BTreeSet;

pub fn tokenize_for_search(input: &str) -> Vec<String> {
    let mut out: BTreeSet<String> = BTreeSet::new();
    let mut ascii = String::new();
    let mut cjk_run = String::new();

    let flush_ascii = |buf: &mut String, out: &mut BTreeSet<String>| {
        if !buf.is_empty() {
            out.insert(std::mem::take(buf));
        }
    };
    let flush_cjk = |buf: &mut String, out: &mut BTreeSet<String>| {
        let chars: Vec<char> = buf.chars().collect();
        match chars.len() {
            0 => {}
            1 => {
                out.insert(chars[0].to_string());
            }
            _ => {
                for pair in chars.windows(2) {
                    out.insert(pair.iter().collect());
                }
            }
        }
        buf.clear();
    };

    for ch in input.chars() {
        if ch.is_ascii_alphanumeric() {
            flush_cjk(&mut cjk_run, &mut out);
            ascii.push(ch.to_ascii_lowercase());
        } else if is_cjk(ch) {
            flush_ascii(&mut ascii, &mut out);
            cjk_run.push(ch);
        } else {
            flush_ascii(&mut ascii, &mut out);
            flush_cjk(&mut cjk_run, &mut out);
        }
    }
    flush_ascii(&mut ascii, &mut out);
    flush_cjk(&mut cjk_run, &mut out);
    out.into_iter().collect()
}

pub fn lexical_score(query: &str, fields: &[&str]) -> f64 {
    let q = query.trim();
    if q.is_empty() || fields.is_empty() {
        return 0.0;
    }
    let q_lower = q.to_lowercase();
    let q_tokens = tokenize_for_search(q);
    if q_tokens.is_empty() {
        return 0.0;
    }

    let mut score = 0.0;
    for field in fields {
        let f_lower = field.to_lowercase();
        if f_lower.contains(&q_lower) {
            score += 10.0;
        }
        let f_tokens: BTreeSet<String> = tokenize_for_search(field).into_iter().collect();
        for token in &q_tokens {
            if f_tokens.contains(token) {
                score += 1.0;
            }
        }
    }
    score
}

fn is_cjk(ch: char) -> bool {
    matches!(
        ch as u32,
        0x3400..=0x4DBF
            | 0x4E00..=0x9FFF
            | 0xF900..=0xFAFF
            | 0x20000..=0x2A6DF
            | 0x2A700..=0x2B73F
            | 0x2B740..=0x2B81F
            | 0x2B820..=0x2CEAF
    )
}

#[cfg(test)]
mod tests {
    use crate::score::{lexical_score, tokenize_for_search};

    #[test]
    fn tokenizes_ascii_words_and_cjk_bigrams() {
        let tokens = tokenize_for_search("Agent memory 代理记忆系统");

        assert!(tokens.contains(&"agent".to_string()));
        assert!(tokens.contains(&"memory".to_string()));
        assert!(tokens.contains(&"代理".to_string()));
        assert!(tokens.contains(&"理记".to_string()));
        assert!(tokens.contains(&"记忆".to_string()));
    }

    #[test]
    fn scores_ascii_and_cjk_matches_above_misses() {
        let ascii = lexical_score("agent memory", &["Agent memory systems"]);
        let cjk = lexical_score("记忆", &["代理记忆系统"]);
        let miss = lexical_score("polymarket", &["Agent memory systems"]);

        assert!(ascii > miss);
        assert!(cjk > miss);
        assert_eq!(lexical_score("", &["Agent memory systems"]), 0.0);
    }
}
