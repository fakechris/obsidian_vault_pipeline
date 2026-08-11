//! Operator config for source-work automation (`.ovp/source-work.toml`).
//!
//! Missing file → product defaults (auto summarize + translate for new daily
//! successes). Operators can disable or cap in the TOML without code changes.

use std::fs;
use std::path::Path;

use serde::{Deserialize, Serialize};

/// Vault-relative config path.
pub const CONFIG_REL: &str = ".ovp/source-work.toml";

/// Default cap on auto-enqueues per daily run (protects LLM budget).
fn default_auto_max() -> usize {
    30
}

/// Default true — product wants daily sources to get deep summary/zh without
/// a manual click. Set `false` in TOML to opt out.
fn default_true() -> bool {
    true
}

/// Configuration for automatic + batch source-work enrichment.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SourceWorkConfig {
    /// After a daily source succeeds, enqueue deep summary when missing.
    #[serde(default = "default_true")]
    pub auto_summarize: bool,
    /// After a daily source succeeds, enqueue EN→zh when missing / primarily English.
    #[serde(default = "default_true")]
    pub auto_translate: bool,
    /// Desktop/browser notify for auto-enqueued jobs (usually quieter than manual).
    #[serde(default)]
    pub auto_notify: bool,
    /// Max sources auto-enqueued per daily run (0 = unlimited).
    #[serde(default = "default_auto_max")]
    pub auto_max_per_run: usize,
    /// Batch claim_zh when running `crystal-claims-zh` with --auto defaults.
    #[serde(default = "default_true")]
    pub auto_claim_zh: bool,
    /// Cap on claim_zh translations per crystal-synth tail run (0 =
    /// unlimited). Deliberately separate from `auto_max_per_run`: that budget
    /// limits how many SOURCES daily auto-enqueues per run, while a claims
    /// backlog must be able to drain in one run — a provider outage is
    /// already contained by the batch's consecutive-failure breaker.
    #[serde(default)]
    pub auto_claim_zh_max_per_run: usize,
    /// Batch card/theme zh projections (stage D).
    #[serde(default = "default_true")]
    pub auto_memory_zh: bool,
}

impl Default for SourceWorkConfig {
    fn default() -> Self {
        Self {
            auto_summarize: true,
            auto_translate: true,
            auto_notify: false,
            auto_max_per_run: default_auto_max(),
            auto_claim_zh: true,
            auto_claim_zh_max_per_run: 0,
            auto_memory_zh: true,
        }
    }
}

impl SourceWorkConfig {
    /// Load from `<vault>/.ovp/source-work.toml`. Missing → defaults.
    /// Parse errors → `Err` (fail loud so a bad file is not silently ignored).
    pub fn load(vault_root: &Path) -> Result<Self, String> {
        let path = vault_root.join(CONFIG_REL);
        if !path.is_file() {
            return Ok(Self::default());
        }
        let raw = fs::read_to_string(&path)
            .map_err(|e| format!("reading {}: {e}", path.display()))?;
        toml::from_str(&raw).map_err(|e| format!("parsing {}: {e}", path.display()))
    }

    /// Write a commented template if the file does not exist yet.
    pub fn ensure_template(vault_root: &Path) -> Result<bool, String> {
        let path = vault_root.join(CONFIG_REL);
        if path.is_file() {
            return Ok(false);
        }
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("mkdir .ovp: {e}"))?;
        }
        fs::write(&path, DEFAULT_TEMPLATE).map_err(|e| format!("write template: {e}"))?;
        Ok(true)
    }
}

const DEFAULT_TEMPLATE: &str = r#"# OVP source-work enrichment (.ovp/source-work.toml)
# Controls automatic deep summary + EN→zh after daily succeeds, and
# bilingual claim/memory projection defaults.
#
# Authority stays English (crystal ledger). zh/summary are rebuildable
# projections under 40-Resources/Source-Work/ and .ovp/crystal/*_zh.json.

# After daily succeeds on a source, enqueue deep summary when missing.
auto_summarize = true
# After daily succeeds, enqueue refined EN→zh when the source looks English.
auto_translate = true
# Notify the desktop/browser for auto jobs (manual queue still notifies).
auto_notify = false
# Cap auto-enqueues per daily run (0 = unlimited). Protects LLM budget.
auto_max_per_run = 30
# Prefer claim_zh / memory zh projections when running bilingual batch CLIs.
auto_claim_zh = true
# Cap claim_zh translations per crystal-synth tail run (0 = unlimited).
# Separate from auto_max_per_run: an existing claims backlog drains without
# being throttled by the daily enqueue budget; a provider outage is contained
# by the batch's consecutive-failure breaker, not by this cap.
auto_claim_zh_max_per_run = 0
auto_memory_zh = true
"#;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_file_is_product_default() {
        let tmp = tempfile::tempdir().unwrap();
        let cfg = SourceWorkConfig::load(tmp.path()).unwrap();
        assert!(cfg.auto_summarize);
        assert!(cfg.auto_translate);
        assert!(!cfg.auto_notify);
        assert_eq!(cfg.auto_max_per_run, 30);
        assert_eq!(cfg.auto_claim_zh_max_per_run, 0);
    }

    #[test]
    fn parses_overrides() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join(CONFIG_REL);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(
            &path,
            "auto_summarize = false\nauto_translate = true\nauto_max_per_run = 5\nauto_claim_zh_max_per_run = 10\n",
        )
        .unwrap();
        let cfg = SourceWorkConfig::load(tmp.path()).unwrap();
        assert!(!cfg.auto_summarize);
        assert!(cfg.auto_translate);
        assert_eq!(cfg.auto_max_per_run, 5);
        assert_eq!(cfg.auto_claim_zh_max_per_run, 10);
    }

    /// A pre-field source-work.toml (no `auto_claim_zh_max_per_run`) loads
    /// with the tail cap defaulting to 0 = unlimited.
    #[test]
    fn legacy_config_without_claim_zh_cap_defaults_unlimited() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join(CONFIG_REL);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, "auto_max_per_run = 7\n").unwrap();
        let cfg = SourceWorkConfig::load(tmp.path()).unwrap();
        assert_eq!(cfg.auto_max_per_run, 7);
        assert_eq!(cfg.auto_claim_zh_max_per_run, 0);
    }

    #[test]
    fn ensure_template_idempotent() {
        let tmp = tempfile::tempdir().unwrap();
        assert!(SourceWorkConfig::ensure_template(tmp.path()).unwrap());
        assert!(!SourceWorkConfig::ensure_template(tmp.path()).unwrap());
        let cfg = SourceWorkConfig::load(tmp.path()).unwrap();
        assert!(cfg.auto_summarize);
    }
}
