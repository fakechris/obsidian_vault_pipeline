"""Content-addressed stage artifact manifests for resumable pipeline stages."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_json_payload(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _relative_path(root: Path, path: Path) -> str:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        return resolved_path.relative_to(resolved_root).as_posix()
    except ValueError:
        return os.fspath(resolved_path)


def hash_file_set(root: Path, files: list[Path] | tuple[Path, ...]) -> str:
    records: list[dict[str, Any]] = []
    for path in sorted((Path(item) for item in files), key=lambda item: _relative_path(root, item)):
        stat = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        records.append(
            {
                "path": _relative_path(root, path),
                "sha256": digest,
                "size": stat.st_size,
            }
        )
    return hash_json_payload(records)


def build_stage_fingerprint(
    *,
    stage: str,
    input_digest: str,
    algorithm_digest: str,
    pack_name: str,
    workflow_profile: str,
) -> str:
    return hash_json_payload(
        {
            "stage": stage,
            "input_digest": input_digest,
            "algorithm_digest": algorithm_digest,
            "pack_name": pack_name,
            "workflow_profile": workflow_profile,
        }
    )


class StageArtifactStore:
    def __init__(self, root_dir: Path):
        self.root_dir = Path(root_dir)

    def path_for(self, stage: str, fingerprint: str) -> Path:
        return self.root_dir / stage / f"{fingerprint}.json"

    def load(self, stage: str, fingerprint: str) -> dict[str, Any] | None:
        path = self.path_for(stage, fingerprint)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("stage") != stage or payload.get("fingerprint") != fingerprint:
            return None
        if payload.get("status") != "completed":
            return None
        return payload

    def write_completed(
        self,
        *,
        stage: str,
        fingerprint: str,
        input_digest: str,
        algorithm_digest: str,
        run_id: str | None,
        pack_name: str,
        workflow_profile: str,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        manifest = {
            "stage": stage,
            "fingerprint": fingerprint,
            "input_digest": input_digest,
            "algorithm_digest": algorithm_digest,
            "status": "completed",
            "run_id": run_id or "",
            "pack_name": pack_name,
            "workflow_profile": workflow_profile,
            "created_at": _utc_now(),
            "inputs": inputs,
            "outputs": outputs,
            "metrics": metrics or {},
        }
        path = self.path_for(stage, fingerprint)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(path)
        return manifest
