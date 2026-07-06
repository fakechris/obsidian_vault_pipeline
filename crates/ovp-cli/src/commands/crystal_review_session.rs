use std::path::PathBuf;

use ovp_domain::crystal::{ReviewAction, ReviewDecision, ReviewEntry};

use crate::commands::crystal_write::read_review_queue;
use crate::CliError;

pub struct CrystalReviewSessionPrepareArgs {
    pub vault_root: PathBuf,
    pub batch: usize,
    pub out: PathBuf,
}

pub fn run_prepare(args: CrystalReviewSessionPrepareArgs) -> Result<(), CliError> {
    let review_path = args.vault_root.join(".ovp/crystal/review.json");
    let mut review = read_review_queue(&review_path)?;
    review.sort_by(|a, b| {
        (a.theme.as_str(), a.claim_id.as_str()).cmp(&(b.theme.as_str(), b.claim_id.as_str()))
    });
    review.truncate(args.batch);

    std::fs::create_dir_all(&args.out)
        .map_err(|e| CliError::Io(format!("creating {}: {e}", args.out.display())))?;
    std::fs::write(
        args.out.join("selected-claim-ids.txt"),
        selected_ids(&review),
    )
    .map_err(|e| CliError::Io(format!("writing selected ids: {e}")))?;
    std::fs::write(
        args.out.join("review-sheet.md"),
        render_review_sheet(&review),
    )
    .map_err(|e| CliError::Io(format!("writing review sheet: {e}")))?;
    std::fs::write(
        args.out.join("decisions.template.json"),
        render_decisions_template(&review)?,
    )
    .map_err(|e| CliError::Io(format!("writing decisions template: {e}")))?;

    println!(
        "crystal-review session prepare: {} claim(s) -> {}",
        review.len(),
        args.out.display()
    );
    println!("  review-sheet.md");
    println!("  decisions.template.json");
    println!("  selected-claim-ids.txt");
    Ok(())
}

fn selected_ids(review: &[ReviewEntry]) -> String {
    let mut out = String::new();
    for entry in review {
        out.push_str(&entry.claim_id);
        out.push('\n');
    }
    out
}

fn render_review_sheet(review: &[ReviewEntry]) -> String {
    let mut out = String::from("# Crystal Review Session\n\n");
    if review.is_empty() {
        out.push_str("_No review entries selected._\n");
        return out;
    }
    for (idx, entry) in review.iter().enumerate() {
        out.push_str(&format!(
            "## {}. `{}` [{}] - {}\n\n{}\n\n_strength: {:?} | evidence_sufficient: {}_\n\n{}\n\n",
            idx + 1,
            entry.claim_id,
            entry.theme,
            final_class_label(entry),
            entry.claim.trim(),
            entry.strength,
            entry.evidence_sufficient,
            entry.rationale.trim()
        ));
    }
    out
}

fn final_class_label(entry: &ReviewEntry) -> String {
    format!("{:?}", entry.final_class)
}

fn render_decisions_template(review: &[ReviewEntry]) -> Result<String, CliError> {
    let decisions: Vec<ReviewDecision> = review
        .iter()
        .map(|entry| ReviewDecision {
            claim_id: entry.claim_id.clone(),
            action: ReviewAction::KeepCaveated,
            revisions: Vec::new(),
            note: "TODO: rewrite | split | keep_caveated | reject".into(),
        })
        .collect();
    serde_json::to_string_pretty(&decisions)
        .map(|body| format!("{body}\n"))
        .map_err(|e| CliError::Io(format!("serializing decisions template: {e}")))
}

#[cfg(test)]
mod tests {
    use std::fs;

    use serde_json::json;

    use crate::commands::crystal_review_session::{run_prepare, CrystalReviewSessionPrepareArgs};

    #[test]
    fn crystal_review_session_prepare_writes_deterministic_batch_files() {
        let tmp = tempfile::tempdir().unwrap();
        let vault = tmp.path().join("vault");
        let store = vault.join(".ovp/crystal");
        fs::create_dir_all(&store).unwrap();
        fs::write(
            store.join("review.json"),
            serde_json::to_string_pretty(&json!({
                "review": [
                    {
                        "claim_id": "z",
                        "claim": "z claim",
                        "theme": "zeta",
                        "final_class": "caveated",
                        "strength": "supported",
                        "evidence_sufficient": true,
                        "rationale": "z rationale"
                    },
                    {
                        "claim_id": "a",
                        "claim": "a claim",
                        "theme": "alpha",
                        "final_class": "caveated",
                        "strength": "supported",
                        "evidence_sufficient": true,
                        "rationale": "a rationale"
                    }
                ]
            }))
            .unwrap(),
        )
        .unwrap();
        let out = tmp.path().join("session");

        run_prepare(CrystalReviewSessionPrepareArgs {
            vault_root: vault,
            batch: 1,
            out: out.clone(),
        })
        .unwrap();

        assert_eq!(
            fs::read_to_string(out.join("selected-claim-ids.txt")).unwrap(),
            "a\n"
        );
        let sheet = fs::read_to_string(out.join("review-sheet.md")).unwrap();
        assert!(sheet.contains("a claim"), "{sheet}");
        assert!(!sheet.contains("z claim"), "{sheet}");
        let template = fs::read_to_string(out.join("decisions.template.json")).unwrap();
        assert!(template.contains(r#""claim_id": "a""#), "{template}");
        assert!(
            template.contains(r#""action": "keep_caveated""#),
            "{template}"
        );
    }
}
