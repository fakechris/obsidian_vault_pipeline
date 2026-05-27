use std::path::PathBuf;

use ovp_core::fakes::{DropZeroes, FakeBody, FakeSource, VaultPlanSink};
use ovp_core::{GraphRunner, PipelineManifest, RunId};

use crate::CliError;

pub fn run(manifest_path: PathBuf, run_id_str: String, out_dir: PathBuf) -> Result<(), CliError> {
    let toml_str = std::fs::read_to_string(&manifest_path).map_err(|e| {
        CliError::Io(format!("reading manifest `{}`: {e}", manifest_path.display()))
    })?;
    let manifest = PipelineManifest::parse(&toml_str).map_err(|e| CliError::Core(e.into()))?;
    let run_id = RunId::new(run_id_str);

    // v0.1 wiring: registers the in-tree fakes against the three node names
    // declared in `manifests/fake.pipeline.toml`. Real wiring (post-v0.1)
    // will be driven by the manifest itself + a node-kind registry.
    let mut runner: GraphRunner<FakeBody> = GraphRunner::new(manifest, run_id.clone());
    runner.register_source("fake_source", FakeSource::new("fake_source", run_id.clone()));
    runner.register_transform("fake_transform", DropZeroes::new("fake_transform"));
    runner.register_sink("fake_sink", VaultPlanSink::new("fake_sink"));

    let report = runner.run().map_err(CliError::Core)?;

    let plans_dir = out_dir.join("plans");
    let events_dir = out_dir.join("events");
    std::fs::create_dir_all(&plans_dir)
        .map_err(|e| CliError::Io(format!("creating {}: {e}", plans_dir.display())))?;
    std::fs::create_dir_all(&events_dir)
        .map_err(|e| CliError::Io(format!("creating {}: {e}", events_dir.display())))?;

    let plan_path = plans_dir.join(format!("{}.json", report.run_id.as_str()));
    let events_path = events_dir.join(format!("{}.jsonl", report.run_id.as_str()));

    let plan_json = serde_json::to_string_pretty(&report.write_plan)
        .map_err(|e| CliError::Io(format!("serializing write plan: {e}")))?;
    std::fs::write(&plan_path, plan_json)
        .map_err(|e| CliError::Io(format!("writing {}: {e}", plan_path.display())))?;

    let mut events_jsonl = String::new();
    for ev in &report.events {
        let line = serde_json::to_string(ev)
            .map_err(|e| CliError::Io(format!("serializing event: {e}")))?;
        events_jsonl.push_str(&line);
        events_jsonl.push('\n');
    }
    std::fs::write(&events_path, events_jsonl)
        .map_err(|e| CliError::Io(format!("writing {}: {e}", events_path.display())))?;

    println!("run_id:                {}", report.run_id.as_str());
    println!("records_seen:          {}", report.records_seen);
    println!("records_forwarded:     {}", report.records_forwarded_to_sinks);
    println!("records_dropped:       {}", report.records_dropped);
    println!("write_plan ops:        {}", report.write_plan.len());
    println!("events:                {}", report.events.len());
    println!();
    println!("wrote {}", plan_path.display());
    println!("wrote {}", events_path.display());

    Ok(())
}
