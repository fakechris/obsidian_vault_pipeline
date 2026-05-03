"""Per-step pipeline metrics — Guardrail 3.

Each pipeline run appends one structured line per step to
``60-Logs/pipeline-metrics.jsonl``::

    {
      "run_id": "run-2026-05-02-21-37-30",
      "ts": "2026-05-02T21:37:30+00:00",
      "step": "entity_extract",
      "success": true,
      "produced": 30,
      "duration_s": 612.4,
      "via": "llm",                    // or "alias_only" / "skipped"
      "anomalies": []                  // populated by doctor on read
    }

Read back by ``ovp-doctor`` (existing CLI) for anomaly checks like:

  * step ran via=alias_only when the vault has an LLM API key configured
    → the symptom that hid the missing-llm_client.py bug for months
  * files_skipped_already_extracted dropped to zero on a rerun
    → the symptom that ate token budget when dedup wasn't checking
  * produced count fell by >50% vs the rolling median
    → generic regression detector

This module is tiny and deliberately decoupled from EnhancedPipeline:
``MetricsRecorder`` can be wired in by step methods (or by the
dispatcher boundary alongside StepResult coercion).
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class StepMetric:
    run_id: str
    ts: str
    step: str
    success: bool
    duration_s: float
    produced: int = 0
    via: str = "unknown"
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MetricsRecorder:
    """Writes one ``StepMetric`` line per step to the metrics log."""

    def __init__(self, log_path: Path, run_id: str | None = None) -> None:
        self.log_path = log_path
        self.run_id = run_id or _default_run_id()
        self._step_starts: dict[str, float] = {}

    def begin(self, step: str) -> None:
        self._step_starts[step] = time.time()

    def end(
        self,
        step: str,
        *,
        success: bool,
        produced: int = 0,
        via: str = "unknown",
        **extras: Any,
    ) -> StepMetric:
        start = self._step_starts.pop(step, time.time())
        metric = StepMetric(
            run_id=self.run_id,
            ts=datetime.now(timezone.utc).isoformat(),
            step=step,
            success=success,
            duration_s=round(time.time() - start, 2),
            produced=produced,
            via=via,
            extras=extras,
        )
        self._append(metric)
        return metric

    def _append(self, metric: StepMetric) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(metric.to_dict(), ensure_ascii=False) + "\n")


def _default_run_id() -> str:
    return "run-" + datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M-%S")


# ---------------------------------------------------------------------------
# Doctor anomaly detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Anomaly:
    severity: str  # "WARN" | "ERROR"
    step: str
    rule: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_metrics(log_path: Path) -> list[StepMetric]:
    if not log_path.exists():
        return []
    out: list[StepMetric] = []
    with log_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            extras = d.get("extras") or {}
            out.append(StepMetric(
                run_id=d.get("run_id", ""),
                ts=d.get("ts", ""),
                step=d.get("step", ""),
                success=bool(d.get("success", False)),
                duration_s=float(d.get("duration_s", 0.0) or 0.0),
                produced=int(d.get("produced", 0) or 0),
                via=d.get("via", "unknown"),
                extras=extras if isinstance(extras, dict) else {},
            ))
    return out


def detect_anomalies(
    metrics: list[StepMetric],
    *,
    has_llm_credentials: bool,
) -> list[Anomaly]:
    """Run a small set of cheap anomaly checks against recent metrics.

    Designed to be called by ``ovp-doctor``.  ``has_llm_credentials``
    tells the checker whether the vault has an API key configured —
    needed so we don't false-positive on legitimately offline vaults.
    """
    out: list[Anomaly] = []

    # 1) entity_extract via=alias_only despite credentials available.
    #    This is the missing-llm_client.py symptom.
    if has_llm_credentials:
        for m in metrics:
            if m.step == "entity_extract" and m.via == "alias_only":
                out.append(Anomaly(
                    severity="ERROR",
                    step=m.step,
                    rule="llm_silently_disabled",
                    message=(
                        f"{m.step} ran via=alias_only at {m.ts} despite an "
                        "LLM API key being configured.  Likely a silent "
                        "ImportError or LLM client construction failure."
                    ),
                ))

    # 2) Per-step "produced" anomaly: latest run differs from rolling
    #    median by >50%.  Skip if we have <3 historical samples.
    by_step: dict[str, list[StepMetric]] = {}
    for m in metrics:
        by_step.setdefault(m.step, []).append(m)

    for step, runs in by_step.items():
        # Need ≥3 historical points (excluding the latest) for the
        # median to be statistically meaningful — so total runs ≥ 4.
        if len(runs) < 4:
            continue
        produced = [r.produced for r in runs[:-1]]
        median = statistics.median(produced)  # true median (averages the
                                              # middle two for even counts)
        latest = runs[-1].produced
        if median == 0:
            continue
        delta_ratio = abs(latest - median) / max(median, 1)
        if delta_ratio > 0.5:
            out.append(Anomaly(
                severity="WARN",
                step=step,
                rule="produced_drift",
                message=(
                    f"{step} produced={latest} on the latest run, "
                    f"vs historical median {median} (Δ={delta_ratio*100:.0f}%)"
                ),
            ))

    return out
