//! `crystal-theme-pages` — weave each theme's ACTIVE durable claims into a
//! grounded topic page (`.ovp/crystal/theme_pages.json`).
//!
//! Pipeline:
//!
//!   crystal ledger (fold → active durable records)
//!     → group by majority theme community (the same vote `ovp-index` uses
//!       for `ClaimRow.theme`, keyed by community id — labels never vote)
//!     → per community ≥ --min-claims: `theme_page/v1` synthesis over the
//!       sorted claim set (request sees keywords + claims, NEVER labels)
//!     → deterministic page gate: every sentence cites ≥1 known
//!       `[claim:<claim_key>]`, unknown keys fail loud; a failing draft
//!       gets one bounded repair call before the theme counts as failed
//!     → `.ovp/crystal/theme_pages.json` (rebuildable projection).
//!
//! Staleness: a page whose theme's active claim set is unchanged is kept
//! verbatim (no LLM call); `--refresh` forces regeneration. Degradation
//! contract: no `themes.json` or an empty ledger prints why and exits 0 —
//! daily runs are never blocked.

use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;

use ovp_domain::crystal::theme_pages::{
    PageClaim, THEME_PAGES_SCHEMA, ThemePage, ThemePagesFile, parse_theme_page, resolve_handles,
    theme_page_repair_request, theme_page_request, uncited_keys, verify_page,
};
use ovp_domain::crystal::themes::ThemesFile;
use ovp_domain::crystal::{CrystalStatus, DurableRecord, StoreEvent, fold_ledger};
use ovp_domain::vault_layout::VaultLayout;
use ovp_intake::read_jsonl;
use ovp_llm::ModelClient;

use crate::CliError;
use crate::commands::client::{ClientKind, build_client};
use crate::commands::crystal_synth::call_and_parse;

pub struct CrystalThemePagesArgs {
    pub vault_root: PathBuf,
    pub client_kind: ClientKind,
    /// Cassette root for `theme_page/v1`. Default:
    /// `<vault-root>/.ovp/cassettes/crystal` (shared with crystal-synth).
    pub cache_dir: Option<PathBuf>,
    /// Regenerate every page even when its claim set is unchanged.
    pub refresh: bool,
    /// Themes with fewer active durable claims get no page.
    pub min_claims: usize,
}

/// What one build pass did, for the summary and for tests.
#[derive(Debug)]
pub(crate) struct PageBuildOutcome {
    pub(crate) pages: Vec<ThemePage>,
    pub(crate) built: usize,
    pub(crate) kept: usize,
    pub(crate) skipped_small: usize,
    /// Themes whose draft AND bounded repair both failed the gate, with the
    /// final defect list. Their old pages are NOT in `pages` — the caller
    /// writes the partial projection (a missing page is honest; a stale one
    /// citing retracted claims is not) and then fails loud.
    pub(crate) failures: Vec<(i64, Vec<String>)>,
}

/// Group active durable records by majority theme community and synthesize a
/// page per qualifying community. Pure over its inputs (the client carries
/// the LLM); order is deterministic (communities in `themes.json` order,
/// claims in sorted claim_key order).
pub(crate) fn build_pages(
    records: &[DurableRecord],
    themes: &ThemesFile,
    existing: Option<&ThemePagesFile>,
    client: &mut dyn ModelClient,
    min_claims: usize,
    refresh: bool,
) -> Result<PageBuildOutcome, CliError> {
    let mut by_community: BTreeMap<i64, Vec<&DurableRecord>> = BTreeMap::new();
    for r in records {
        if let Some(id) = themes.majority_community(&r.source_cases) {
            by_community.entry(id).or_default().push(r);
        }
    }

    let mut outcome = PageBuildOutcome {
        pages: Vec::new(),
        built: 0,
        kept: 0,
        skipped_small: 0,
        failures: Vec::new(),
    };
    // Failed themes accumulate instead of aborting the loop: one run
    // invalidates EVERY ungrounded cassette entry, so the next live pass
    // re-asks them all at once (successes stay cached).
    for community in &themes.communities {
        let Some(mut group) = by_community.remove(&community.id) else {
            continue;
        };
        if group.len() < min_claims {
            outcome.skipped_small += 1;
            continue;
        }
        group.sort_by(|a, b| a.claim_key.cmp(&b.claim_key));
        let claim_keys: Vec<String> = group.iter().map(|r| r.claim_key.clone()).collect();
        let known: BTreeSet<String> = claim_keys.iter().cloned().collect();

        if !refresh
            && let Some(prev) = existing.and_then(|f| f.page(community.id))
            && prev.claim_keys == claim_keys
            && verify_page(&prev.sections, &known).is_empty()
        {
            // Unchanged claim set AND the retained page still passes the
            // gate (a hand-edited or older-generator projection must not
            // ride the fast path — codex review P2). Labels refresh; they
            // are never synthesis input.
            outcome.pages.push(ThemePage {
                label: community.label.clone(),
                label_zh: community.label_zh.clone(),
                ..prev.clone()
            });
            outcome.kept += 1;
            continue;
        }

        let claims: Vec<PageClaim> = group
            .iter()
            .map(|r| PageClaim {
                claim_key: r.claim_key.clone(),
                claim: r.claim.clone(),
                source_count: r.source_cases.len(),
            })
            .collect();
        // A call/parse failure on ONE theme must not abort the run before
        // the safe partial write — the old page may cite claims that are no
        // longer active, and exiting early would leave it serving (codex
        // round-4 P1). Same failure path as a gate failure.
        let req = theme_page_request(&community.synth_theme(), &claims);
        let draft = match call_and_parse(client, &req, "theme-page", parse_theme_page) {
            Ok((draft, _repair)) => draft,
            Err(e) => {
                outcome
                    .failures
                    .push((community.id, vec![format!("{e:?}")]));
                continue;
            }
        };
        // The model cites positional handles (c1, c2, …); the code owns the
        // handle → claim_key substitution. Unresolved handles survive into
        // the verifier and fail loud as UnknownClaim.
        let mut sections = draft.clone();
        resolve_handles(&mut sections, &claim_keys);

        let mut defects = verify_page(&sections, &known);
        if !defects.is_empty() {
            // ONE bounded repair (M36 repair-workshop shape): the model gets
            // its own draft + the defect list and must cite or delete each
            // offending sentence. Small themes reliably need this — the
            // model pads them with uncited synthesis glue.
            let listed: Vec<String> = defects.iter().map(|d| d.to_string()).collect();
            let repair_req =
                theme_page_repair_request(&community.synth_theme(), &claims, &draft, &listed);
            let repaired =
                match call_and_parse(client, &repair_req, "theme-page-repair", parse_theme_page) {
                    Ok((repaired, _log)) => repaired,
                    Err(e) => {
                        client.invalidate(&req);
                        outcome
                            .failures
                            .push((community.id, vec![format!("{e:?}")]));
                        continue;
                    }
                };
            let mut repaired_resolved = repaired;
            resolve_handles(&mut repaired_resolved, &claim_keys);
            defects = verify_page(&repaired_resolved, &known);
            if defects.is_empty() {
                sections = repaired_resolved;
            } else {
                // Forget both exchanges so a rerun re-asks instead of
                // replaying the same ungrounded page (no-op on replay).
                client.invalidate(&req);
                client.invalidate(&repair_req);
                outcome.failures.push((
                    community.id,
                    defects.iter().map(|d| d.to_string()).collect(),
                ));
                continue;
            }
        }

        outcome.pages.push(ThemePage {
            community_id: community.id,
            label: community.label.clone(),
            label_zh: community.label_zh.clone(),
            claim_keys,
            sections,
        });
        outcome.built += 1;
    }
    Ok(outcome)
}

/// Atomically publish the projection (temp + rename) — readers must never see
/// a torn file.
fn write_pages_file(
    store: &std::path::Path,
    pages_path: &std::path::Path,
    file: &ThemePagesFile,
) -> Result<(), CliError> {
    std::fs::create_dir_all(store)
        .map_err(|e| CliError::Io(format!("creating {}: {e}", store.display())))?;
    let body = serde_json::to_string_pretty(file)
        .map_err(|e| CliError::Io(format!("serializing theme_pages.json: {e}")))?;
    let tmp = pages_path.with_extension("json.tmp");
    std::fs::write(&tmp, format!("{body}\n"))
        .map_err(|e| CliError::Io(format!("writing {}: {e}", tmp.display())))?;
    std::fs::rename(&tmp, pages_path)
        .map_err(|e| CliError::Io(format!("publishing {}: {e}", pages_path.display())))?;
    Ok(())
}

pub fn run(args: CrystalThemePagesArgs) -> Result<(), CliError> {
    if args.min_claims == 0 {
        return Err(CliError::Io(
            "crystal-theme-pages: --min-claims must be ≥ 1".to_string(),
        ));
    }
    let layout = VaultLayout::new();
    let store = args.vault_root.join(layout.crystal_store_dir());
    let pages_path = store.join("theme_pages.json");

    let themes = match ThemesFile::load(&store.join("themes.json")) {
        Ok(Some(themes)) => themes,
        Ok(None) => {
            println!(
                "crystal-theme-pages: no themes.json under {} — run `ovp2 crystal-themes` first.",
                store.display()
            );
            return Ok(());
        }
        Err(e) => return Err(CliError::Io(format!("crystal-theme-pages: {e}"))),
    };

    let ledger = store.join("ledger.jsonl");
    let events: Vec<StoreEvent> = read_jsonl(&ledger).map_err(|e| {
        CliError::Io(format!(
            "crystal-theme-pages: ledger {}: {e}",
            ledger.display()
        ))
    })?;
    let records: Vec<DurableRecord> = fold_ledger(&events)
        .into_iter()
        .filter(|r| r.status == CrystalStatus::Active)
        .collect();
    if records.is_empty() {
        // Publish an EMPTY projection rather than leaving a stale one behind:
        // if every claim was later retracted/superseded, readers must not keep
        // serving pages whose citations are no longer active (codex review P2).
        write_pages_file(
            &store,
            &pages_path,
            &ThemePagesFile {
                schema: THEME_PAGES_SCHEMA.to_string(),
                pages: Vec::new(),
            },
        )?;
        println!(
            "crystal-theme-pages: no active durable claims — wrote empty {}.",
            pages_path.display()
        );
        return Ok(());
    }

    // A corrupt existing projection is about to be rebuilt anyway — warn and
    // regenerate rather than refusing (`ovp2 index` is where corruption of
    // read inputs fails loud).
    let existing = match ThemePagesFile::load(&pages_path) {
        Ok(existing) => existing,
        Err(e) => {
            eprintln!("warning: rebuilding corrupt theme_pages.json ({e})");
            None
        }
    };

    let cassette_dir = args
        .cache_dir
        .clone()
        .unwrap_or_else(|| args.vault_root.join(".ovp/cassettes/crystal"));
    let mut client = build_client(args.client_kind, &cassette_dir)?;

    let outcome = build_pages(
        &records,
        &themes,
        existing.as_ref(),
        client.as_mut(),
        args.min_claims,
        args.refresh,
    )?;

    // Write the projection BEFORE reporting gate failures: the passing pages
    // land, and a failed theme's OLD page (which may cite claims that are no
    // longer active) is dropped rather than left to masquerade as current
    // (codex round-3 P2). A missing page is honest; a stale one is not.
    let file = ThemePagesFile {
        schema: THEME_PAGES_SCHEMA.to_string(),
        pages: outcome.pages,
    };
    write_pages_file(&store, &pages_path, &file)?;
    if !outcome.failures.is_empty() {
        let mut msg = format!(
            "crystal-theme-pages: {} theme(s) failed the page gate \
             (partial projection written without them; rerun re-asks them):",
            outcome.failures.len()
        );
        for (id, defects) in &outcome.failures {
            msg.push_str(&format!("\n  t{:03} ({} defect(s)):", id, defects.len()));
            for d in defects {
                msg.push_str(&format!("\n    {d}"));
            }
        }
        return Err(CliError::Io(msg));
    }

    println!(
        "crystal-theme-pages: {} page(s) ({} built, {} kept, {} theme(s) below --min-claims {}) → {}",
        file.pages.len(),
        outcome.built,
        outcome.kept,
        outcome.skipped_small,
        args.min_claims,
        pages_path.display()
    );
    for page in &file.pages {
        let known: BTreeSet<String> = page.claim_keys.iter().cloned().collect();
        let uncited = uncited_keys(&page.sections, &known);
        println!(
            "  t{:03}  {}  {} claim(s), {} section(s){}",
            page.community_id,
            page.label,
            page.claim_keys.len(),
            page.sections.len(),
            if uncited.is_empty() {
                String::new()
            } else {
                format!(", {} uncited", uncited.len())
            }
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_domain::crystal::themes::{THEMES_SCHEMA, ThemeCommunity, ThemeParams};
    use ovp_domain::crystal::{DurableCitation, FinalClass, ProvenanceClass, StrengthClass};
    use ovp_llm::{FixtureModelClient, ModelReply, StopReason, Usage};

    fn themes_fixture() -> ThemesFile {
        ThemesFile {
            schema: THEMES_SCHEMA.into(),
            model: "test-model".into(),
            params: ThemeParams {
                k: 10,
                cosine_threshold: 0.5,
                resolution: 1.5,
                seed: 42,
                text_prefix: "passage: ".into(),
                head_chars: 1500,
            },
            generated_from: "abc".into(),
            packs: BTreeMap::from([
                ("case-a".to_string(), 0),
                ("case-b".to_string(), 0),
                ("case-c".to_string(), 1),
            ]),
            communities: vec![
                ThemeCommunity {
                    id: 0,
                    label: "Agent memory".into(),
                    label_zh: "智能体记忆".into(),
                    keywords: vec!["memory".into()],
                    size: 2,
                },
                ThemeCommunity {
                    id: 1,
                    label: "Quant markets".into(),
                    label_zh: "量化市场".into(),
                    keywords: vec!["quant".into()],
                    size: 1,
                },
            ],
            labels_provenance: Default::default(),
        }
    }

    fn record(key: &str, claim: &str, cases: &[&str]) -> DurableRecord {
        DurableRecord {
            claim_key: key.into(),
            claim_id: format!("id-{key}"),
            claim: claim.into(),
            theme: "t".into(),
            source_cases: cases.iter().map(|s| s.to_string()).collect(),
            citations: cases
                .iter()
                .map(|c| DurableCitation {
                    case_id: c.to_string(),
                    unit_id: "u-1".into(),
                    quote: "q".into(),
                    resolved_line: None,
                })
                .collect(),
            provenance_score: 0.8,
            provenance_class: ProvenanceClass::Durable,
            strength: StrengthClass::Supported,
            strength_rationale: "r".into(),
            final_class: FinalClass::Durable,
            run_id: "run-x".into(),
            status: CrystalStatus::Active,
        }
    }

    fn reply(text: &str) -> ModelReply {
        ModelReply {
            model: "test".into(),
            text: text.to_string(),
            stop_reason: StopReason::EndTurn,
            usage: Usage {
                input_tokens: 0,
                output_tokens: 0,
            },
        }
    }

    /// The community-0 records used across tests (both cite theme-0 cases).
    fn records_fixture() -> Vec<DurableRecord> {
        vec![
            record("ck-b", "Memory persists.", &["case-a", "case-b"]),
            record("ck-a", "Context compounds.", &["case-a", "case-b"]),
        ]
    }

    /// The request build_pages will issue for `records_fixture` (sorted by
    /// claim_key: ck-a first).
    fn fixture_request() -> ovp_llm::ModelRequest {
        theme_page_request(
            &themes_fixture().communities[0].synth_theme(),
            &[
                PageClaim {
                    claim_key: "ck-a".into(),
                    claim: "Context compounds.".into(),
                    source_count: 2,
                },
                PageClaim {
                    claim_key: "ck-b".into(),
                    claim: "Memory persists.".into(),
                    source_count: 2,
                },
            ],
        )
    }

    #[test]
    fn builds_a_page_per_qualifying_community_and_skips_small_ones() {
        let themes = themes_fixture();
        // Community 1 has a single claim → below min_claims 2 → skipped.
        let mut records = records_fixture();
        records.push(record("ck-c", "Quant claim.", &["case-c"]));

        let mut client = FixtureModelClient::new();
        client.insert(
            &fixture_request(),
            reply(
                r#"{"sections":[{"heading":"Memory","body":"Persists [claim:c2], compounds [claim:c1]."}]}"#,
            ),
        );

        let out = build_pages(&records, &themes, None, &mut client, 2, false).unwrap();
        assert_eq!((out.built, out.kept, out.skipped_small), (1, 0, 1));
        assert_eq!(out.pages.len(), 1);
        let page = &out.pages[0];
        assert_eq!(page.community_id, 0);
        assert_eq!(page.label, "Agent memory");
        assert_eq!(page.claim_keys, vec!["ck-a", "ck-b"], "sorted claim keys");
        assert_eq!(page.sections.len(), 1);
        assert_eq!(
            page.sections[0].body, "Persists [claim:ck-b], compounds [claim:ck-a].",
            "persisted pages carry real claim keys, not handles"
        );
    }

    #[test]
    fn unchanged_claim_set_keeps_the_page_without_an_llm_call() {
        let themes = themes_fixture();
        let records = records_fixture();
        let existing = ThemePagesFile {
            schema: THEME_PAGES_SCHEMA.into(),
            pages: vec![ThemePage {
                community_id: 0,
                label: "Old label".into(),
                label_zh: "旧名".into(),
                claim_keys: vec!["ck-a".into(), "ck-b".into()],
                sections: vec![ovp_domain::crystal::theme_pages::PageSection {
                    heading: "H".into(),
                    body: "B [claim:ck-a].".into(),
                }],
            }],
        };
        // Empty fixture client: any LLM call would CacheMiss → error.
        let mut client = FixtureModelClient::new();
        let out = build_pages(&records, &themes, Some(&existing), &mut client, 2, false).unwrap();
        assert_eq!((out.built, out.kept), (0, 1));
        assert_eq!(out.pages[0].label, "Agent memory", "labels refresh on keep");
        assert_eq!(out.pages[0].sections, existing.pages[0].sections);
    }

    #[test]
    fn refresh_regenerates_even_when_unchanged() {
        let themes = themes_fixture();
        let records = records_fixture();
        let existing = ThemePagesFile {
            schema: THEME_PAGES_SCHEMA.into(),
            pages: vec![ThemePage {
                community_id: 0,
                label: "Agent memory".into(),
                label_zh: "智能体记忆".into(),
                claim_keys: vec!["ck-a".into(), "ck-b".into()],
                sections: vec![ovp_domain::crystal::theme_pages::PageSection {
                    heading: "Old".into(),
                    body: "Old [claim:ck-a].".into(),
                }],
            }],
        };
        let mut client = FixtureModelClient::new();
        client.insert(
            &fixture_request(),
            reply(r#"{"sections":[{"heading":"New","body":"New [claim:c2]."}]}"#),
        );
        let out = build_pages(&records, &themes, Some(&existing), &mut client, 2, true).unwrap();
        assert_eq!((out.built, out.kept), (1, 0));
        assert_eq!(out.pages[0].sections[0].heading, "New");
    }

    /// The repair request the command will issue for `bad_reply` below.
    fn fixture_repair_request(bad_reply_json: &str) -> ovp_llm::ModelRequest {
        let draft = parse_theme_page(bad_reply_json).unwrap();
        let mut resolved = draft.clone();
        resolve_handles(&mut resolved, &["ck-a".into(), "ck-b".into()]);
        let known: BTreeSet<String> = ["ck-a".to_string(), "ck-b".to_string()].into();
        let defects: Vec<String> = verify_page(&resolved, &known)
            .iter()
            .map(|d| d.to_string())
            .collect();
        let claims = [
            PageClaim {
                claim_key: "ck-a".into(),
                claim: "Context compounds.".into(),
                source_count: 2,
            },
            PageClaim {
                claim_key: "ck-b".into(),
                claim: "Memory persists.".into(),
                source_count: 2,
            },
        ];
        theme_page_repair_request(
            &themes_fixture().communities[0].synth_theme(),
            &claims,
            &draft,
            &defects,
        )
    }

    const BAD_REPLY: &str =
        r#"{"sections":[{"heading":"H","body":"No citation here.\n\nGhost handle [claim:c9]."}]}"#;

    #[test]
    fn failed_repair_lands_in_failures_and_drops_the_page() {
        let themes = themes_fixture();
        let records = records_fixture();
        let mut client = FixtureModelClient::new();
        client.insert(&fixture_request(), reply(BAD_REPLY));
        // The repair attempt returns the SAME bad draft — still ungrounded.
        client.insert(&fixture_repair_request(BAD_REPLY), reply(BAD_REPLY));
        let out = build_pages(&records, &themes, None, &mut client, 2, false).unwrap();
        assert!(out.pages.is_empty(), "failed theme must not produce a page");
        assert_eq!(out.failures.len(), 1);
        let (id, defects) = &out.failures[0];
        assert_eq!(*id, 0);
        let joined = defects.join("\n");
        assert!(
            joined.contains("uncited sentence `No citation here.`"),
            "{joined}"
        );
        assert!(joined.contains("unknown claim `c9`"), "{joined}");
    }

    #[test]
    fn one_bounded_repair_can_save_a_failing_page() {
        let themes = themes_fixture();
        let records = records_fixture();
        let mut client = FixtureModelClient::new();
        client.insert(&fixture_request(), reply(BAD_REPLY));
        client.insert(
            &fixture_repair_request(BAD_REPLY),
            reply(r#"{"sections":[{"heading":"H","body":"Now cited [claim:c1]."}]}"#),
        );
        let out = build_pages(&records, &themes, None, &mut client, 2, false).unwrap();
        assert_eq!((out.built, out.kept), (1, 0));
        assert_eq!(out.pages[0].sections[0].body, "Now cited [claim:ck-a].");
    }

    #[test]
    fn keep_path_revalidates_and_regenerates_an_invalid_retained_page() {
        let themes = themes_fixture();
        let records = records_fixture();
        // Same claim set, but the retained page violates the sentence gate
        // (hand edit / older generator) — it must NOT ride the fast path.
        let existing = ThemePagesFile {
            schema: THEME_PAGES_SCHEMA.into(),
            pages: vec![ThemePage {
                community_id: 0,
                label: "Agent memory".into(),
                label_zh: "智能体记忆".into(),
                claim_keys: vec!["ck-a".into(), "ck-b".into()],
                sections: vec![ovp_domain::crystal::theme_pages::PageSection {
                    heading: "H".into(),
                    body: "Uncited sentence. Cited one [claim:ck-a].".into(),
                }],
            }],
        };
        let mut client = FixtureModelClient::new();
        client.insert(
            &fixture_request(),
            reply(r#"{"sections":[{"heading":"Regen","body":"Fresh [claim:c1]."}]}"#),
        );
        let out = build_pages(&records, &themes, Some(&existing), &mut client, 2, false).unwrap();
        assert_eq!(
            (out.built, out.kept),
            (1, 0),
            "invalid page regenerated, not kept"
        );
        assert_eq!(out.pages[0].sections[0].heading, "Regen");
    }

    #[test]
    fn claims_without_a_majority_community_are_not_paged() {
        let themes = themes_fixture();
        // Cites only unknown cases → majority_community None → no group.
        let records = vec![
            record("ck-x", "Orphan claim.", &["unknown-1", "unknown-2"]),
            record("ck-y", "Another orphan.", &["unknown-3", "unknown-4"]),
        ];
        let mut client = FixtureModelClient::new();
        let out = build_pages(&records, &themes, None, &mut client, 2, false).unwrap();
        assert_eq!(out.pages.len(), 0);
        assert_eq!((out.built, out.kept, out.skipped_small), (0, 0, 0));
    }
}
