# BL-112: leaf-extracted from unified_pipeline_enhanced.py — verbatim move, no logic change.
import json
import os
import tempfile
from datetime import datetime
from ovp_pipeline.txn import build_transaction_payload, heartbeat_transaction, mark_transaction_completed, mark_transaction_failed, update_transaction_step
from pathlib import Path
from typing import Any




class PipelineLogger:
    """统一过程日志记录器"""

    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.session_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()}"

    def log(self, event_type: str, data: dict[str, Any]):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            "event_type": event_type,
            **data
        }
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")



class TransactionManager:
    """事务管理器"""

    def __init__(self, txn_dir: Path):
        self.txn_dir = txn_dir

    def _txn_file(self, txn_id: str) -> Path:
        return self.txn_dir / f"{txn_id}.json"

    def _read(self, txn_id: str) -> dict[str, Any] | None:
        txn_file = self._txn_file(txn_id)
        if not txn_file.exists():
            return None
        with open(txn_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, txn_id: str, txn_data: dict[str, Any]) -> None:
        txn_file = self._txn_file(txn_id)
        txn_file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=txn_file.parent,
            prefix=f".{txn_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            tmp_file = Path(f.name)
            try:
                json.dump(txn_data, f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            except Exception:
                try:
                    tmp_file.unlink()
                except OSError:
                    pass
                raise
        try:
            os.replace(tmp_file, txn_file)
        finally:
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except OSError:
                    pass

    def start(
        self,
        workflow_type: str,
        description: str,
        *,
        pack_name: str | None = None,
        workflow_profile: str | None = None,
        planned_steps: list[str] | None = None,
    ) -> str:
        txn_id = f"pipeline-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()[:8]}"
        txn_data = build_transaction_payload(
            txn_id,
            workflow_type,
            description,
            pack_name=pack_name,
            workflow_profile=workflow_profile,
            planned_steps=planned_steps,
        )
        self._write(txn_id, txn_data)
        return txn_id

    def step(self, txn_id: str, step_name: str, status: str, output: str = "", **progress_kwargs: Any):
        txn_data = self._read(txn_id)
        if txn_data is None:
            return
        update_transaction_step(txn_data, step_name, status, output=output, **progress_kwargs)
        self._write(txn_id, txn_data)

    def heartbeat(self, txn_id: str, *, step_name: str | None = None, **kwargs: Any):
        txn_data = self._read(txn_id)
        if txn_data is None:
            return
        heartbeat_transaction(txn_data, step_name=step_name, **kwargs)
        self._write(txn_id, txn_data)

    def complete(self, txn_id: str):
        txn_data = self._read(txn_id)
        if txn_data is None:
            return
        mark_transaction_completed(txn_data)
        self._write(txn_id, txn_data)

    def fail(self, txn_id: str, reason: str):
        txn_data = self._read(txn_id)
        if txn_data is None:
            return
        mark_transaction_failed(txn_data, reason)
        self._write(txn_id, txn_data)


__all__ = [
    'PipelineLogger',
    'TransactionManager'
]
