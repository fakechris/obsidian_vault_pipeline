//! `mcp-guidance` — write the "when to consult the OVP vault" section into
//! the vault's agent instruction files (CLAUDE.md + AGENTS.md), so agents
//! actually reach for the MCP tools instead of grepping markdown (the
//! openwiki pattern: the wiki is only consulted if the instruction files say
//! so).
//!
//! Idempotent: the section lives between marker comments and is replaced in
//! place on every run; content OUTSIDE the markers is never touched. A
//! missing file is created with just the section.

use std::path::{Path, PathBuf};

use crate::CliError;

pub struct McpGuidanceArgs {
    pub vault_root: PathBuf,
}

const START: &str = "<!-- ovp-mcp:guidance:start -->";
const END: &str = "<!-- ovp-mcp:guidance:end -->";

/// The section body (between the markers). Kept as one literal so the whole
/// contract is reviewable in one place.
fn guidance_body() -> String {
    "## OVP knowledge vault (MCP)\n\
     \n\
     This vault is indexed by OVP. Consult it BEFORE answering from memory\n\
     when a question touches captured sources, synthesized knowledge, or\n\
     what the operator has read. Launch: `ovp2 mcp --vault-root <this vault>`.\n\
     \n\
     - `search` / `find` — locate sources, reader packs, and durable claims\n\
       (filter by kind/status/date/tag/entity).\n\
     - `theme_page` — grounded topic overviews woven from durable claims;\n\
       every sentence carries a `[claim:<key>]` citation. Call with no\n\
       arguments to list topics.\n\
     - `claim` — audit any `[claim:<key>]` citation: full evidence closure\n\
       (claim → verbatim quote → line → source).\n\
     - `status` / `doctor` — pipeline freshness and health.\n\
     \n\
     Stable references — safe to store in notes and answers:\n\
     - `ovp://claim/<claim_key>` (claim_keys are deterministic and survive\n\
       re-runs)\n\
     - `ovp://source/<sha256>`\n\
     \n\
     Prefer citing claims over paraphrasing sources: a cited claim already\n\
     passed the evidence gate; a paraphrase did not.\n"
        .to_string()
}

/// Replace the marked section in `content`, or append it. Returns the new
/// file content, or an error when the markers are unmatched/mis-ordered —
/// appending a second block there would make the NEXT run pair the stray
/// marker with the new one and delete the operator's content in between
/// (codex review P2). Fail loud; the operator repairs the markers by hand.
fn splice(content: &str, body: &str) -> Result<String, String> {
    let section = format!("{START}\n{body}{END}\n");
    let starts = content.matches(START).count();
    let ends = content.matches(END).count();
    match (starts, ends) {
        (0, 0) => Ok(if content.trim().is_empty() {
            section
        } else {
            // Keep exactly one blank line before the appended section.
            format!("{}\n\n{section}", content.trim_end())
        }),
        (1, 1) => {
            let s = content.find(START).expect("counted above");
            let e = content.find(END).expect("counted above");
            if e < s {
                return Err(format!(
                    "guidance markers are out of order ({END} before {START}) — fix the file manually"
                ));
            }
            let after = &content[e + END.len()..];
            let after = after.strip_prefix('\n').unwrap_or(after);
            Ok(format!("{}{section}{after}", &content[..s]))
        }
        _ => Err(format!(
            "unmatched guidance markers ({starts}× start, {ends}× end) — fix the file manually"
        )),
    }
}

fn update_file(path: &Path, body: &str) -> Result<bool, CliError> {
    let old = match std::fs::read_to_string(path) {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(e) => return Err(CliError::Io(format!("reading {}: {e}", path.display()))),
    };
    let new = splice(&old, body)
        .map_err(|e| CliError::Io(format!("mcp-guidance: {}: {e}", path.display())))?;
    if new == old {
        return Ok(false);
    }
    std::fs::write(path, &new)
        .map_err(|e| CliError::Io(format!("writing {}: {e}", path.display())))?;
    Ok(true)
}

pub fn run(args: McpGuidanceArgs) -> Result<(), CliError> {
    let body = guidance_body();
    for name in ["CLAUDE.md", "AGENTS.md"] {
        let path = args.vault_root.join(name);
        let changed = update_file(&path, &body)?;
        println!(
            "mcp-guidance: {} {}",
            path.display(),
            if changed { "updated" } else { "up to date" }
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn creates_updates_idempotently_and_preserves_surroundings() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().to_path_buf();

        // Existing CLAUDE.md with operator content survives around the section.
        std::fs::write(root.join("CLAUDE.md"), "# My vault rules\n\nkeep me\n").unwrap();
        run(McpGuidanceArgs {
            vault_root: root.clone(),
        })
        .unwrap();
        let claude = std::fs::read_to_string(root.join("CLAUDE.md")).unwrap();
        assert!(claude.starts_with("# My vault rules"), "{claude}");
        assert!(claude.contains("keep me"));
        assert!(claude.contains(START) && claude.contains(END));
        assert!(claude.contains("ovp://claim/<claim_key>"));

        // Missing AGENTS.md was created with just the section.
        let agents = std::fs::read_to_string(root.join("AGENTS.md")).unwrap();
        assert!(agents.starts_with(START), "{agents}");

        // Re-run: no change (idempotent).
        run(McpGuidanceArgs {
            vault_root: root.clone(),
        })
        .unwrap();
        assert_eq!(
            std::fs::read_to_string(root.join("CLAUDE.md")).unwrap(),
            claude
        );

        // Content added AFTER the section survives an update; a stale body
        // between the markers is replaced.
        let edited = claude.replace("Prefer citing claims", "STALE TEXT") + "\n## Appended later\n";
        std::fs::write(root.join("CLAUDE.md"), &edited).unwrap();
        run(McpGuidanceArgs {
            vault_root: root.clone(),
        })
        .unwrap();
        let fresh = std::fs::read_to_string(root.join("CLAUDE.md")).unwrap();
        assert!(!fresh.contains("STALE TEXT"));
        assert!(fresh.contains("## Appended later"));
    }

    #[test]
    fn unmatched_or_misordered_markers_fail_instead_of_eating_content() {
        // A lone START must NOT trigger the append path: the next run would
        // pair the stray START with the appended END and delete everything
        // between them.
        let lone_start = format!("# rules\n\n{START}\nuser content here\n");
        let err = splice(&lone_start, "body\n").unwrap_err();
        assert!(err.contains("unmatched"), "{err}");

        let misordered = format!("{END}\nmiddle\n{START}\n");
        let err = splice(&misordered, "body\n").unwrap_err();
        assert!(err.contains("out of order"), "{err}");

        let doubled = format!("{START}\na\n{END}\n{START}\nb\n{END}\n");
        let err = splice(&doubled, "body\n").unwrap_err();
        assert!(err.contains("unmatched") || err.contains("2"), "{err}");
    }
}
