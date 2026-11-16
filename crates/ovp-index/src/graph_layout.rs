//! Precompute portal graph coordinates at index-build time.

use std::collections::{BTreeMap, BTreeSet};

use ovp_domain::graph_layout;

use crate::model::{ClaimRow, ClaimStatus, PackRow, SourceRow};

/// Attach `[x, y]` layout coordinates to claim and source rows.
pub fn attach_graph_layout(claims: &mut [ClaimRow], sources: &mut [SourceRow], packs: &[PackRow]) {
    layout_claims(claims);
    layout_sources(claims, sources, packs);
}

fn claim_graph_key(c: &ClaimRow) -> String {
    c.claim_key
        .clone()
        .unwrap_or_else(|| c.claim_id.clone())
}

fn layout_claims(claims: &mut [ClaimRow]) {
    let mut nodes = BTreeMap::new();
    let mut claim_sources: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for c in claims
        .iter()
        .filter(|c| matches!(c.status, ClaimStatus::Durable | ClaimStatus::Caveated))
    {
        let key = claim_graph_key(c);
        nodes.insert(key.clone(), c.sources.len() as f64);
        if !c.sources.is_empty() {
            claim_sources
                .entry(key)
                .or_default()
                .extend(c.sources.iter().cloned());
        }
    }
    let edges = co_citation_edges(&claim_sources);
    let layout = graph_layout::compute_layout_default(&nodes, &edges);
    for node in layout.nodes {
        if let Some(c) = claims.iter_mut().find(|c| claim_graph_key(c) == node.id) {
            c.xy = Some(node.xy);
        }
    }
}

fn layout_sources(claims: &[ClaimRow], sources: &mut [SourceRow], packs: &[PackRow]) {
    let case_sha: BTreeMap<String, String> = packs
        .iter()
        .filter_map(|p| {
            let sha = p.source_sha256.as_deref()?;
            let case = p
                .pack_dir
                .rsplit('/')
                .next()
                .unwrap_or(p.pack_dir.as_str());
            Some((case.to_string(), sha.to_string()))
        })
        .collect();

    let mut sha_claims: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for c in claims
        .iter()
        .filter(|c| matches!(c.status, ClaimStatus::Durable | ClaimStatus::Caveated))
    {
        let key = claim_graph_key(c);
        for case in &c.sources {
            if let Some(sha) = case_sha.get(case) {
                sha_claims
                    .entry(sha.clone())
                    .or_default()
                    .insert(key.clone());
            }
        }
    }

    let mut nodes = BTreeMap::new();
    for s in &*sources {
        let w = sha_claims
            .get(&s.sha256)
            .map(BTreeSet::len)
            .unwrap_or(0) as f64;
        if w > 0.0 {
            nodes.insert(s.sha256.clone(), w);
        }
    }
    let edges = co_citation_edges(&sha_claims);
    let layout = graph_layout::compute_layout_default(&nodes, &edges);
    for node in layout.nodes {
        if let Some(s) = sources.iter_mut().find(|s| s.sha256 == node.id) {
            s.xy = Some(node.xy);
        }
    }
}

fn co_citation_edges(ids: &BTreeMap<String, BTreeSet<String>>) -> Vec<(String, String, f64)> {
    let keys: Vec<&String> = ids.keys().collect();
    let mut edges = Vec::new();
    for i in 0..keys.len() {
        for j in (i + 1)..keys.len() {
            let a = keys[i];
            let b = keys[j];
            let shared = ids[a].intersection(&ids[b]).count();
            if shared > 0 {
                edges.push((a.clone(), b.clone(), shared as f64));
            }
        }
    }
    edges
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{ClaimStatus, PackRow};

    #[test]
    fn claims_get_xy_when_linked() {
        let mut claims = vec![
            ClaimRow {
                claim_id: "a".into(),
                claim_key: Some("ka".into()),
                claim: "A".into(),
                theme: None,
                theme_id: None,
                status: ClaimStatus::Durable,
                sources: vec!["case1".into()],
                strength: None,
                run_id: None,
                run_date: None,
                lane: None,
                patched_by: None,
                origin_source: None,
                adoption_timeline: Vec::new(),
                adoption_count: None,
                credit_rate: None,
                xy: None,
            },
            ClaimRow {
                claim_id: "b".into(),
                claim_key: Some("kb".into()),
                claim: "B".into(),
                theme: None,
                theme_id: None,
                status: ClaimStatus::Durable,
                sources: vec!["case1".into()],
                strength: None,
                run_id: None,
                run_date: None,
                lane: None,
                patched_by: None,
                origin_source: None,
                adoption_timeline: Vec::new(),
                adoption_count: None,
                credit_rate: None,
                xy: None,
            },
        ];
        attach_graph_layout(&mut claims, &mut [], &[]);
        assert!(claims[0].xy.is_some());
        assert!(claims[1].xy.is_some());
    }

    #[test]
    fn sources_get_xy_from_shared_claims() {
        let mut sources = vec![
            SourceRow::blank("sha-a", crate::model::SourceStatus::Processed),
            SourceRow::blank("sha-b", crate::model::SourceStatus::Processed),
        ];
        let packs = vec![
            PackRow {
                pack_dir: "40-Resources/Reader/case-a".into(),
                title: "A".into(),
                date: None,
                units: 1,
                cards: 1,
                json_repaired: false,
                card_titles: vec![],
                source_sha256: Some("sha-a".into()),
            },
            PackRow {
                pack_dir: "40-Resources/Reader/case-b".into(),
                title: "B".into(),
                date: None,
                units: 1,
                cards: 1,
                json_repaired: false,
                card_titles: vec![],
                source_sha256: Some("sha-b".into()),
            },
        ];
        let mut claims = vec![ClaimRow {
            claim_id: "c".into(),
            claim_key: Some("kc".into()),
            claim: "C".into(),
            theme: None,
            theme_id: None,
            status: ClaimStatus::Durable,
            sources: vec!["case-a".into(), "case-b".into()],
            strength: None,
            run_id: None,
            run_date: None,
            lane: None,
            patched_by: None,
            origin_source: None,
            adoption_timeline: Vec::new(),
            adoption_count: None,
            credit_rate: None,
            xy: None,
        }];
        attach_graph_layout(&mut claims, &mut sources, &packs);
        assert!(sources[0].xy.is_some());
        assert!(sources[1].xy.is_some());
    }
}