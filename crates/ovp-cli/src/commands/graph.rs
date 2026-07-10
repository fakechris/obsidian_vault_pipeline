use std::path::PathBuf;

use ovp_core::PipelineManifest;

use crate::CliError;

pub fn run(manifest_path: PathBuf) -> Result<(), CliError> {
    let toml_str = std::fs::read_to_string(&manifest_path).map_err(|e| {
        CliError::Io(format!("reading manifest `{}`: {e}", manifest_path.display()))
    })?;
    let manifest = PipelineManifest::parse(&toml_str).map_err(|e| CliError::Core(e.into()))?;
    let topo = manifest.topo_order().map_err(|e| CliError::Core(e.into()))?;

    println!("nodes ({}):", manifest.nodes().len());
    for n in manifest.nodes() {
        println!("  - {n}");
    }
    println!();
    println!("edges ({}):", manifest.edges().len());
    for [from, to] in manifest.edges() {
        println!("  {from} -> {to}");
    }
    println!();
    println!("topological order:");
    for (i, n) in topo.iter().enumerate() {
        println!("  {i:>2}. {n}");
    }
    Ok(())
}
