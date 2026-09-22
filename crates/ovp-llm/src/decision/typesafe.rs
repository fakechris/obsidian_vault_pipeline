//! Pure TypeSafe wire mapping. Available without the live HTTP feature.
use super::*;
use serde::{Deserialize, Deserializer};
use serde_json::{Value, json};
use std::collections::BTreeMap;

pub const CAPABILITIES: DecisionCapabilities = DecisionCapabilities {
    boolean: true,
    choice: true,
    score: true,
    batch: true,
    probabilities: true,
};

pub fn validate_profile(profile: &DecisionProfile) -> Result<(), DecisionError> {
    profile.validate()?;
    let version = profile.model.strip_prefix("jev-").unwrap_or("");
    let parts: Vec<_> = version.split('.').collect();
    if profile.provider != "typesafe"
        || parts.len() != 3
        || parts
            .iter()
            .any(|p| p.is_empty() || !p.bytes().all(|b| b.is_ascii_digit()))
    {
        return Err(DecisionError::InvalidRequest(
            "TypeSafe requires provider typesafe and a pinned jev-x.y.z model",
        ));
    }
    Ok(())
}

pub fn encode_request(
    profile: &DecisionProfile,
    request: &DecisionRequest,
) -> Result<Value, DecisionError> {
    validate_profile(profile)?;
    request.validate(CAPABILITIES)?;
    let mut questions = serde_json::Map::new();
    for (id, q) in &request.questions {
        let value = match &q.kind {
            QuestionKind::Boolean { yes, no } => {
                json!({"type":"noul", "instructions":q.instructions, "criteria":{"true":yes,"false":no}})
            }
            QuestionKind::Choice { options } => {
                if options.len() > 255 {
                    return Err(DecisionError::Unsupported("TypeSafe choice limit"));
                }
                json!({"type":"choice", "instructions":q.instructions, "criteria":options})
            }
            QuestionKind::Score { levels } => {
                if levels.len() > 10 {
                    return Err(DecisionError::Unsupported("TypeSafe score limit"));
                }
                json!({"type":"score", "instructions":q.instructions, "criteria":levels.iter().map(|l| &l.description).collect::<Vec<_>>()})
            }
        };
        questions.insert(id.0.clone(), value);
    }
    // Keep paths in instructions stable: do not wrap or rename domain state.
    Ok(json!({"model":profile.model,"state":request.state,"questions":questions}))
}

#[derive(Deserialize)]
struct WireReply {
    model: String,
    answers: BTreeMap<QuestionId, WireAnswer>,
    usage: DecisionUsage,
}
#[derive(Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum WireAnswer {
    Noul {
        noul: f64,
    },
    Choice {
        choice: OptionId,
        probabilities: BTreeMap<OptionId, f64>,
        confidence: f64,
    },
    Score {
        score: f64,
        legend: BTreeMap<String, String>,
        probabilities: BTreeMap<String, f64>,
        confidence: f64,
    },
}

pub fn decode_reply(
    profile: &DecisionProfile,
    request: &DecisionRequest,
    bytes: &[u8],
    elapsed_ms: u64,
    attempts: u32,
) -> Result<DecisionReply, DecisionError> {
    // Validate before indexing rubric levels, even if called directly.
    encode_request(profile, request)?;
    let wire: WireReply = serde_json::from_value(strict_json(bytes)?)
        .map_err(|_| DecisionError::InvalidReply("invalid TypeSafe response shape"))?;
    if wire.model != profile.model {
        return Err(DecisionError::InvalidReply("model version mismatch"));
    }
    if wire.answers.len() != request.questions.len()
        || wire.answers.keys().ne(request.questions.keys())
    {
        return Err(DecisionError::InvalidReply("answer coverage mismatch"));
    }
    let mut answers = BTreeMap::new();
    for (id, answer) in wire.answers {
        let value = match (answer, &request.questions[&id].kind) {
            (WireAnswer::Noul { noul }, QuestionKind::Boolean { .. }) => DecisionAnswer::Boolean {
                value: None,
                probability_true: Some(noul),
            },
            (
                WireAnswer::Choice {
                    choice,
                    probabilities,
                    confidence,
                },
                QuestionKind::Choice { .. },
            ) => DecisionAnswer::Choice {
                selected: choice,
                probabilities: Some(probabilities),
                confidence: Some(confidence),
            },
            (
                WireAnswer::Score {
                    score,
                    legend,
                    probabilities,
                    confidence,
                },
                QuestionKind::Score { levels },
            ) => {
                if probabilities.len() != levels.len() || legend.len() != levels.len() {
                    return Err(DecisionError::InvalidReply("score coverage mismatch"));
                }
                let mut mapped = BTreeMap::new();
                for (i, level) in levels.iter().enumerate() {
                    let key = i.to_string();
                    if legend.get(&key) != Some(&level.description) {
                        return Err(DecisionError::InvalidReply("score legend mismatch"));
                    }
                    let p = probabilities.get(&key).ok_or(DecisionError::InvalidReply(
                        "score distribution missing level",
                    ))?;
                    mapped.insert(level.id.clone(), *p);
                }
                DecisionAnswer::Score {
                    position: score,
                    probabilities: Some(mapped),
                    confidence: Some(confidence),
                }
            }
            _ => return Err(DecisionError::InvalidReply("answer type mismatch")),
        };
        answers.insert(id, value);
    }
    let reply = DecisionReply {
        answers,
        receipt: DecisionReceipt {
            provider: profile.identity(),
            request_key: decision_key(profile, request)?,
            question_namespace: request.namespace.clone(),
            evidence: request.evidence.clone(),
            calibration: Calibration::ProviderClaimed,
            confidence_semantics: Some("typesafe/distribution-concentration".into()),
            evaluation_usage: Some(wire.usage),
            evaluation_ms: elapsed_ms,
            origin: DecisionOrigin::Live,
            network_attempts: attempts,
        },
    };
    reply.validate(profile, request)?;
    Ok(reply)
}

/// serde_json's ordinary map decoding silently keeps the last duplicate key.
/// Reject duplicates at every depth before decoding typed provider/cassette data.
pub(crate) fn strict_json(bytes: &[u8]) -> Result<Value, DecisionError> {
    struct Unique(Value);
    impl<'de> Deserialize<'de> for Unique {
        fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
            struct Visitor;
            impl<'de> serde::de::Visitor<'de> for Visitor {
                type Value = Unique;
                fn expecting(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                    f.write_str("JSON without duplicate keys")
                }
                fn visit_bool<E: serde::de::Error>(self, v: bool) -> Result<Unique, E> {
                    Ok(Unique(Value::Bool(v)))
                }
                fn visit_i64<E: serde::de::Error>(self, v: i64) -> Result<Unique, E> {
                    Ok(Unique(v.into()))
                }
                fn visit_u64<E: serde::de::Error>(self, v: u64) -> Result<Unique, E> {
                    Ok(Unique(v.into()))
                }
                fn visit_f64<E: serde::de::Error>(self, v: f64) -> Result<Unique, E> {
                    serde_json::Number::from_f64(v)
                        .map(|n| Unique(Value::Number(n)))
                        .ok_or_else(|| E::custom("nonfinite"))
                }
                fn visit_str<E: serde::de::Error>(self, v: &str) -> Result<Unique, E> {
                    Ok(Unique(v.into()))
                }
                fn visit_string<E: serde::de::Error>(self, v: String) -> Result<Unique, E> {
                    Ok(Unique(v.into()))
                }
                fn visit_unit<E: serde::de::Error>(self) -> Result<Unique, E> {
                    Ok(Unique(Value::Null))
                }
                fn visit_seq<A: serde::de::SeqAccess<'de>>(
                    self,
                    mut a: A,
                ) -> Result<Unique, A::Error> {
                    let mut v = vec![];
                    while let Some(Unique(x)) = a.next_element()? {
                        v.push(x);
                    }
                    Ok(Unique(Value::Array(v)))
                }
                fn visit_map<A: serde::de::MapAccess<'de>>(
                    self,
                    mut a: A,
                ) -> Result<Unique, A::Error> {
                    let mut m = serde_json::Map::new();
                    while let Some((k, Unique(v))) = a.next_entry::<String, Unique>()? {
                        if m.insert(k, v).is_some() {
                            return Err(serde::de::Error::custom("duplicate key"));
                        }
                    }
                    Ok(Unique(Value::Object(m)))
                }
            }
            d.deserialize_any(Visitor)
        }
    }
    serde_json::from_slice::<Unique>(bytes)
        .map(|v| v.0)
        .map_err(|_| DecisionError::InvalidReply("invalid or duplicate JSON"))
}
