from __future__ import annotations

from pathlib import Path


def _make_pipeline(tmp_path: Path):
    from openclaw_pipeline.unified_pipeline_enhanced import EnhancedPipeline, PipelineLogger, TransactionManager

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)
    logger = PipelineLogger(vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(vault, logger, txn)
    pipeline.txn_id = txn.start("test-pipeline", "pack runtime e2e")
    return vault, pipeline


def _bind_success_step(pipeline, step_name: str, calls: list[str], **payload):
    def _step(*args, **kwargs):
        calls.append(step_name)
        result = {"success": True}
        result.update(payload)
        return result

    setattr(pipeline, f"step_{step_name}", _step)


def test_research_tech_full_profile_runtime_e2e(tmp_path):
    from openclaw_pipeline.packs.loader import load_pack

    _vault, pipeline = _make_pipeline(tmp_path)
    calls: list[str] = []
    captured_absorb: dict[str, object] = {}

    _bind_success_step(pipeline, "pinboard", calls)
    _bind_success_step(pipeline, "pinboard_process", calls)
    _bind_success_step(pipeline, "clippings", calls)
    _bind_success_step(pipeline, "articles", calls)
    _bind_success_step(
        pipeline,
        "quality",
        calls,
        quality_score=4.25,
        quality_checked=3,
        quality_qualified=2,
        quality_qualified_files=["/tmp/qualified-a.md", "/tmp/qualified-b.md"],
    )
    _bind_success_step(pipeline, "fix_links", calls)

    def fake_absorb(recent_days=7, dry_run=False, quality_score=-1.0, qualified_files=None, batch_size=None):
        calls.append("absorb")
        captured_absorb["recent_days"] = recent_days
        captured_absorb["dry_run"] = dry_run
        captured_absorb["quality_score"] = quality_score
        captured_absorb["qualified_files"] = list(qualified_files or [])
        captured_absorb["batch_size"] = batch_size
        return {"success": True, "produced": 2}

    pipeline.step_absorb = fake_absorb
    _bind_success_step(pipeline, "registry_sync", calls)
    _bind_success_step(pipeline, "moc", calls)
    _bind_success_step(pipeline, "knowledge_index", calls)

    steps = load_pack("research-tech").profile("full").stages
    results = pipeline.run_pipeline(steps=steps, batch_size=25, dry_run=False)

    assert list(results) == steps
    assert calls == steps
    assert all(result["success"] is True for result in results.values())
    assert captured_absorb == {
        "recent_days": 7,
        "dry_run": False,
        "quality_score": 4.25,
        "qualified_files": ["/tmp/qualified-a.md", "/tmp/qualified-b.md"],
        "batch_size": 25,
    }


def test_default_knowledge_compatibility_runtime_from_step_e2e(tmp_path):
    from openclaw_pipeline.packs.loader import load_pack

    _vault, pipeline = _make_pipeline(tmp_path)
    calls: list[str] = []
    captured_absorb: dict[str, object] = {}

    _bind_success_step(
        pipeline,
        "quality",
        calls,
        quality_score=3.5,
        quality_checked=2,
        quality_qualified=1,
        quality_qualified_files=["/tmp/compat-qualified.md"],
    )
    _bind_success_step(pipeline, "fix_links", calls)

    def fake_absorb(recent_days=7, dry_run=False, quality_score=-1.0, qualified_files=None, batch_size=None):
        calls.append("absorb")
        captured_absorb["quality_score"] = quality_score
        captured_absorb["qualified_files"] = list(qualified_files or [])
        return {"success": True, "produced": 1}

    pipeline.step_absorb = fake_absorb
    _bind_success_step(pipeline, "registry_sync", calls)
    _bind_success_step(pipeline, "moc", calls)
    _bind_success_step(pipeline, "knowledge_index", calls)

    full_steps = load_pack("default-knowledge").profile("full").stages
    sliced_steps = full_steps[full_steps.index("quality") :]
    results = pipeline.run_pipeline(steps=sliced_steps, batch_size=10, dry_run=False)

    assert list(results) == sliced_steps
    assert calls == sliced_steps
    assert captured_absorb["quality_score"] == 3.5
    assert captured_absorb["qualified_files"] == ["/tmp/compat-qualified.md"]
