use std::path::Path;

use ovp_domain::reader::Card;
use ovp_domain::units::Unit;
use ovp_intake::vaultops::rel_to;
use serde::{Deserialize, Serialize};

use crate::model::IndexModel;

pub const EVIDENCE_SCHEMA: &str = "ovp.index.evidence/v1";
const EVIDENCE_FILE: &str = ".ovp/index/evidence.json";

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EvidenceModel {
    pub schema: String,
    pub date: String,
    pub cards: Vec<CardEvidenceRow>,
    pub units: Vec<UnitEvidenceRow>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub warnings: Vec<EvidenceWarning>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CardEvidenceRow {
    pub id: String,
    pub pack_dir: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_sha256: Option<String>,
    pub source_title: String,
    pub title: String,
    pub content: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub unit_type: Option<String>,
    #[serde(default)]
    pub cited_unit_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UnitEvidenceRow {
    pub id: String,
    pub pack_dir: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_sha256: Option<String>,
    pub source_title: String,
    pub unit_id: String,
    pub text: String,
    pub quote: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub line: Option<usize>,
    pub attribution: String,
    pub modality: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EvidenceWarning {
    pub pack_dir: String,
    pub file: String,
    pub reason: String,
}

pub fn build_evidence(
    vault_root: &Path,
    date: &str,
    index: &IndexModel,
) -> Result<EvidenceModel, String> {
    let mut cards = Vec::new();
    let mut units = Vec::new();
    let mut warnings = Vec::new();

    for pack in &index.packs {
        let pack_path = vault_root.join(&pack.pack_dir);
        let cards_path = pack_path.join("cards.json");
        match read_json::<Vec<Card>>(&cards_path) {
            Ok(pack_cards) => {
                for (idx, card) in pack_cards.into_iter().enumerate() {
                    cards.push(CardEvidenceRow {
                        id: format!("card:{}:{idx}", pack.pack_dir),
                        pack_dir: pack.pack_dir.clone(),
                        source_sha256: pack.source_sha256.clone(),
                        source_title: pack.title.clone(),
                        title: card.title,
                        content: card.content,
                        unit_type: card.unit_type,
                        cited_unit_ids: card.cited_unit_ids,
                    });
                }
            }
            Err(reason) => warnings.push(EvidenceWarning {
                pack_dir: pack.pack_dir.clone(),
                file: "cards.json".into(),
                reason,
            }),
        }

        let units_path = pack_path.join("units.accepted.json");
        match read_json::<Vec<Unit>>(&units_path) {
            Ok(pack_units) => {
                for unit in pack_units {
                    units.push(UnitEvidenceRow {
                        id: format!("unit:{}:{}", pack.pack_dir, unit.id),
                        pack_dir: pack.pack_dir.clone(),
                        source_sha256: pack.source_sha256.clone(),
                        source_title: pack.title.clone(),
                        unit_id: unit.id,
                        text: unit.text,
                        quote: unit.evidence.quote,
                        line: unit.evidence.location.map(|loc| loc.line),
                        attribution: enum_str(&unit.attribution),
                        modality: enum_str(&unit.modality),
                    });
                }
            }
            Err(reason) => warnings.push(EvidenceWarning {
                pack_dir: pack.pack_dir.clone(),
                file: "units.accepted.json".into(),
                reason,
            }),
        }
    }

    cards.sort_by(|a, b| a.id.cmp(&b.id));
    units.sort_by(|a, b| a.id.cmp(&b.id));
    warnings.sort_by(|a, b| {
        (a.pack_dir.as_str(), a.file.as_str()).cmp(&(b.pack_dir.as_str(), b.file.as_str()))
    });

    Ok(EvidenceModel {
        schema: EVIDENCE_SCHEMA.into(),
        date: date.into(),
        cards,
        units,
        warnings,
    })
}

pub fn write_evidence(vault_root: &Path, evidence: &EvidenceModel) -> Result<String, String> {
    let target = vault_root.join(EVIDENCE_FILE);
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("creating {}: {e}", parent.display()))?;
    }
    let body =
        serde_json::to_string_pretty(evidence).map_err(|e| format!("serializing evidence: {e}"))?;
    std::fs::write(&target, format!("{body}\n"))
        .map_err(|e| format!("writing {}: {e}", target.display()))?;
    Ok(rel_to(vault_root, &target))
}

pub fn read_evidence(vault_root: &Path) -> Result<EvidenceModel, String> {
    let path = vault_root.join(EVIDENCE_FILE);
    let raw = std::fs::read_to_string(&path).map_err(|e| {
        format!(
            "reading {}: {e} (run `ovp2 index --vault-root …` to build it)",
            path.display()
        )
    })?;
    serde_json::from_str(&raw).map_err(|e| format!("parsing {}: {e}", path.display()))
}

fn read_json<T: serde::de::DeserializeOwned>(path: &Path) -> Result<T, String> {
    let raw =
        std::fs::read_to_string(path).map_err(|e| format!("reading {}: {e}", path.display()))?;
    serde_json::from_str(&raw).map_err(|e| format!("parsing {}: {e}", path.display()))
}

fn enum_str<T: serde::Serialize>(v: &T) -> String {
    serde_json::to_value(v)
        .ok()
        .and_then(|j| j.as_str().map(String::from))
        .unwrap_or_else(|| "unknown".into())
}

#[cfg(test)]
mod tests {
    use std::fs;

    use crate::evidence::{build_evidence, read_evidence, write_evidence, EVIDENCE_SCHEMA};
    use crate::model::{IndexModel, OpsState, PackRow, Totals};

    fn index_with_pack(pack_dir: &str) -> IndexModel {
        IndexModel {
            schema: "ovp.index/v2".into(),
            date: "2026-07-06".into(),
            run_id: None,
            totals: Totals::default(),
            sources: vec![],
            packs: vec![PackRow {
                pack_dir: pack_dir.into(),
                title: "Agent Memory Systems".into(),
                date: Some("2026-07-06".into()),
                units: 1,
                cards: 1,
                json_repaired: false,
                card_titles: vec!["Memory as state".into()],
                source_sha256: Some("sha-a".into()),
            }],
            claims: vec![],
            runs: vec![],
            ops: OpsState::default(),
        }
    }

    fn write_pack(vault: &std::path::Path, pack_dir: &str) {
        let dir = vault.join(pack_dir);
        fs::create_dir_all(&dir).unwrap();
        fs::write(
            dir.join("cards.json"),
            r#"[
              {
                "title": "Memory as state",
                "content": "Agent memory should be treated as persistent state, not a transient prompt trick.",
                "unit_type": "claim",
                "cited_unit_ids": ["u-001-abcd"]
              }
            ]"#,
        )
        .unwrap();
        fs::write(
            dir.join("units.accepted.json"),
            r#"[
              {
                "id": "u-001-abcd",
                "kind": "assertion",
                "subtype": "claim",
                "text": "Agent memory is persistent state.",
                "evidence": {
                  "ref_id": "p001.s001",
                  "quote": "Agent memory should be treated as persistent state.",
                  "location": {
                    "byte_start": 0,
                    "byte_end": 49,
                    "line": 12,
                    "match_kind": "exact"
                  }
                },
                "attribution": "author",
                "modality": "asserted",
                "arguments": [],
                "status": "accepted",
                "issues": []
              }
            ]"#,
        )
        .unwrap();
    }

    #[test]
    fn builds_cards_and_units_from_reader_pack_files() {
        let tmp = tempfile::tempdir().unwrap();
        let pack_dir = "40-Resources/Reader/agent-memory";
        write_pack(tmp.path(), pack_dir);

        let evidence =
            build_evidence(tmp.path(), "2026-07-06", &index_with_pack(pack_dir)).unwrap();

        assert_eq!(evidence.schema, EVIDENCE_SCHEMA);
        assert_eq!(evidence.cards.len(), 1);
        assert_eq!(
            evidence.cards[0].id,
            "card:40-Resources/Reader/agent-memory:0"
        );
        assert_eq!(evidence.cards[0].source_title, "Agent Memory Systems");
        assert_eq!(
            evidence.cards[0].content,
            "Agent memory should be treated as persistent state, not a transient prompt trick."
        );
        assert_eq!(evidence.cards[0].cited_unit_ids, vec!["u-001-abcd"]);

        assert_eq!(evidence.units.len(), 1);
        assert_eq!(
            evidence.units[0].id,
            "unit:40-Resources/Reader/agent-memory:u-001-abcd"
        );
        assert_eq!(evidence.units[0].unit_id, "u-001-abcd");
        assert_eq!(
            evidence.units[0].quote,
            "Agent memory should be treated as persistent state."
        );
        assert_eq!(evidence.units[0].line, Some(12));
    }

    #[test]
    fn evidence_sidecar_round_trips_as_json_projection() {
        let tmp = tempfile::tempdir().unwrap();
        let pack_dir = "40-Resources/Reader/agent-memory";
        write_pack(tmp.path(), pack_dir);
        let evidence =
            build_evidence(tmp.path(), "2026-07-06", &index_with_pack(pack_dir)).unwrap();

        let rel = write_evidence(tmp.path(), &evidence).unwrap();
        let read_back = read_evidence(tmp.path()).unwrap();

        assert_eq!(rel, ".ovp/index/evidence.json");
        assert_eq!(read_back.schema, EVIDENCE_SCHEMA);
        assert_eq!(read_back.cards[0].title, "Memory as state");
        assert_eq!(read_back.units[0].text, "Agent memory is persistent state.");
    }
}
