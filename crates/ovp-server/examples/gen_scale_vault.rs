//! Synthetic scale vault generator — a committed replacement for the ad-hoc
//! generator used in the M32 10k-claim scale test (commit b2b06928).
//!
//! Writes a `.ovp/crystal/ledger.jsonl` with N claims over a power-law
//! shared-source distribution and ~40 themes, so `/api/graph` performance and
//! overview truncation can be regression-tested against a known shape.
//!
//! Usage:
//!   cargo run -p ovp-server --example gen_scale_vault -- \
//!       --claims 10000 --out /tmp/ovp-scale-vault

use std::io::Write;
use std::path::PathBuf;

use ovp_domain::VaultLayout;
use ovp_domain::crystal::{
    CrystalStatus, DurableCitation, DurableRecord, FinalClass, ProvenanceClass, StoreEvent,
    StoreOp, StrengthClass,
};

/// Deterministic LCG so runs are reproducible (no `rand` dependency).
struct Lcg(u64);

impl Lcg {
    fn next_f64(&mut self) -> f64 {
        self.0 = self
            .0
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        ((self.0 >> 11) as f64) / ((1u64 << 53) as f64)
    }

    fn next_usize(&mut self, bound: usize) -> usize {
        (self.next_f64() * bound as f64) as usize % bound.max(1)
    }
}

fn main() {
    let mut claims = 10_000usize;
    let mut out = PathBuf::from("/tmp/ovp-scale-vault");

    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--claims" => {
                claims = args
                    .get(i + 1)
                    .and_then(|v| v.parse().ok())
                    .expect("--claims needs a number");
                i += 2;
            }
            "--out" => {
                out = PathBuf::from(args.get(i + 1).expect("--out needs a path"));
                i += 2;
            }
            other => panic!("unknown arg: {other}"),
        }
    }

    let layout = VaultLayout::new();
    let store_dir = out.join(layout.crystal_store_dir());
    std::fs::create_dir_all(&store_dir).expect("create store dir");
    let ledger_path = store_dir.join("ledger.jsonl");
    let mut file = std::fs::File::create(&ledger_path).expect("create ledger");

    let n_sources = (claims / 12).max(4);
    let n_themes = 40usize;
    let mut rng = Lcg(0x5eed_cafe_2026_0630);

    for i in 0..claims {
        // Power-law source pick: cubing the uniform skews toward low indices,
        // so a few "hub" sources are cited by many claims (real corpora look
        // like this and it exercises the community + related-edge paths).
        let n_cites = 1 + rng.next_usize(3);
        let mut citations = Vec::with_capacity(n_cites);
        let mut primary_source = 0usize;
        for c in 0..n_cites {
            let u = rng.next_f64();
            let src = ((u * u * u) * n_sources as f64) as usize % n_sources;
            if c == 0 {
                primary_source = src;
            }
            citations.push(DurableCitation {
                case_id: format!("case-{src:05}"),
                unit_id: format!("u-{i}-{c}"),
                quote: format!("synthetic quote {i}-{c} for scale testing"),
                resolved_line: Some(10 + c),
            });
        }

        let theme = format!("theme-{:02}", primary_source % n_themes);
        let strength = match rng.next_usize(10) {
            0 => StrengthClass::Overreach,
            1 => StrengthClass::OverSynthesized,
            2 => StrengthClass::OpinionAsFact,
            _ => StrengthClass::Supported,
        };
        let provenance_score = 0.5 + rng.next_f64() * 0.5;

        let record = DurableRecord {
            claim_key: format!("ck-scale-{i:06}"),
            claim_id: format!("scale-{i:06}"),
            claim: format!(
                "Synthetic scale claim {i}: sources in bucket {primary_source} \
                 support pattern {theme}."
            ),
            theme,
            source_cases: citations.iter().map(|c| c.case_id.clone()).collect(),
            citations,
            provenance_score,
            provenance_class: ProvenanceClass::Durable,
            strength,
            strength_rationale: "synthetic".into(),
            final_class: FinalClass::Durable,
            run_id: "scale-fixture".into(),
            status: CrystalStatus::Active,
        };

        let event = StoreEvent {
            op: StoreOp::Write,
            record,
            supersedes: None,
            reason: None,
        };
        let line = serde_json::to_string(&event).expect("serialize event");
        writeln!(file, "{line}").expect("write ledger line");
    }

    eprintln!(
        "wrote {claims} claims over {n_sources} sources to {}",
        ledger_path.display()
    );
    eprintln!("serve with: ovp2 serve --vault-root {}", out.display());
}
