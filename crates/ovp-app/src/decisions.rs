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
            "chat" => chat::validate_profile(profile).is_ok(),
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
                if profile.provider == "chat" {
                    chat::CAPABILITIES
                } else {
                    DecisionCapabilities {
                        boolean: true,
                        choice: true,
                        score: true,
                        batch: true,
                        probabilities: true,
                    }
                },
                cache,
            )?));
        }
        if profile.provider == "chat" {
            return build_chat(
                profile,
                execution,
                cache,
                &std::collections::BTreeMap::new(),
            );
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

    fn chat_profile() -> DecisionProfile {
        DecisionProfile {
            id: "same-model-v1".into(),
            provider: "chat".into(),
            endpoint: "https://example.invalid/v1/messages".into(),
            model: "frozen-model-v1".into(),
            credential_ref: "UNSET_TEST_KEY".into(),
        }
    }
    #[test]
    fn chat_replay_is_offline_even_with_broken_provider_config() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::create_dir(tmp.path().join(".ovp")).unwrap();
        std::fs::write(tmp.path().join(".ovp/providers.toml"), "broken = [").unwrap();
        let factory = ProvidersDecisionFactory::new(tmp.path());
        let profile = chat_profile();
        let mut client = factory
            .build(&profile, ExecutionMode::Replay, &tmp.path().join("cache"))
            .unwrap();
        assert!(!client.capabilities().probabilities);
        let request = DecisionRequest {
            namespace: "test/v1".into(),
            state: serde_json::json!({}),
            evidence: vec![],
            questions: std::collections::BTreeMap::from([(
                "q".into(),
                DecisionQuestion {
                    instructions: "Select yes or no".into(),
                    kind: QuestionKind::Boolean {
                        yes: "yes".into(),
                        no: "no".into(),
                    },
                },
            )]),
        };
        assert!(matches!(
            client.decide(&request),
            Err(DecisionError::CacheMiss { .. })
        ));
        assert!(!SearchDecisionFactory.supports(&profile, ExecutionMode::Live));
        assert!(
            SearchDecisionFactory
                .build(&profile, ExecutionMode::Live, tmp.path())
                .is_err()
        );
    }
    #[test]
    fn chat_supplier_is_available_but_off_does_not_build_it() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("decisions.json");
        std::fs::write(
            &path,
            serde_json::to_vec(&serde_json::json!({
                "profiles":{"same-model-v1":chat_profile()},
                "capabilities":{"strength_check":{"mode":"off"}}
            }))
            .unwrap(),
        )
        .unwrap();
        let settings = load_settings(&path).unwrap();
        assert_eq!(
            settings.capabilities["strength_check"].mode,
            DecisionMode::Off
        );
        let mut unsafe_profile = chat_profile();
        unsafe_profile.endpoint = "http://remote.invalid/v1/messages".into();
        assert!(!BuiltinDecisionFactory.supports(&unsafe_profile, ExecutionMode::Replay));
    }
}

/// Shared operator path for CLI, Portal and MCP; absence means baseline only.
pub fn load_optional_settings(vault: &Path) -> Result<Option<DecisionSettings>, DecisionError> {
    let path = vault.join(".ovp/decisions.json");
    match std::fs::metadata(&path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(_) => Err(DecisionError::InvalidRequest(
            "cannot stat decision settings",
        )),
        Ok(meta) if meta.len() > 1024 * 1024 => {
            Err(DecisionError::InvalidRequest("decision settings too large"))
        }
        Ok(_) => load_settings(&path).map(Some),
    }
}

/// Search-specific latency budget. Replay uses the same strict offline client.
#[derive(Debug)]
pub struct SearchDecisionFactory;
impl DecisionClientFactory for SearchDecisionFactory {
    fn supports(&self, profile: &DecisionProfile, execution: ExecutionMode) -> bool {
        // The chat control is an isolated experiment supplier; its slower
        // generation path has not qualified for the interactive search budget.
        profile.provider != "chat" && BuiltinDecisionFactory.supports(profile, execution)
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
            return BuiltinDecisionFactory.build(profile, execution, cache);
        }
        #[cfg(feature = "decision-live")]
        {
            let client = Box::new(TypeSafeDecisionClient::from_env(
                profile.clone(),
                HttpOptions {
                    timeout: std::time::Duration::from_millis(500),
                    max_retries: 0,
                    ..HttpOptions::default()
                },
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

/// Experiment factory resolving existing vault provider configuration without
/// mutating global environment. Replay never reads providers.toml or secrets.
pub struct ProvidersDecisionFactory {
    vault: std::path::PathBuf,
}
impl ProvidersDecisionFactory {
    pub fn new(vault: impl Into<std::path::PathBuf>) -> Self {
        Self {
            vault: vault.into(),
        }
    }
}
impl DecisionClientFactory for ProvidersDecisionFactory {
    fn supports(&self, profile: &DecisionProfile, execution: ExecutionMode) -> bool {
        BuiltinDecisionFactory.supports(profile, execution)
    }
    fn build(
        &self,
        profile: &DecisionProfile,
        execution: ExecutionMode,
        cache: &Path,
    ) -> Result<Box<dyn DecisionClient>, DecisionError> {
        if profile.provider != "chat" || execution == ExecutionMode::Replay {
            return BuiltinDecisionFactory.build(profile, execution, cache);
        }
        let file = ovp_domain::providers::read_providers_file(&self.vault)
            .map_err(|_| DecisionError::InvalidRequest("cannot read providers configuration"))?;
        build_chat(profile, execution, cache, &file)
    }
}

fn build_chat(
    profile: &DecisionProfile,
    execution: ExecutionMode,
    cache: &Path,
    file: &std::collections::BTreeMap<String, String>,
) -> Result<Box<dyn DecisionClient>, DecisionError> {
    chat::validate_profile(profile)?;
    #[cfg(feature = "decision-live")]
    {
        let lookup = |name: &str| {
            std::env::var(name)
                .ok()
                .filter(|v| !v.trim().is_empty())
                .or_else(|| file.get(name).cloned())
        };
        let cfg = ovp_llm::LiveClientConfig::from_lookup(lookup)
            .map_err(|_| DecisionError::InvalidRequest("invalid chat provider configuration"))?;
        if cfg.max_tokens.is_some_and(|v| v != chat::MAX_TOKENS) {
            return Err(DecisionError::InvalidRequest(
                "chat adapter token budget differs from configured budget",
            ));
        }
        // No ambient model/endpoint overrides: cache identity must describe the
        // exact provider receiving the request, including same-model controls.
        if cfg.model.as_ref().is_some_and(|v| v != &profile.model)
            || cfg
                .base_url
                .as_ref()
                .is_some_and(|v| v != &profile.endpoint)
        {
            return Err(DecisionError::InvalidRequest(
                "chat profile differs from configured model or endpoint",
            ));
        }
        let key = lookup(&profile.credential_ref)
            .filter(|v| !v.trim().is_empty())
            .ok_or(DecisionError::MissingCredential)?;
        let live = ovp_llm::AnthropicBlockingClient::new(key)
            .with_base_url(profile.endpoint.clone())
            .with_decision_transport(
                std::time::Duration::from_secs(ovp_llm::live::clamp_timeout(cfg.timeout_secs, 180)),
                cfg.no_proxy,
            )
            .map_err(|_| DecisionError::Transport)?;
        // One HTTP attempt, no inner cassette or retries. The adapter protocol
        // fixes token/temperature parameters so the outer key remains honest.
        let client: Box<dyn DecisionClient> = Box::new(chat::ChatDecisionClient::new(
            profile.clone(),
            Box::new(live),
        )?);
        if execution == ExecutionMode::Record {
            Ok(Box::new(CachedDecisionClient::record(client, cache)?))
        } else {
            Ok(client)
        }
    }
    #[cfg(not(feature = "decision-live"))]
    {
        let _ = (execution, cache, file);
        Err(DecisionError::Unsupported("decision-live build feature"))
    }
}
