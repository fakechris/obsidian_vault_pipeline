//! OVP Next application / assembly layer.
//!
//! Turns a **declarative** pipeline manifest (`node id` + `kind` + `config` +
//! `edges`) plus app-supplied **wiring** (the live effect objects + per-run
//! values) into a ready-to-run `GraphRunner<DomainBody>` — so the CLI and tests
//! stop hand-coding `register_*` calls.
//!
//! DirectShow-like in spirit, NOT a plugin system: the node set is compiled in
//! (`NodeRegistry::with_domain_nodes`), there is no dynamic loading and no ABI.
//!
//! Layering: `ovp-cli → ovp-app → {ovp-domain, ovp-llm, ovp-core}`. `ovp-core`
//! stays domain-blind and knows no concrete `NodeKind`; the node catalog lives
//! entirely here.
//!
//! See `docs/stage-graph-assembly.md`.

pub mod assembler;
pub mod error;
pub mod node_kind;
pub mod registry;
pub mod spec;
pub mod wiring;

pub use assembler::GraphAssembler;
pub use error::AssemblyError;
pub use node_kind::{kinds, NodeCategory, NodeKind};
pub use registry::NodeRegistry;
pub use spec::{DomainPipelineSpec, NodeConfig};
pub use wiring::AppWiring;
