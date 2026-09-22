//! Operational decision configuration and built-in supplier assembly.
use ovp_llm::decision::{runtime::*, *};
use std::path::Path;

pub fn load_settings(path: &Path) -> Result<DecisionSettings, DecisionError> {
    let bytes = std::fs::read(path)
        .map_err(|_| DecisionError::InvalidRequest("cannot read decision settings"))?;
    let settings = DecisionSettings::parse(&bytes)?;
    settings.validate(&BuiltinDecisionFactory)?;
    Ok(settings)
}

pub struct BuiltinDecisionFactory;
impl DecisionClientFactory for BuiltinDecisionFactory {
    fn supports(&self, profile: &DecisionProfile, execution: ExecutionMode) -> bool {
        match profile.provider.as_str() {
            "typesafe" => typesafe::validate_profile(profile).is_ok(),
            "fixture" => execution == ExecutionMode::Replay,
            _ => false,
        }
    }
    fn build(
        &self,
        profile: &DecisionProfile,
        execution: ExecutionMode,
        cache: &Path,
    ) -> Result<Box<dyn DecisionClient>, DecisionError> {
        if !self.supports(profile, execution) {
            return Err(DecisionError::Unsupported("provider execution mode"));
        }
        if execution == ExecutionMode::Replay {
            return Ok(Box::new(CachedDecisionClient::replay(
                profile.clone(),
                DecisionCapabilities {
                    boolean: true,
                    choice: true,
                    score: true,
                    batch: true,
                    probabilities: true,
                },
                cache,
            )?));
        }
        #[cfg(feature = "decision-live")]
        {
            let client = Box::new(TypeSafeDecisionClient::from_env(
                profile.clone(),
                HttpOptions::default(),
            )?);
            if execution == ExecutionMode::Record {
                Ok(Box::new(CachedDecisionClient::record(client, cache)?))
            } else {
                Ok(client)
            }
        }
        #[cfg(not(feature = "decision-live"))]
        Err(DecisionError::Unsupported("decision-live build feature"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn operational_example_is_off_and_replay_needs_no_credentials() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let settings =
            load_settings(&root.join("manifests/decision-settings.example.json")).unwrap();
        assert!(
            settings
                .capabilities
                .values()
                .all(|c| c.mode == DecisionMode::Off)
        );
        let profile = &settings.profiles["typesafe-primary"];
        let tmp = tempfile::tempdir().unwrap();
        let mut client = BuiltinDecisionFactory
            .build(profile, ExecutionMode::Replay, tmp.path())
            .unwrap();
        let fixture: serde_json::Value = serde_json::from_slice(
            &std::fs::read(root.join("fixtures/decision-paired-v1/cases.json")).unwrap(),
        )
        .unwrap();
        let request: DecisionRequest =
            serde_json::from_value(fixture["cases"][0]["request"].clone()).unwrap();
        assert!(matches!(
            client.decide(&request),
            Err(DecisionError::CacheMiss { .. })
        ));
    }
    #[test]
    fn unknown_provider_is_rejected_before_client_construction() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("settings.json");
        std::fs::write(&path,r#"{"profiles":{"p":{"id":"p","provider":"typo","endpoint":"fixture://test","model":"v1","credential_ref":"UNUSED"}}}"#).unwrap();
        assert!(matches!(
            load_settings(&path),
            Err(DecisionError::Unsupported("provider"))
        ));
    }
}
