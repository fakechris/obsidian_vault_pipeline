"""Tests for M21a / BL-082 — chat_fileops."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ovp_pipeline.chat_fileops import (
    CHAT_SCHEMA_VERSION,
    CHAT_TYPE,
    CHATS_DIR,
    ChatAnchor,
    ChatFrontmatter,
    append_turn,
    build_chat_path,
    create_chat_file,
    ensure_unique_path,
    mark_interrupted,
    new_chat_id,
    parse_chat,
    pending_chat_block,
    render_initial_chat,
)

# ── path computation ────────────────────────────────────────────


def test_build_chat_path_lives_under_40_resources(tmp_path: Path):
    when = datetime(2026, 5, 12, 14, 0, 0, tzinfo=timezone.utc)
    p = build_chat_path(
        tmp_path,
        started_at=when,
        topic="memory architecture",
        anchor_ref="40-Resources/Generated/digests/x.md",
    )
    rel = p.relative_to(tmp_path)
    parts = rel.parts
    assert parts[0:2] == ("40-Resources", "Chats")
    assert parts[2] == "2026-05"
    assert parts[3].endswith(".md")
    # slug + 6 hex chars + extension
    name = parts[3]
    assert "memory-architecture" in name
    # File name: <slug>-<6 hex>.md
    stem = name[: -len(".md")]
    suffix = stem.rsplit("-", 1)[-1]
    assert len(suffix) == 6
    assert all(c in "0123456789abcdef" for c in suffix)


def test_build_chat_path_falls_back_to_anchor_when_topic_empty(tmp_path: Path):
    when = datetime(2026, 5, 12, tzinfo=timezone.utc)
    p = build_chat_path(
        tmp_path,
        started_at=when,
        topic="",
        anchor_ref="40-Resources/Generated/digests/daily.md",
    )
    assert "daily" in p.name or "digests" in p.name


def test_build_chat_path_non_ascii_topic_degrades_gracefully(tmp_path: Path):
    when = datetime(2026, 5, 12, tzinfo=timezone.utc)
    p = build_chat_path(
        tmp_path,
        started_at=when,
        topic="思考记忆架构",
        anchor_ref="x.md",
    )
    # All-Chinese topic + ASCII-only slug → falls back to "inquiry"
    assert p.name.startswith("inquiry-")


def test_ensure_unique_path_collision_handling(tmp_path: Path):
    target = tmp_path / "x.md"
    target.write_text("already here", encoding="utf-8")
    fresh = ensure_unique_path(target)
    assert fresh != target
    assert fresh.name == "x-2.md"


# ── frontmatter render + parse ──────────────────────────────────


def test_render_initial_chat_round_trips(tmp_path: Path):
    fm = ChatFrontmatter(
        chat_id="chat-test01",
        anchor=ChatAnchor(kind="note", path="20-Areas/note.md", title="A note"),
        profile="balanced",
        model="anthropic/claude-sonnet-4-6",
        started_at="2026-05-12T11:00:00Z",
        last_message_at="2026-05-12T11:00:00Z",
        turn_count=0,
    )
    md = render_initial_chat(fm, "A note")
    assert "type: chat" in md
    assert "chat_id: chat-test01" in md
    assert "kind: note" in md
    assert "# Chat — A note" in md

    p = tmp_path / "x.md"
    p.write_text(md, encoding="utf-8")
    parsed = parse_chat(p)
    assert parsed is not None
    assert parsed.chat_id == "chat-test01"
    assert parsed.anchor.kind == "note"
    assert parsed.anchor.path == "20-Areas/note.md"
    assert parsed.profile == "balanced"
    assert parsed.turn_count == 0
    assert parsed.schema_version == CHAT_SCHEMA_VERSION


def test_parse_chat_rejects_non_chat_files(tmp_path: Path):
    other = tmp_path / "x.md"
    other.write_text("---\ntype: evergreen\ntitle: X\n---\n\nbody\n", encoding="utf-8")
    assert parse_chat(other) is None


def test_parse_chat_returns_none_for_missing_or_empty(tmp_path: Path):
    assert parse_chat(tmp_path / "nope.md") is None
    empty = tmp_path / "empty.md"
    empty.write_text("", encoding="utf-8")
    assert parse_chat(empty) is None


def test_parse_chat_coerces_invalid_enums_to_defaults(tmp_path: Path):
    """Frontmatter with an unknown ``status`` value falls back rather
    than raising — keeps the reader robust to operator edits."""
    p = tmp_path / "x.md"
    p.write_text(
        "---\ntype: chat\nchat_id: c1\nstatus: weird\nvisibility: bogus\n"
        "anchor:\n  kind: not-a-kind\n  path: x\n  title: y\n---\n\n# body\n",
        encoding="utf-8",
    )
    fm = parse_chat(p)
    assert fm is not None
    assert fm.status == "active"
    assert fm.visibility == "indexed"
    assert fm.anchor.kind == "standalone"


# ── session creation ────────────────────────────────────────────


def test_new_chat_id_format():
    cid = new_chat_id()
    assert cid.startswith("chat-")
    assert len(cid) == len("chat-") + 8


def test_create_chat_file_writes_initial_state(tmp_path: Path):
    path, fm = create_chat_file(
        tmp_path,
        anchor=ChatAnchor(kind="note", path="20-Areas/note.md", title="Note"),
        profile="balanced",
        model="anthropic/claude-sonnet-4-6",
        topic="What does the digest say about X",
    )
    assert path.exists()
    assert path.relative_to(tmp_path).parts[0] == "40-Resources"
    assert fm.chat_id.startswith("chat-")
    assert fm.turn_count == 0
    text = path.read_text(encoding="utf-8")
    assert "type: chat" in text
    assert f"chat_id: {fm.chat_id}" in text


def test_create_chat_file_rejects_bad_anchor_kind(tmp_path: Path):
    with pytest.raises(ValueError, match="anchor kind"):
        create_chat_file(
            tmp_path,
            anchor=ChatAnchor(kind="bogus"),
        )


def test_create_chat_file_rejects_bad_visibility(tmp_path: Path):
    with pytest.raises(ValueError, match="visibility"):
        create_chat_file(tmp_path, visibility="private")


# ── append_turn ────────────────────────────────────────────────


def test_append_user_turn_increments_count(tmp_path: Path):
    path, fm = create_chat_file(tmp_path)
    assert fm.turn_count == 0
    new_fm = append_turn(
        path,
        role="user",
        body="Hello, vault.",
        timestamp="2026-05-12T11:00:01Z",
    )
    assert new_fm.turn_count == 1
    assert new_fm.last_message_at == "2026-05-12T11:00:01Z"
    text = path.read_text(encoding="utf-8")
    assert "## User · 2026-05-12T11:00:01Z" in text
    assert "Hello, vault." in text


def test_append_assistant_turn_carries_manifest(tmp_path: Path):
    path, _ = create_chat_file(tmp_path)
    new_fm = append_turn(
        path,
        role="assistant",
        body="Looking at [[evergreen-x]] ...",
        timestamp="2026-05-12T11:00:02Z",
        turn_number=2,
        manifest_lines=[
            "context_built_at: 2026-05-12T11:00:01Z",
            "token_estimate: 8421",
            "included_anchor: 20-Areas/note.md",
        ],
    )
    assert new_fm.turn_count == 1
    text = path.read_text(encoding="utf-8")
    assert "## Assistant · 2026-05-12T11:00:02Z · turn-2" in text
    assert "<!-- context-manifest" in text
    assert "context_built_at: 2026-05-12T11:00:01Z" in text
    assert "Looking at [[evergreen-x]]" in text


def test_append_turn_rejects_bad_role(tmp_path: Path):
    path, _ = create_chat_file(tmp_path)
    with pytest.raises(ValueError, match="role"):
        append_turn(path, role="system", body="x")


def test_append_turn_refuses_non_chat_file(tmp_path: Path):
    other = tmp_path / "x.md"
    other.write_text("---\ntype: evergreen\n---\n\nbody\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a valid chat transcript"):
        append_turn(other, role="user", body="hi")


def test_mark_interrupted_records_partial_text(tmp_path: Path):
    path, _ = create_chat_file(tmp_path)
    new_fm = mark_interrupted(
        path,
        partial_body="streamed so far ...",
        turn_number=2,
        reason="client_disconnected",
        timestamp="2026-05-12T11:01:30Z",
    )
    assert new_fm.turn_count == 1
    text = path.read_text(encoding="utf-8")
    assert "· turn-2 · interrupted" in text
    assert "status: interrupted" in text
    assert "client_disconnected" in text
    assert "streamed so far ..." in text


# ── pending_chat_block — stream-safe atomic append ─────────────


def test_pending_chat_block_commit_writes_turn(tmp_path: Path):
    path, _ = create_chat_file(tmp_path)
    with pending_chat_block(path) as pending:
        pending.turn_number = 2
        pending.timestamp = "2026-05-12T11:00:05Z"
        pending.append("Hello ")
        pending.append("world.")
        pending.commit(
            manifest_lines=["token_estimate: 42"],
        )
    text = path.read_text(encoding="utf-8")
    assert "Hello world." in text
    assert "## Assistant · 2026-05-12T11:00:05Z · turn-2" in text
    assert "token_estimate: 42" in text


def test_pending_chat_block_no_commit_marks_interrupted(tmp_path: Path):
    path, _ = create_chat_file(tmp_path)
    with pending_chat_block(path) as pending:
        pending.turn_number = 2
        pending.timestamp = "2026-05-12T11:00:05Z"
        pending.reason = "client_disconnected"
        pending.append("partial stream ")
        # No commit() before exit
    text = path.read_text(encoding="utf-8")
    assert "· turn-2 · interrupted" in text
    assert "partial stream" in text
    assert "client_disconnected" in text


def test_pending_chat_block_exception_marks_interrupted(tmp_path: Path):
    path, _ = create_chat_file(tmp_path)

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        with pending_chat_block(path) as pending:
            pending.turn_number = 2
            pending.timestamp = "2026-05-12T11:00:05Z"
            pending.append("crashed mid-stream ")
            raise Boom("network fell out")

    text = path.read_text(encoding="utf-8")
    assert "· turn-2 · interrupted" in text
    assert "crashed mid-stream" in text


# ── atomic write integrity ────────────────────────────────────


def test_atomic_write_leaves_no_temp_files_on_success(tmp_path: Path):
    path, _ = create_chat_file(tmp_path)
    append_turn(path, role="user", body="x", timestamp="2026-05-12T11:01:00Z")
    siblings = list(path.parent.iterdir())
    # Only the canonical .md file + the .lock sentinel remain — no
    # .tmp leftovers.
    suffixes = {p.suffix for p in siblings}
    assert ".tmp" not in suffixes


def test_chats_dir_constant_matches_plan():
    """Lock the path so future BL changes notice a relocation."""
    assert CHATS_DIR == "40-Resources/Chats"
    assert CHAT_TYPE == "chat"


# ── codex P2 — concurrent append safety ───────────────────────


def test_concurrent_appends_serialize_via_per_chat_lock(tmp_path: Path):
    """Two threaded appends against the same chat must both land.

    Codex review P2: without the per-chat ``flock``, the later
    writer's ``os.replace`` would drop the earlier writer's turn.
    The test fires two appends concurrently and asserts both
    turns appear + ``turn_count`` ends at exactly 2.
    """
    import threading

    path, _ = create_chat_file(tmp_path)

    results: list[Exception | None] = [None, None]

    def append_one(idx: int, label: str, ts: str) -> None:
        try:
            append_turn(
                path,
                role="user",
                body=f"message-{label}",
                timestamp=ts,
            )
        except Exception as exc:  # pragma: no cover - test failure
            results[idx] = exc

    t1 = threading.Thread(
        target=append_one,
        args=(0, "alpha", "2026-05-12T11:00:01Z"),
    )
    t2 = threading.Thread(
        target=append_one,
        args=(1, "beta", "2026-05-12T11:00:02Z"),
    )
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results == [None, None]
    text = path.read_text(encoding="utf-8")
    assert "message-alpha" in text
    assert "message-beta" in text
    fm = parse_chat(path)
    assert fm is not None
    assert fm.turn_count == 2


# ── codex P2 — manifest required for assistant ok turn ────────


def test_assistant_ok_turn_requires_manifest(tmp_path: Path):
    """Codex P2 — audit contract: every successful assistant turn
    must carry a non-empty manifest snapshot.  Skipping it would
    leave the operator unable to audit which context backed the
    reply."""
    path, _ = create_chat_file(tmp_path)
    with pytest.raises(ValueError, match="manifest_lines is required"):
        append_turn(
            path,
            role="assistant",
            body="answer",
            turn_number=1,
            # No manifest_lines passed
        )


def test_assistant_ok_turn_accepts_explicit_no_context_manifest(
    tmp_path: Path,
):
    """Operators that have nothing meaningful to record can pass
    a single-line manifest like ``["note: no retrieval context"]``
    — keeps the audit trail honest without forcing real context."""
    path, _ = create_chat_file(tmp_path)
    append_turn(
        path,
        role="assistant",
        body="standalone answer",
        timestamp="2026-05-12T11:00:02Z",
        turn_number=1,
        manifest_lines=["note: no retrieval context"],
    )
    text = path.read_text(encoding="utf-8")
    assert "no retrieval context" in text


# ── codex P2 — no duplicate turn after commit ─────────────────


def test_pending_block_no_duplicate_after_commit(tmp_path: Path):
    """Codex P2 — if the body raises *after* ``pending.commit()``
    succeeded, do NOT also write an interrupted turn.  The commit
    already produced a status: ok turn + bumped turn_count.
    Double-writing would duplicate the response."""
    path, _ = create_chat_file(tmp_path)

    class PostCommitFailure(RuntimeError):
        pass

    with pytest.raises(PostCommitFailure):
        with pending_chat_block(path) as pending:
            pending.turn_number = 1
            pending.timestamp = "2026-05-12T11:00:05Z"
            pending.append("real answer")
            pending.commit(manifest_lines=["token_estimate: 10"])
            raise PostCommitFailure("downstream blew up")

    fm = parse_chat(path)
    assert fm is not None
    # Exactly one assistant turn, not two.
    assert fm.turn_count == 1
    text = path.read_text(encoding="utf-8")
    assert text.count("## Assistant · 2026-05-12T11:00:05Z") == 1
    assert "interrupted" not in text


# ── CodeRabbit — UTC handling ─────────────────────────────────


def test_naive_started_at_emits_utc_iso(tmp_path: Path):
    """A naive datetime passed to ``create_chat_file`` is treated as
    UTC, so ``started_at`` / ``last_message_at`` end with ``Z``."""
    naive = datetime(2026, 5, 12, 11, 0, 0)  # no tzinfo
    _, fm = create_chat_file(tmp_path, started_at=naive)
    assert fm.started_at.endswith("Z")
    assert fm.last_message_at.endswith("Z")
    assert "+00:00" not in fm.started_at


def test_non_utc_aware_started_at_normalises_to_utc(tmp_path: Path):
    """A non-UTC tz-aware datetime is converted to UTC, not pasted
    as ``+05:00`` (which would break the implicit-UTC schema)."""
    from datetime import timedelta

    nyc = timezone(timedelta(hours=-5))
    when = datetime(2026, 5, 12, 6, 0, 0, tzinfo=nyc)  # 11:00 UTC
    _, fm = create_chat_file(tmp_path, started_at=when)
    assert fm.started_at == "2026-05-12T11:00:00Z"
