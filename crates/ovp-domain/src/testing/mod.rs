//! Test-only utilities. Gated behind the `testing` feature flag so
//! production builds don't carry assertion engines.

pub mod contract;

pub use contract::{
    assert_contract, load_contract, Clause, Contract, ContractError, ContractReport,
    ExpectedArtifact, TerminalState,
};
