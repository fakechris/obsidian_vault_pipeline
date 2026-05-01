from __future__ import annotations

import json
from pathlib import Path

from ovp_pipeline.runtime import (
    append_jsonl,
    iter_jsonl,
    rotate_jsonl_if_needed,
    _count_lines_fast,
)


def _write_lines(path: Path, n: int, *, prefix: str = "evt") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({"event_type": prefix, "seq": i, "ts": f"2026-04-{i:02d}"}) + "\n")


class TestCountLinesFast:
    def test_nonexistent(self, tmp_path: Path) -> None:
        assert _count_lines_fast(tmp_path / "missing.jsonl") == 0

    def test_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        assert _count_lines_fast(f) == 0

    def test_counts_nonempty(self, tmp_path: Path) -> None:
        f = tmp_path / "data.jsonl"
        _write_lines(f, 42)
        assert _count_lines_fast(f) == 42


class TestRotateJsonl:
    def test_no_rotation_when_small(self, tmp_path: Path) -> None:
        f = tmp_path / "small.jsonl"
        _write_lines(f, 5)
        result = rotate_jsonl_if_needed(f, max_lines=10)
        assert result is None
        assert f.exists()

    def test_rotation_when_at_limit(self, tmp_path: Path) -> None:
        f = tmp_path / "big.jsonl"
        _write_lines(f, 15)
        result = rotate_jsonl_if_needed(f, max_lines=10)
        assert result is not None
        assert result.exists()
        assert not f.exists()
        assert result.name.startswith("big.")
        assert result.name.endswith(".jsonl")

    def test_sidecar_written(self, tmp_path: Path) -> None:
        f = tmp_path / "log.jsonl"
        _write_lines(f, 20, prefix="test_event")
        result = rotate_jsonl_if_needed(f, max_lines=10)
        assert result is not None
        sidecar = result.with_suffix(".stats.json")
        assert sidecar.exists()
        stats = json.loads(sidecar.read_text())
        assert stats["line_count"] == 20
        assert "test_event" in stats["event_types"]

    def test_nonexistent_noop(self, tmp_path: Path) -> None:
        result = rotate_jsonl_if_needed(tmp_path / "no.jsonl", max_lines=5)
        assert result is None

    def test_duplicate_archive_name(self, tmp_path: Path) -> None:
        f = tmp_path / "dup.jsonl"
        _write_lines(f, 20)
        first = rotate_jsonl_if_needed(f, max_lines=10)
        assert first is not None
        _write_lines(f, 20)
        second = rotate_jsonl_if_needed(f, max_lines=10)
        assert second is not None
        assert first != second
        assert first.exists()
        assert second.exists()


class TestAppendJsonl:
    def test_creates_and_appends(self, tmp_path: Path) -> None:
        f = tmp_path / "sub" / "test.jsonl"
        append_jsonl(f, {"event_type": "a"}, max_lines=100)
        append_jsonl(f, {"event_type": "b"}, max_lines=100)
        lines = f.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_auto_rotation(self, tmp_path: Path) -> None:
        f = tmp_path / "auto.jsonl"
        _write_lines(f, 15)
        append_jsonl(f, {"event_type": "new"}, max_lines=10)
        assert f.exists()
        lines = f.read_text().strip().split("\n")
        assert len(lines) == 1
        archives = list(tmp_path.glob("auto.*.jsonl"))
        assert len(archives) == 1


class TestIterJsonl:
    def test_full_read(self, tmp_path: Path) -> None:
        f = tmp_path / "full.jsonl"
        _write_lines(f, 50)
        items = list(iter_jsonl(f))
        assert len(items) == 50
        assert items[0]["seq"] == 0
        assert items[-1]["seq"] == 49

    def test_tail_read(self, tmp_path: Path) -> None:
        f = tmp_path / "tail.jsonl"
        _write_lines(f, 100)
        items = list(iter_jsonl(f, tail_lines=10))
        assert len(items) == 10
        assert items[-1]["seq"] == 99
        assert items[0]["seq"] == 90

    def test_tail_larger_than_file(self, tmp_path: Path) -> None:
        f = tmp_path / "small.jsonl"
        _write_lines(f, 5)
        items = list(iter_jsonl(f, tail_lines=100))
        assert len(items) == 5

    def test_nonexistent(self, tmp_path: Path) -> None:
        items = list(iter_jsonl(tmp_path / "no.jsonl"))
        assert items == []

    def test_malformed_lines_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.jsonl"
        f.write_text('{"a":1}\nnot json\n{"b":2}\n\n')
        items = list(iter_jsonl(f))
        assert len(items) == 2

    def test_tail_with_empty_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "gaps.jsonl"
        lines_out = []
        for i in range(20):
            lines_out.append(json.dumps({"seq": i}))
            if i % 3 == 0:
                lines_out.append("")
        f.write_text("\n".join(lines_out) + "\n")
        items = list(iter_jsonl(f, tail_lines=5))
        assert len(items) == 5
        assert items[-1]["seq"] == 19
