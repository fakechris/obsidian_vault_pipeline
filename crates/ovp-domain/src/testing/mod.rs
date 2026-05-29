//! Test-only utilities. Gated behind the `testing` feature flag so
//! production builds don't carry assertion engines.

pub mod contract;

pub use contract::{
    assert_contract, assert_contract_paper, assert_contract_subject, load_contract, Clause,
    Contract, ContractError, ContractFields, ContractReport, ExpectedArtifact, FieldValue,
    TerminalState,
};
