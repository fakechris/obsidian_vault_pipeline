"""Tests for pipeline_metrics — Guardrail 3."""

from __future__ import annotations

import json
from pathlib import Path

from ovp_pipeline.pipeline_metrics import (
    Anomaly,
    MetricsRecorder,
    StepMetric,
    detect_anomalies,
    read_metrics,
)


class TestRecorder:
    def test_appends_one_line_per_step(self, tmp_path):
        log = tmp_path / "metrics.jsonl"
        rec = MetricsRecorder(log, run_id="run-1")

        rec.begin("absorb")
        rec.end("absorb", success=True, produced=10, via="llm")
        rec.begin("entity_extract")
        rec.end("entity_extract", success=True, produced=30, via="llm")

        lines = log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        a = json.loads(lines[0])
        b = json.loads(lines[1])
        assert a["step"] == "absorb" and a["produced"] == 10
        assert b["step"] == "entity_extract" and b["produced"] == 30
        assert a["run_id"] == b["run_id"] == "run-1"

    def test_extras_passed_through(self, tmp_path):
        log = tmp_path / "metrics.jsonl"
        rec = MetricsRecorder(log, run_id="run-2")
        rec.begin("absorb")
        rec.end("absorb", success=True, produced=5, via="llm",
                files_skipped_already_extracted=120)

        line = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
        assert line["extras"]["files_skipped_already_extracted"] == 120


class TestAnomalyDetection:

    def _metric(self, step="entity_extract", via="llm", produced=30, ts="2026-05-02T21:00:00+00:00"):
        return StepMetric(
            run_id="r", ts=ts, step=step, success=True,
            duration_s=10.0, produced=produced, via=via,
        )

    def test_alias_only_with_credentials_fires(self):
        ms = [self._metric(via="alias_only")]
        anomalies = detect_anomalies(ms, has_llm_credentials=True)
        assert any(a.rule == "llm_silently_disabled" for a in anomalies)

    def test_alias_only_without_credentials_silent(self):
        ms = [self._metric(via="alias_only")]
        anomalies = detect_anomalies(ms, has_llm_credentials=False)
        assert all(a.rule != "llm_silently_disabled" for a in anomalies)

    def test_via_llm_does_not_fire(self):
        ms = [self._metric(via="llm")]
        anomalies = detect_anomalies(ms, has_llm_credentials=True)
        assert all(a.rule != "llm_silently_disabled" for a in anomalies)

    def test_produced_drift_fires_on_50pct_delta(self):
        # 3 historical runs (~30) + latest=1 = 97% drop → fires.
        history = [self._metric(produced=p) for p in (30, 28, 32)]
        latest = self._metric(produced=1)
        anomalies = detect_anomalies(history + [latest], has_llm_credentials=False)
        assert any(a.rule == "produced_drift" and a.step == "entity_extract" for a in anomalies)

    def test_produced_drift_skips_with_too_little_history(self):
        # 1 historical + 1 latest = 2 runs → below the ≥4 threshold.
        ms = [self._metric(produced=30), self._metric(produced=1)]
        anomalies = detect_anomalies(ms, has_llm_credentials=False)
        assert all(a.rule != "produced_drift" for a in anomalies)

    def test_produced_drift_skips_at_exactly_3_runs(self):
        # Boundary: 2 historical + 1 latest = 3 runs → still below ≥4 threshold.
        ms = [self._metric(produced=30), self._metric(produced=32), self._metric(produced=1)]
        anomalies = detect_anomalies(ms, has_llm_credentials=False)
        assert all(a.rule != "produced_drift" for a in anomalies)

    def test_produced_drift_uses_true_median_for_even_history(self):
        # Even-length history: median(30, 32) = 31.  Latest=15 → drop is
        # ~52%, which crosses the 50% threshold → fires.  This guards
        # against the previous "upper-middle element" stand-in for median.
        history = [self._metric(produced=p) for p in (30, 32, 30, 32)]
        latest = self._metric(produced=15)
        anomalies = detect_anomalies(history + [latest], has_llm_credentials=False)
        assert any(a.rule == "produced_drift" for a in anomalies)


class TestReadMetrics:
    def test_round_trip(self, tmp_path):
        log = tmp_path / "m.jsonl"
        rec = MetricsRecorder(log, run_id="r1")
        rec.begin("absorb")
        rec.end("absorb", success=True, produced=42, via="llm")

        loaded = read_metrics(log)
        assert len(loaded) == 1
        assert loaded[0].step == "absorb"
        assert loaded[0].produced == 42

    def test_skips_malformed_lines(self, tmp_path):
        log = tmp_path / "m.jsonl"
        log.write_text("not-json\n{\"step\":\"absorb\",\"success\":true}\n", encoding="utf-8")
        loaded = read_metrics(log)
        assert len(loaded) == 1
        assert loaded[0].step == "absorb"

    def test_empty_log(self, tmp_path):
        log = tmp_path / "m.jsonl"
        assert read_metrics(log) == []
