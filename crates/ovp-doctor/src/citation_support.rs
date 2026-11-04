//! Doctor check for the citation-support shadow (INV-1422).
use std::path::Path;

use crate::{Finding, Severity};

/// The citation-support shadow (INV-1422): how many citations on active
/// durable claims were confidently judged not to support their claim. Shadow
/// answers never change a grade, so this is information, never a failure.
pub(crate) fn check(vault_root: &Path, findings: &mut Vec<Finding>) {
    let check = "citation-support".to_string();
    let store = vault_root.join(".ovp/crystal");
    let Ok(raw) = std::fs::read(store.join("citation-support.jsonl")) else {
        return; // the shadow has never run
    };
    let judged = match ovp_domain::citation_support::latest_judged(&raw) {
        Ok(j) => j,
        Err(e) => {
            findings.push(
                Finding {
                    check,
                    severity: Severity::Warn,
                    message: format!(".ovp/crystal/citation-support.jsonl unreadable: {e}"),
                    hint: None,
                    fixed: false,
                }
                .attach_hint("the portal shows no answers while it is unreadable; remove or repair the bad line"),
            );
            return;
        }
    };
    // The ledger's own health is crystal-integrity's to report.
    let Ok(ledger) = std::fs::read(store.join("ledger.jsonl")) else {
        return;
    };
    let strict = ovp_domain::jsonl::TornLines::<fn(usize)>::Fail;
    let Ok(events) = ovp_domain::jsonl::parse_ledger(&ledger, strict) else {
        return;
    };
    let active: Vec<_> = ovp_domain::crystal::fold_ledger(&events)
        .into_iter()
        .filter(|r| {
            r.status == ovp_domain::crystal::CrystalStatus::Active
                && r.final_class == ovp_domain::crystal::FinalClass::Durable
        })
        .collect();
    let (mut answered, mut flagged_claims, mut flagged) = (0, 0, 0);
    for r in &active {
        let answers: Vec<_> = (0..r.citations.len())
            .filter_map(|i| judged.get(&(r.claim_key.clone(), i)))
            .collect();
        answered += usize::from(!answers.is_empty());
        let n = answers.iter().filter(|j| j.flagged).count();
        flagged += n;
        flagged_claims += usize::from(n > 0);
    }
    let coverage = format!(
        "{answered} of {} active durable claims have answers",
        active.len()
    );
    let finding = if flagged == 0 {
        Finding {
            check,
            severity: Severity::Pass,
            message: format!("no citation confidently judged unsupporting ({coverage})"),
            hint: None,
            fixed: false,
        }
    } else {
        Finding {
            check,
            severity: Severity::Info,
            message: format!(
                "{flagged} citation(s) on {flagged_claims} active durable claim(s) confidently \
                 judged not to support their claim ({coverage}); shadow only, grades unchanged"
            ),
            hint: None,
            fixed: false,
        }
        .attach_hint("each one is marked on its claim page in the portal")
    };
    findings.push(finding);
}

#[cfg(test)]
mod tests {
    use super::*;
    use ovp_domain::crystal::{
        CrystalStatus, DurableCitation, DurableRecord, FinalClass, ProvenanceClass, StoreEvent,
        StoreOp, StrengthClass,
    };

    fn event(op: StoreOp, key: &str, citations: usize) -> String {
        let record = DurableRecord {
            claim_key: key.into(),
            claim_id: key.into(),
            claim: "c".into(),
            theme: "t".into(),
            theme_id: None,
            source_cases: vec!["a".into()],
            citations: (0..citations)
                .map(|i| DurableCitation {
                    case_id: "a".into(),
                    unit_id: format!("u{i}"),
                    quote: "q".into(),
                    resolved_line: None,
                })
                .collect(),
            provenance_score: 1.0,
            provenance_class: ProvenanceClass::Durable,
            strength: StrengthClass::Supported,
            strength_rationale: "r".into(),
            final_class: FinalClass::Durable,
            run_id: "r".into(),
            status: CrystalStatus::Active,
        };
        serde_json::to_string(&StoreEvent {
            op,
            record,
            supersedes: None,
            reason: None,
        })
        .unwrap()
    }

    fn row(key: &str, i: usize, support: &str, confidence: f64) -> String {
        serde_json::json!({"claim_key":key,"citation":i,"status":"judged","support":support,"confidence":confidence})
            .to_string()
    }

    fn run(vault: &Path) -> Vec<Finding> {
        let mut findings = Vec::new();
        check(vault, &mut findings);
        findings
    }

    #[test]
    fn counts_confident_non_support_on_active_durable_claims() {
        let tmp = tempfile::tempdir().unwrap();
        let store = tmp.path().join(".ovp/crystal");
        std::fs::create_dir_all(&store).unwrap();
        std::fs::write(
            store.join("ledger.jsonl"),
            [
                event(StoreOp::Write, "ck-1", 2),
                event(StoreOp::Write, "ck-2", 1),
                event(StoreOp::Write, "ck-gone", 1),
                event(StoreOp::Retract, "ck-gone", 1),
            ]
            .join("\n")
                + "\n",
        )
        .unwrap();
        assert!(
            run(tmp.path()).is_empty(),
            "no answers file: the shadow never ran"
        );

        let answers = |rows: &[String]| {
            std::fs::write(store.join("citation-support.jsonl"), rows.join("\n") + "\n").unwrap()
        };
        answers(&[row("ck-1", 0, "supports", 0.99)]);
        let f = run(tmp.path());
        assert_eq!(f[0].severity, Severity::Pass);
        assert!(
            f[0].message.contains("1 of 2 active durable claims"),
            "{}",
            f[0].message
        );

        answers(&[
            row("ck-1", 0, "supports", 0.99),
            row("ck-1", 1, "says_nothing", 0.9),
            row("ck-2", 0, "contradicts", 0.5),
            row("ck-gone", 0, "says_nothing", 0.99),
        ]);
        let f = run(tmp.path());
        assert_eq!(
            f[0].severity,
            Severity::Info,
            "shadow data never fails doctor"
        );
        assert!(
            f[0].message
                .starts_with("1 citation(s) on 1 active durable claim(s)"),
            "{}",
            f[0].message
        );
        assert!(
            f[0].message.contains("2 of 2 active durable claims"),
            "{}",
            f[0].message
        );

        std::fs::write(store.join("citation-support.jsonl"), "garbage\n").unwrap();
        assert_eq!(run(tmp.path())[0].severity, Severity::Warn);
    }
}
