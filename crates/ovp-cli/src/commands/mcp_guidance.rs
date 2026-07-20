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
/// file content.
fn splice(content: &str, body: &str) -> String {
    let section = format!("{START}\n{body}{END}\n");
    match (content.find(START), content.find(END)) {
        (Some(s), Some(e)) if e >= s => {
            let after = &content[e + END.len()..];
            let after = after.strip_prefix('\n').unwrap_or(after);
            format!("{}{section}{after}", &content[..s])
        }
        _ => {
            // Append (or start) — keep exactly one blank line before the
            // section when the file already has content.
            if content.trim().is_empty() {
                section
            } else {
                format!("{}\n\n{section}", content.trim_end())
            }
        }
    }
}

fn update_file(path: &Path, body: &str) -> Result<bool, CliError> {
    let old = match std::fs::read_to_string(path) {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(e) => return Err(CliError::Io(format!("reading {}: {e}", path.display()))),
    };
    let new = splice(&old, body);
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
        run(McpGuidanceArgs { vault_root: root.clone() }).unwrap();
        let claude = std::fs::read_to_string(root.join("CLAUDE.md")).unwrap();
        assert!(claude.starts_with("# My vault rules"), "{claude}");
        assert!(claude.contains("keep me"));
        assert!(claude.contains(START) && claude.contains(END));
        assert!(claude.contains("ovp://claim/<claim_key>"));

        // Missing AGENTS.md was created with just the section.
        let agents = std::fs::read_to_string(root.join("AGENTS.md")).unwrap();
        assert!(agents.starts_with(START), "{agents}");

        // Re-run: no change (idempotent).
        run(McpGuidanceArgs { vault_root: root.clone() }).unwrap();
        assert_eq!(std::fs::read_to_string(root.join("CLAUDE.md")).unwrap(), claude);

        // Content added AFTER the section survives an update; a stale body
        // between the markers is replaced.
        let edited = claude.replace("Prefer citing claims", "STALE TEXT") + "\n## Appended later\n";
        std::fs::write(root.join("CLAUDE.md"), &edited).unwrap();
        run(McpGuidanceArgs { vault_root: root.clone() }).unwrap();
        let fresh = std::fs::read_to_string(root.join("CLAUDE.md")).unwrap();
        assert!(!fresh.contains("STALE TEXT"));
        assert!(fresh.contains("## Appended later"));
    }
}
