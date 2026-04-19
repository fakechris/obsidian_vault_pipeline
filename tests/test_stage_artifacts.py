import json
from pathlib import Path


def test_hash_file_set_is_stable_across_input_order(tmp_path):
    from ovp_pipeline.stage_artifacts import hash_file_set

    vault = tmp_path / "vault"
    alpha = vault / "20-Areas" / "alpha.md"
    beta = vault / "20-Areas" / "nested" / "beta.md"
    beta.parent.mkdir(parents=True)
    alpha.parent.mkdir(parents=True, exist_ok=True)
    alpha.write_text("alpha\n", encoding="utf-8")
    beta.write_text("beta\n", encoding="utf-8")

    first = hash_file_set(vault, [beta, alpha])
    second = hash_file_set(vault, [alpha, beta])

    assert first == second


def test_stage_artifact_store_writes_and_loads_manifest_by_fingerprint(tmp_path):
    from ovp_pipeline.stage_artifacts import StageArtifactStore

    store = StageArtifactStore(tmp_path / "stage-artifacts")
    manifest = store.write_completed(
        stage="quality",
        fingerprint="quality-demo",
        input_digest="input-demo",
        algorithm_digest="algorithm-demo",
        run_id="run-1",
        pack_name="research-tech",
        workflow_profile="full",
        inputs={"files": ["a.md"]},
        outputs={"qualified_files": ["a.md"]},
        metrics={"quality_checked": 1},
    )

    loaded = store.load("quality", "quality-demo")

    assert manifest["status"] == "completed"
    assert loaded is not None
    assert loaded["fingerprint"] == "quality-demo"
    assert loaded["outputs"]["qualified_files"] == ["a.md"]
    assert json.loads((tmp_path / "stage-artifacts" / "quality" / "quality-demo.json").read_text(encoding="utf-8"))["run_id"] == "run-1"


def test_stage_artifact_store_rejects_manifest_with_missing_declared_outputs(tmp_path):
    from ovp_pipeline.stage_artifacts import StageArtifactStore

    vault = tmp_path / "vault"
    vault.mkdir()
    store = StageArtifactStore(vault / "60-Logs" / "stage-artifacts")
    store.write_completed(
        stage="knowledge_index",
        fingerprint="knowledge-demo",
        input_digest="input-demo",
        algorithm_digest="algorithm-demo",
        run_id="run-1",
        pack_name="research-tech",
        workflow_profile="full",
        inputs={"files": []},
        outputs={"paths": ["60-Logs/knowledge.db"]},
    )

    assert store.load("knowledge_index", "knowledge-demo", validate_outputs_under=vault) is None


def test_stage_artifact_store_treats_output_validation_io_errors_as_cache_miss(tmp_path, monkeypatch):
    from ovp_pipeline.stage_artifacts import StageArtifactStore

    vault = tmp_path / "vault"
    vault.mkdir()
    store = StageArtifactStore(vault / "60-Logs" / "stage-artifacts")
    store.write_completed(
        stage="knowledge_index",
        fingerprint="knowledge-demo",
        input_digest="input-demo",
        algorithm_digest="algorithm-demo",
        run_id="run-1",
        pack_name="research-tech",
        workflow_profile="full",
        inputs={"files": []},
        outputs={"paths": ["60-Logs/knowledge.db"]},
    )

    def raise_oserror(_payload, _base_dir):
        raise OSError("permission denied")

    monkeypatch.setattr(store, "_declared_outputs_exist", raise_oserror)

    assert store.load("knowledge_index", "knowledge-demo", validate_outputs_under=vault) is None
