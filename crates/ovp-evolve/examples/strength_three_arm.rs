//! Explicit local freeze / live observation tool. Never writes the operator vault.
use ovp_domain::crystal::{
    Citation, CrystalCandidate, CrystalClaim,
    synth::{self, CatalogCase, UnitsCatalog},
};
#[cfg(feature = "decision-live")]
use ovp_llm::decision::DecisionProfile;
use serde::{Deserialize, Serialize};
#[cfg(feature = "decision-live")]
use serde_json::Value;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    fs::{self, OpenOptions},
    io::Write,
    path::Path,
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
#[derive(Clone, Serialize, Deserialize)]
struct CitationInput {
    case_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    unit_id: Option<String>,
    quote: String,
}
#[derive(Clone, Serialize, Deserialize)]
struct Item {
    id: String,
    split: String,
    lang: String,
    claim: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    caveat: Option<String>,
    citations: Vec<CitationInput>,
}
#[derive(Serialize, Deserialize)]
struct Frozen {
    original: Item,
    candidate: CrystalCandidate,
    catalog: UnitsCatalog,
    input_digest: String,
}
#[derive(Serialize, Deserialize)]
struct Bundle {
    schema: String,
    items_digest: String,
    items: Vec<Frozen>,
}
#[cfg(feature = "decision-live")]
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Profiles {
    incumbent: DecisionProfile,
    candidate: DecisionProfile,
}
fn hash<T: Serialize>(value: &T) -> Result<String> {
    Ok(format!("{:x}", Sha256::digest(serde_json::to_vec(value)?)))
}
fn save<T: Serialize>(path: &Path, value: &T) -> Result<()> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path)?;
    file.write_all(&serde_json::to_vec_pretty(value)?)?;
    file.sync_all()?;
    Ok(())
}
fn new_dir(path: &Path) -> Result<()> {
    fs::create_dir(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}
fn freeze_item(item: Item, source: &UnitsCatalog) -> Result<Frozen> {
    if item.id.trim().is_empty()
        || item.claim.trim().is_empty()
        || item.citations.is_empty()
        || !matches!(item.split.as_str(), "gold" | "development" | "holdout")
    {
        return Err("invalid item".into());
    }
    let mut catalog = UnitsCatalog::default();
    let mut citations = Vec::new();
    for c in &item.citations {
        if c.quote.trim().is_empty() {
            return Err("empty quote".into());
        }
        let case = source.cases.get(&c.case_id).ok_or("source case absent")?;
        let matches: Vec<_> = case
            .units
            .iter()
            .filter(|u| {
                c.unit_id.as_ref().is_none_or(|id| &u.unit_id == id) && u.quote.contains(&c.quote)
            })
            .collect();
        if matches.len() != 1 {
            return Err(format!(
                "citation requires unique accepted unit; matches={}",
                matches.len()
            )
            .into());
        }
        let unit = matches[0];
        let local = catalog
            .cases
            .entry(c.case_id.clone())
            .or_insert_with(|| CatalogCase {
                title: case.title.clone(),
                units: vec![],
            });
        if !local.units.iter().any(|u| u.unit_id == unit.unit_id) {
            local.units.push(unit.clone());
        }
        citations.push(Citation {
            case_id: c.case_id.clone(),
            unit_id: unit.unit_id.clone(),
            quote: c.quote.clone(),
            claimed_line: unit.line,
        });
    }
    let candidate = CrystalCandidate {
        items: vec![CrystalClaim {
            id: item.id.clone(),
            claim: item.claim.clone(),
            theme: String::new(),
            citations,
            caveat: item.caveat.clone(),
        }],
    };
    ovp_domain::decision_strength::request(&candidate, &catalog)?;
    let input_digest = hash(&(&item, &candidate, &catalog))?;
    Ok(Frozen {
        original: item,
        candidate,
        catalog,
        input_digest,
    })
}
fn freeze(items_path: &Path, reader: &Path, output: &Path) -> Result<()> {
    let items: Vec<Item> = serde_json::from_slice(&fs::read(items_path)?)?;
    new_dir(output)?;
    let source = match synth::collect_catalog(reader) {
        Ok(v) => v,
        Err(e) => {
            save(
                &output.join("failure.json"),
                &json!({"phase":"catalog","error":e.to_string()}),
            )?;
            return Err("catalog collection failed; see private failure artifact".into());
        }
    };
    let items_digest = hash(&items)?;
    let mut frozen = Vec::new();
    let mut ids = BTreeSet::new();
    let mut failures = Vec::new();
    for (index, item) in items.into_iter().enumerate() {
        if !ids.insert(item.id.clone()) {
            failures.push(json!({"index":index,"error":"duplicate item id"}));
            continue;
        }
        match freeze_item(item, &source) {
            Ok(v) => frozen.push(v),
            Err(e) => failures.push(json!({"index":index,"error":e.to_string()})),
        }
    }
    if !failures.is_empty() || frozen.is_empty() {
        save(
            &output.join("failure.json"),
            &json!({"failures":failures,"resolved":frozen.len()}),
        )?;
        return Err("freeze failed; see private failure artifact".into());
    }
    save(
        &output.join("bundle.json"),
        &Bundle {
            schema: "strength-three-arm-input/v1".into(),
            items_digest,
            items: frozen,
        },
    )?;
    println!("frozen bundle written");
    Ok(())
}
#[cfg(any(feature = "decision-live", test))]
fn validate_bundle(bundle: &Bundle) -> Result<()> {
    if bundle.schema != "strength-three-arm-input/v1" || bundle.items.is_empty() {
        return Err("invalid bundle schema or empty items".into());
    }
    let originals: Vec<_> = bundle.items.iter().map(|v| &v.original).collect();
    if hash(&originals)? != bundle.items_digest {
        return Err("items digest mismatch".into());
    }
    let mut ids = BTreeSet::new();
    for item in &bundle.items {
        if !ids.insert(&item.original.id)
            || hash(&(&item.original, &item.candidate, &item.catalog))? != item.input_digest
        {
            return Err("bundle binding mismatch".into());
        }
        let reconstructed = freeze_item(item.original.clone(), &item.catalog)?;
        if reconstructed.input_digest != item.input_digest {
            return Err("bundle candidate differs from original".into());
        }
    }
    Ok(())
}
#[cfg(any(feature = "decision-live", test))]
fn incumbent_reply_failure(reply: &serde_json::Value, model: &str) -> Option<String> {
    if reply.get("model").and_then(serde_json::Value::as_str) != Some(model) {
        return Some("incumbent reply model mismatch".into());
    }
    if reply.get("stop_reason").and_then(serde_json::Value::as_str) != Some("end_turn") {
        return Some("incumbent reply did not end with EndTurn".into());
    }
    None
}
#[cfg(feature = "decision-live")]
fn run(
    bundle_path: &Path,
    vault: &Path,
    profiles_path: &Path,
    output: &Path,
    limit: usize,
) -> Result<()> {
    use ovp_app::decisions::{BuiltinDecisionFactory, ProvidersDecisionFactory};
    use ovp_llm::{
        ModelClient,
        decision::runtime::{DecisionClientFactory, ExecutionMode},
    };
    let bundle: Bundle = serde_json::from_slice(&fs::read(bundle_path)?)?;
    validate_bundle(&bundle)?;
    let profiles: Profiles = serde_json::from_slice(&fs::read(profiles_path)?)?;
    profiles.incumbent.validate()?;
    profiles.candidate.validate()?;
    ovp_llm::decision::chat::validate_profile(&profiles.incumbent)?;
    if profiles.incumbent.provider != "chat"
        || profiles.candidate.provider != "typesafe"
        || limit == 0
    {
        return Err("requires chat incumbent, typesafe candidate and positive limit".into());
    }
    let selected: Vec<_> = bundle
        .items
        .iter()
        .filter(|v| matches!(v.original.split.as_str(), "gold" | "development"))
        .take(limit)
        .collect();
    if selected.is_empty() {
        return Err("no development cases".into());
    }
    new_dir(output)?;
    for arm in ["a", "b", "c"] {
        new_dir(&output.join(arm))?;
    }
    save(
        &output.join("plan.json"),
        &json!({"schema":"strength-three-arm-observation/v1","bundle_digest":hash(&bundle)?,"profiles":profiles,"split":"development","split_mapping":{"gold":"development","development":"development","holdout":"excluded"},"adapter_protocol":ovp_llm::decision::chat::NAMESPACE,"selected":selected.iter().map(|i|json!({"case_id":i.original.id,"original_split":i.original.split,"input_digest":i.input_digest})).collect::<Vec<_>>(),"max_tokens_ab":8192,"temperature_ab":null,"labels":"unknown","quality_acceptance":false}),
    )?;
    let file = ovp_domain::providers::read_providers_file(vault)?;
    let lookup = |name: &str| {
        std::env::var(name)
            .ok()
            .filter(|v| !v.trim().is_empty())
            .or_else(|| file.get(name).cloned())
    };
    let mut a: std::result::Result<ovp_llm::AnthropicBlockingClient, String> = (|| {
        let cfg = ovp_llm::LiveClientConfig::from_lookup(lookup)?;
        if cfg
            .model
            .as_ref()
            .is_some_and(|m| m != &profiles.incumbent.model)
            || cfg
                .base_url
                .as_ref()
                .is_some_and(|v| v != &profiles.incumbent.endpoint)
            || cfg.max_tokens.is_some_and(|v| v != 8192)
        {
            return Err("incumbent profile/config mismatch".into());
        }
        let key =
            lookup(&profiles.incumbent.credential_ref).ok_or("missing incumbent credential")?;
        ovp_llm::AnthropicBlockingClient::new(key)
            .with_base_url(profiles.incumbent.endpoint.clone())
            .with_decision_transport(
                std::time::Duration::from_secs(ovp_llm::live::clamp_timeout(cfg.timeout_secs, 180)),
                cfg.no_proxy,
            )
            .map_err(|e| e.to_string())
    })();
    let mut b = ProvidersDecisionFactory::new(vault).build(
        &profiles.incumbent,
        ExecutionMode::Live,
        &output.join("b"),
    );
    let mut c =
        BuiltinDecisionFactory.build(&profiles.candidate, ExecutionMode::Live, &output.join("c"));
    let secrets: Vec<_> = [
        &profiles.incumbent.credential_ref,
        &profiles.candidate.credential_ref,
    ]
    .iter()
    .filter_map(|k| lookup(k))
    .filter(|v| !v.is_empty())
    .collect();
    let redact = |mut s: String| {
        for secret in &secrets {
            s = s.replace(secret, "[REDACTED]");
        }
        s
    };
    let mut observations = Vec::new();
    for (index, item) in selected.iter().enumerate() {
        let mut request = synth::strength_request(&item.candidate, &item.catalog);
        request.model = profiles.incumbent.model.clone();
        request.max_tokens = 8192;
        request.temperature = None;
        let typed = ovp_domain::decision_strength::request(&item.candidate, &item.catalog)?;
        for arm in ["a", "b", "c"] {
            let dir = output.join(arm).join(format!("{index:04}"));
            new_dir(&dir)?;
            let request_value = if arm == "a" {
                serde_json::to_value(&request)?
            } else {
                serde_json::to_value(&typed)?
            };
            save(&dir.join("request.json"), &request_value)?;
            let start = std::time::Instant::now();
            let result: std::result::Result<Value, String> = match arm {
                "a" => match &mut a {
                    Ok(client) => client
                        .call(&request)
                        .map(|r| serde_json::to_value(r).expect("serializable reply"))
                        .map_err(|e| e.to_string()),
                    Err(e) => Err(e.clone()),
                },
                _ => match if arm == "b" { &mut b } else { &mut c } {
                    Ok(client) => client
                        .decide(&typed)
                        .map(|r| serde_json::to_value(r).expect("serializable reply"))
                        .map_err(|e| e.to_string()),
                    Err(e) => Err(e.to_string()),
                },
            };
            let elapsed_ms = start.elapsed().as_millis();
            let (mut success, reply, mut failure) = match result {
                Ok(reply) => (true, Some(reply), None),
                Err(e) => (false, None, Some(redact(e))),
            };
            let protocol_failure = reply
                .as_ref()
                .filter(|_| arm == "a")
                .and_then(|raw| incumbent_reply_failure(raw, &profiles.incumbent.model));
            if let Some(reason) = protocol_failure {
                success = false;
                failure = Some(reason);
            }
            let observation = json!({"arm":arm,"case_id":item.original.id,"split":"development","original_split":item.original.split,"lang":item.original.lang,"input_digest":item.input_digest,"source_revision":hash(&item.catalog)?,"request_digest":hash(&request_value)?,"profile":if arm=="c" {&profiles.candidate} else {&profiles.incumbent},"elapsed_ms":elapsed_ms,"transport_success":success,"reply":reply,"failure":failure});
            save(&dir.join("observation.json"), &observation)?;
            observations.push(json!({"arm":arm,"case_id":item.original.id,"input_digest":item.input_digest,"transport_success":success,"elapsed_ms":elapsed_ms,"artifact":format!("{arm}/{index:04}/observation.json")}));
        }
    }
    save(
        &output.join("coverage.json"),
        &json!({"expected_cases_per_arm":selected.len(),"expected_observations":selected.len()*3,"observations":observations,"labels":"unknown","quality_acceptance":false}),
    )?;
    println!("observations saved; no quality verdict or silver labels produced");
    Ok(())
}
#[cfg(not(feature = "decision-live"))]
fn run(_: &Path, _: &Path, _: &Path, _: &Path, _: usize) -> Result<()> {
    Err("run requires --features decision-live; freeze is offline".into())
}
fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("freeze") if args.len()==4=>freeze(Path::new(&args[1]),Path::new(&args[2]),Path::new(&args[3])),
        Some("run") if (5..=6).contains(&args.len())=>run(Path::new(&args[1]),Path::new(&args[2]),Path::new(&args[3]),Path::new(&args[4]),args.get(5).map(|s|s.parse()).transpose()?.unwrap_or(usize::MAX)),
        _=>Err("usage: freeze <items.json> <reader> <new-output-dir> | run <bundle.json> <vault> <profiles.json> <new-output-dir> [limit]".into())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use ovp_domain::crystal::synth::CatalogUnit;
    fn fixture() -> (Item, UnitsCatalog) {
        (
            Item {
                id: "synthetic".into(),
                split: "gold".into(),
                lang: "en".into(),
                claim: "Some studies suggest improvement".into(),
                caveat: None,
                citations: vec![CitationInput {
                    case_id: "source".into(),
                    unit_id: None,
                    quote: "Some studies suggest improvement".into(),
                }],
            },
            UnitsCatalog {
                cases: std::collections::BTreeMap::from([(
                    "source".into(),
                    CatalogCase {
                        title: "Synthetic source".into(),
                        units: vec![CatalogUnit {
                            unit_id: "u1".into(),
                            line: Some(1),
                            quote: "Some studies suggest improvement, with limits.".into(),
                            attribution: "author".into(),
                            modality: "suggested".into(),
                        }],
                    },
                )]),
            },
        )
    }
    #[test]
    #[cfg(feature = "decision-live")]
    fn unsafe_incumbent_endpoint_fails_before_creating_run() {
        let (item, catalog) = fixture();
        let bundle = Bundle {
            schema: "strength-three-arm-input/v1".into(),
            items_digest: hash(&vec![item.clone()]).unwrap(),
            items: vec![freeze_item(item, &catalog).unwrap()],
        };
        let dir = tempfile::tempdir().unwrap();
        let bundle_path = dir.path().join("bundle.json");
        let profiles_path = dir.path().join("profiles.json");
        save(&bundle_path, &bundle).unwrap();
        save(&profiles_path, &json!({
            "incumbent": {"id":"control", "provider":"chat", "endpoint":"http://remote.example/v1/messages", "model":"test", "credential_ref":"TEST_KEY"},
            "candidate": {"id":"candidate", "provider":"typesafe", "endpoint":"https://api.typesafe.ai/v1/systemone", "model":"jev-1.13.0", "credential_ref":"TEST_KEY"}
        })).unwrap();
        let output = dir.path().join("run");
        let error = run(&bundle_path, dir.path(), &profiles_path, &output, 1).unwrap_err();
        assert!(error.to_string().contains("endpoint"), "{error}");
        assert!(!output.exists());
    }
    #[test]
    fn freeze_requires_unique_accepted_quote_and_binds_inputs() {
        let (item, mut catalog) = fixture();
        let frozen = freeze_item(item.clone(), &catalog).unwrap();
        assert_eq!(frozen.candidate.items[0].citations[0].unit_id, "u1");
        let mut bundle = Bundle {
            schema: "strength-three-arm-input/v1".into(),
            items_digest: hash(&vec![item.clone()]).unwrap(),
            items: vec![frozen],
        };
        validate_bundle(&bundle).unwrap();
        bundle.items[0].candidate.items[0].claim = "changed".into();
        assert!(validate_bundle(&bundle).is_err());
        let case = catalog.cases.get_mut("source").unwrap();
        case.units.push(case.units[0].clone());
        assert!(freeze_item(item.clone(), &catalog).is_err());
        catalog.cases.clear();
        assert!(freeze_item(item, &catalog).is_err());
    }
    #[test]
    fn explicit_unit_binding_preserves_caveat_and_never_falls_back() {
        let (mut item, mut catalog) = fixture();
        let case = catalog.cases.get_mut("source").unwrap();
        let mut duplicate = case.units[0].clone();
        duplicate.unit_id = "u2".into();
        case.units.push(duplicate);
        item.citations[0].unit_id = Some("u1".into());
        item.caveat = Some("Only preliminary evidence".into());
        let frozen = freeze_item(item.clone(), &catalog).unwrap();
        assert_eq!(frozen.candidate.items[0].caveat, item.caveat);
        item.citations[0].unit_id = Some("absent".into());
        assert!(freeze_item(item.clone(), &catalog).is_err());
        item.citations[0].unit_id = Some("u1".into());
        catalog.cases.get_mut("source").unwrap().units[0].quote = "unrelated".into();
        assert!(freeze_item(item, &catalog).is_err());
    }
    #[test]
    fn incumbent_requires_exact_model_and_end_turn() {
        assert!(
            incumbent_reply_failure(
                &json!({"model":"expected","stop_reason":"end_turn"}),
                "expected"
            )
            .is_none()
        );
        assert!(
            incumbent_reply_failure(
                &json!({"model":"other","stop_reason":"end_turn"}),
                "expected"
            )
            .is_some()
        );
        for reason in [
            "max_tokens",
            "stop_sequence",
            "tool_use",
            "refusal",
            "unknown",
        ] {
            assert!(
                incumbent_reply_failure(
                    &json!({"model":"expected","stop_reason":reason}),
                    "expected"
                )
                .is_some()
            );
        }
    }
    #[test]
    fn incumbent_and_typed_control_share_generation_parameters() {
        let (item, catalog) = fixture();
        let frozen = freeze_item(item, &catalog).unwrap();
        let incumbent = synth::strength_request(&frozen.candidate, &frozen.catalog);
        let typed =
            ovp_domain::decision_strength::request(&frozen.candidate, &frozen.catalog).unwrap();
        let profile = ovp_llm::decision::DecisionProfile {
            id: "matched-test".into(),
            provider: "chat".into(),
            endpoint: "https://example.invalid/v1/messages".into(),
            model: incumbent.model.clone(),
            credential_ref: "NO_KEY".into(),
        };
        let control = ovp_llm::decision::chat::encode_request(&profile, &typed).unwrap();
        assert_eq!(incumbent.max_tokens, control.max_tokens);
        assert_eq!(incumbent.temperature, control.temperature);
        assert_eq!(incumbent.model, control.model);
    }
    #[test]
    fn private_writes_refuse_overwrite() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("artifact.json");
        save(&path, &json!({"synthetic":true})).unwrap();
        assert!(save(&path, &json!({})).is_err());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(path).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
    }
}
