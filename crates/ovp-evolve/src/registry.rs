use std::collections::HashSet;
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::types::{ChangeSurface, QualityBucket};

/// A registered component that can be the target of evolution candidates.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ComponentRecord {
    pub id: String,
    pub surface: ChangeSurface,
    #[serde(default)]
    pub current_version: Option<String>,
    pub file: String,
    pub quality_buckets: Vec<QualityBucket>,
    #[serde(default)]
    pub regression_fixtures: Vec<String>,
}

/// The full component registry loaded from `evolution/components.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ComponentRegistry {
    pub components: Vec<ComponentRecord>,
}

#[derive(Debug, thiserror::Error)]
pub enum RegistryError {
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
    #[error("json: {0}")]
    Json(#[from] serde_json::Error),
    #[error("validation: {0}")]
    Validation(String),
}

impl ComponentRegistry {
    /// Load and validate the registry from a JSON file.
    pub fn load(path: &Path) -> Result<Self, RegistryError> {
        let data = std::fs::read_to_string(path)?;
        let registry: Self = serde_json::from_str(&data)?;
        registry.validate()?;
        Ok(registry)
    }

    /// Validate internal consistency.
    pub fn validate(&self) -> Result<(), RegistryError> {
        let mut seen_ids = HashSet::new();
        for c in &self.components {
            if c.id.is_empty() {
                return Err(RegistryError::Validation(
                    "component id must not be empty".into(),
                ));
            }
            if !seen_ids.insert(&c.id) {
                return Err(RegistryError::Validation(format!(
                    "duplicate component id: {}",
                    c.id
                )));
            }
            if c.file.is_empty() {
                return Err(RegistryError::Validation(format!(
                    "component {} has empty file path",
                    c.id
                )));
            }
            if c.quality_buckets.is_empty() {
                return Err(RegistryError::Validation(format!(
                    "component {} has no quality_buckets",
                    c.id
                )));
            }
        }
        Ok(())
    }

    /// Look up a component by ID.
    pub fn get(&self, id: &str) -> Option<&ComponentRecord> {
        self.components.iter().find(|c| c.id == id)
    }

    /// All registered component IDs.
    pub fn ids(&self) -> Vec<&str> {
        self.components.iter().map(|c| c.id.as_str()).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid_registry_loads() {
        let json = r#"{
            "components": [
                {
                    "id": "prompt.unit_extract",
                    "surface": "prompt",
                    "current_version": "v5",
                    "file": "crates/ovp-domain/prompts/unit_extraction.md",
                    "quality_buckets": ["extraction_fidelity", "coverage"]
                }
            ]
        }"#;
        let reg: ComponentRegistry = serde_json::from_str(json).unwrap();
        reg.validate().unwrap();
        assert_eq!(reg.components.len(), 1);
        assert_eq!(reg.get("prompt.unit_extract").unwrap().surface, ChangeSurface::Prompt);
    }

    #[test]
    fn duplicate_id_rejected() {
        let json = r#"{
            "components": [
                { "id": "a", "surface": "prompt", "file": "x.md", "quality_buckets": ["coverage"] },
                { "id": "a", "surface": "runtime", "file": "y.rs", "quality_buckets": ["coverage"] }
            ]
        }"#;
        let reg: ComponentRegistry = serde_json::from_str(json).unwrap();
        assert!(reg.validate().is_err());
    }

    #[test]
    fn empty_buckets_rejected() {
        let json = r#"{
            "components": [
                { "id": "x", "surface": "prompt", "file": "x.md", "quality_buckets": [] }
            ]
        }"#;
        let reg: ComponentRegistry = serde_json::from_str(json).unwrap();
        assert!(reg.validate().is_err());
    }
}
