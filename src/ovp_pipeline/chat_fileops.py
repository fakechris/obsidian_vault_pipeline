"""Inquiry transcript fileops (M21a / BL-082).

Mirrors :mod:`ovp_pipeline.live_concept_fileops` at the
markdown-frontmatter-key level: this module is the single writer
for ``40-Resources/Chats/YYYY-MM/<topic-slug>-<short-hash>.md``.
Every other writer (Obsidian, MCP tools, future merge utilities)
must leave the transcript intact.

What this module owns
---------------------

* **Path computation** — :func:`build_chat_path` derives the canonical
  vault-relative location for a new inquiry session.
* **Schema** — :class:`ChatFrontmatter` (frozen dataclass) +
  :func:`render_initial_chat` for the empty-file initial state.
* **Append-only turn writes** — :func:`append_turn` and
  :func:`mark_interrupted` add ``## User · <ISO>`` /
  ``## Assistant · <ISO> · turn-N`` sections without rewriting
  earlier turns.  Each assistant turn carries an inline
  ``<!-- context-manifest ... -->`` HTML comment as the
  read-only audit snapshot recorded at the time of the call.
* **Stream-safe atomic writes** — :func:`pending_chat_block` is
  a context manager that buffers the assistant turn in memory and
  flushes via a ``.pending`` rename, so a mid-stream crash never
  leaves a torn transcript.

What this module does NOT own
-----------------------------

* Token accounting — lives in the append-only ``audit_events``
  ledger (BL-084 cost guardrail reads from there).  Frontmatter
  carries ``turn_count`` + ``last_message_at`` only.
* Visibility / FTS — the ``visibility`` field is just stored
  here; BL-085 decides whether an indexed session writes the
  ``pages_index`` / ``page_fts`` shadow rows.
* Retrieval — :mod:`ovp_pipeline.context_binder` (BL-083) reads
  *current* vault state on every turn; the manifest persisted
  here is an audit snapshot, never re-read.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import secrets
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Iterator

import yaml

logger = logging.getLogger(__name__)


# Canonical vault-relative location for inquiry transcripts.  Lives
# under ``40-Resources/`` (long-lived artifacts, not intake).
CHATS_DIR: Final[str] = "40-Resources/Chats"

# YAML ``type`` marker.  Frontmatter without this exact value isn't
# treated as a chat — the same "type-as-tag" discipline the rest of
# the vault uses.
CHAT_TYPE: Final[str] = "chat"

# Schema version of the frontmatter contract.  Bump when fields
# change shape; the projection (BL-085) gates on this value.
CHAT_SCHEMA_VERSION: Final[int] = 1

# Hard cap on the markdown topic slug we synthesize from the first
# user message / anchor title.  Filesystems happily take longer, but
# long filenames make the operator's Obsidian file picker painful.
_SLUG_MAX_LEN: Final[int] = 48

# Hex length of the per-session disambiguator that follows the slug.
# 6 chars = 24 bits = ~16M possible suffixes per (month, topic),
# vanishingly unlikely to collide for the volumes M21 will see.
_HASH_LEN: Final[int] = 6

# Valid frontmatter values.  Kept here so BL-085's projection
# constraint stays single-sourced with the fileops contract.
_STATUS_VALUES: Final[frozenset[str]] = frozenset({"active", "pinned", "archived"})
_VISIBILITY_VALUES: Final[frozenset[str]] = frozenset({"indexed", "unindexed"})
_ANCHOR_KINDS: Final[frozenset[str]] = frozenset({"note", "object", "crystal", "standalone"})
_SAVE_POLICY_VALUES: Final[frozenset[str]] = frozenset({"persistent", "ephemeral"})
_ROLE_VALUES: Final[frozenset[str]] = frozenset({"user", "assistant"})
_TURN_STATUS_VALUES: Final[frozenset[str]] = frozenset({"ok", "interrupted", "error"})


# ---------------------------------------------------------------
# Schema
# ---------------------------------------------------------------


@dataclass(frozen=True)
class ChatAnchor:
    """Inquiry anchor — what artifact the session is grounded in.

    For ``kind == "standalone"`` the operator opened ``/chat``
    without an anchor; ``path`` is ``""`` and ``title`` may be
    empty.  For every other kind, ``path`` is vault-relative and
    ``title`` mirrors the artifact's H1 (for display in the manifest
    card and the ``/chats`` list view)."""

    kind: str
    path: str = ""
    title: str = ""


@dataclass(frozen=True)
class ChatFrontmatter:
    """Parsed view of an inquiry transcript's frontmatter."""

    chat_id: str
    status: str = "active"
    visibility: str = "indexed"
    save_policy: str = "persistent"
    anchor: ChatAnchor = field(default_factory=lambda: ChatAnchor("standalone"))
    profile: str = "balanced"
    model: str = ""
    temperature: float = 0.7
    started_at: str = ""
    last_message_at: str = ""
    turn_count: int = 0
    schema_version: int = CHAT_SCHEMA_VERSION


# ---------------------------------------------------------------
# Slug + path
# ---------------------------------------------------------------


_SLUG_REPLACE_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    """Lower-case, dash-separated, ASCII-only slug.

    Non-ASCII characters degrade to nothing rather than being
    transliterated — operators typing in Chinese / Japanese still
    get a usable session via the hash suffix.  Empty input falls
    back to ``"inquiry"``.
    """
    if not text:
        return "inquiry"
    lowered = text.lower().strip()
    ascii_only = lowered.encode("ascii", errors="ignore").decode("ascii")
    collapsed = _SLUG_REPLACE_RE.sub("-", ascii_only).strip("-")
    truncated = collapsed[:_SLUG_MAX_LEN].rstrip("-")
    return truncated or "inquiry"


def _short_hash(seed: str) -> str:
    """Deterministic hex disambiguator computed from ``seed``.

    Falls back to ``secrets.token_hex`` when ``seed`` is empty so
    every chat path is unique even before the first turn lands.
    """
    if not seed:
        return secrets.token_hex(_HASH_LEN // 2)
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return digest[:_HASH_LEN]


def build_chat_path(
    vault_dir: Path | str,
    *,
    started_at: datetime | None = None,
    topic: str = "",
    anchor_ref: str = "",
) -> Path:
    """Return ``<vault>/40-Resources/Chats/YYYY-MM/<slug>-<hash>.md``.

    ``topic`` is typically the first user message; ``anchor_ref``
    is the anchor path so two sessions on the same anchor (but in
    different months) hash to distinct values.  The caller is
    responsible for ensuring the resulting path is unique — see
    :func:`ensure_unique_path`.
    """
    when = started_at or datetime.now(timezone.utc)
    year_month = when.strftime("%Y-%m")
    slug = _slugify(topic) if topic else _slugify(anchor_ref)
    seed = f"{when.isoformat()}::{anchor_ref}::{topic}"
    suffix = _short_hash(seed)
    filename = f"{slug}-{suffix}.md"
    return Path(vault_dir) / CHATS_DIR / year_month / filename


def ensure_unique_path(path: Path) -> Path:
    """Return ``path`` if it doesn't exist, else append ``-N`` until
    a free filename is found.  Defends against the (rare) hash
    collision + same-second start race."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for n in range(2, 1000):
        candidate = parent / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not find unique path under {parent}")


# ---------------------------------------------------------------
# Frontmatter <-> YAML
# ---------------------------------------------------------------


def _ordered_dump(data: dict[str, Any]) -> str:
    buf = io.StringIO()
    yaml.safe_dump(
        data,
        buf,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=10_000,
    )
    return buf.getvalue().rstrip()


def _frontmatter_to_yaml_dict(fm: ChatFrontmatter) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": CHAT_TYPE,
        "schema_version": fm.schema_version,
        "chat_id": fm.chat_id,
        "status": fm.status,
        "visibility": fm.visibility,
        "save_policy": fm.save_policy,
        "anchor": {
            "kind": fm.anchor.kind,
            "path": fm.anchor.path,
            "title": fm.anchor.title,
        },
        "profile": fm.profile,
        "model": fm.model,
        "temperature": fm.temperature,
        "started_at": fm.started_at,
        "last_message_at": fm.last_message_at,
        "turn_count": fm.turn_count,
    }
    return payload


def render_initial_chat(fm: ChatFrontmatter, title: str) -> str:
    """Render the file body when an inquiry session is created.

    Frontmatter + a single H1 ``# Chat — <title>``.  The first user
    turn lands via :func:`append_turn`; there's no need to inject
    an empty section here.
    """
    yaml_block = _ordered_dump(_frontmatter_to_yaml_dict(fm))
    safe_title = (title or "untitled").strip()
    return f"---\n{yaml_block}\n---\n\n# Chat — {safe_title}\n"


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return ``(raw_frontmatter, body)``.  Empty raw + full text as
    body when the file has no frontmatter."""
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---", 4)
    if end < 0:
        return "", text
    raw = text[4:end]
    body_start = end + len("\n---")
    if body_start < len(text) and text[body_start] == "\n":
        body_start += 1
    body = text[body_start:]
    return raw, body


def _read_top_level_value(raw: str, key: str) -> Any:
    if not raw.strip():
        return None
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed.get(key)


def parse_chat(path: Path) -> ChatFrontmatter | None:
    """Read ``path`` and return a :class:`ChatFrontmatter`, or
    ``None`` when the file isn't a chat transcript.

    A file is considered a chat when it carries ``type: chat`` in
    its frontmatter.  Anything else (regular notes, evergreens,
    live concepts) yields ``None`` so callers don't need a separate
    type check.
    """
    if not path.is_file():
        return None
    raw, _ = _split_frontmatter(path.read_text(encoding="utf-8"))
    if not raw:
        return None
    if _read_top_level_value(raw, "type") != CHAT_TYPE:
        return None
    try:
        parsed = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, dict):
        return None

    anchor_blob = parsed.get("anchor") or {}
    if not isinstance(anchor_blob, dict):
        anchor_blob = {}
    anchor_kind = str(anchor_blob.get("kind") or "standalone")
    if anchor_kind not in _ANCHOR_KINDS:
        anchor_kind = "standalone"
    anchor = ChatAnchor(
        kind=anchor_kind,
        path=str(anchor_blob.get("path") or ""),
        title=str(anchor_blob.get("title") or ""),
    )

    return ChatFrontmatter(
        chat_id=str(parsed.get("chat_id") or "").strip(),
        status=_coerce_enum(parsed.get("status"), _STATUS_VALUES, "active"),
        visibility=_coerce_enum(parsed.get("visibility"), _VISIBILITY_VALUES, "indexed"),
        save_policy=_coerce_enum(parsed.get("save_policy"), _SAVE_POLICY_VALUES, "persistent"),
        anchor=anchor,
        profile=str(parsed.get("profile") or "balanced"),
        model=str(parsed.get("model") or ""),
        temperature=_coerce_float(parsed.get("temperature"), 0.7),
        started_at=str(parsed.get("started_at") or ""),
        last_message_at=str(parsed.get("last_message_at") or ""),
        turn_count=_coerce_int(parsed.get("turn_count"), 0),
        schema_version=_coerce_int(parsed.get("schema_version"), CHAT_SCHEMA_VERSION),
    )


def _coerce_enum(value: object, allowed: frozenset[str], default: str) -> str:
    if isinstance(value, str) and value in allowed:
        return value
    return default


def _coerce_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _coerce_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


# ---------------------------------------------------------------
# Session creation
# ---------------------------------------------------------------


def new_chat_id() -> str:
    """Return ``"chat-<8hex>"`` — short, sortable, human-friendly."""
    return f"chat-{secrets.token_hex(4)}"


def create_chat_file(
    vault_dir: Path | str,
    *,
    chat_id: str | None = None,
    anchor: ChatAnchor | None = None,
    profile: str = "balanced",
    model: str = "",
    temperature: float = 0.7,
    visibility: str = "indexed",
    save_policy: str = "persistent",
    topic: str = "",
    started_at: datetime | None = None,
) -> tuple[Path, ChatFrontmatter]:
    """Create a new inquiry transcript and return (path, frontmatter).

    The file is created with zero turns; the operator's first
    message lands via :func:`append_turn`.  ``topic`` shapes the
    filename slug — pass the first 30-40 chars of the operator's
    message, or the anchor title for new-from-Reader entry buttons.

    Raises :class:`ValueError` for malformed inputs (unknown
    anchor kind, bad visibility / status / save_policy).  The
    fileops module enforces enum integrity at the boundary so
    downstream code can trust whatever it reads back.
    """
    if anchor is None:
        anchor = ChatAnchor("standalone")
    if anchor.kind not in _ANCHOR_KINDS:
        raise ValueError(
            f"unknown anchor kind {anchor.kind!r}; " f"expected one of {sorted(_ANCHOR_KINDS)}"
        )
    if visibility not in _VISIBILITY_VALUES:
        raise ValueError(
            f"unknown visibility {visibility!r}; " f"expected one of {sorted(_VISIBILITY_VALUES)}"
        )
    if save_policy not in _SAVE_POLICY_VALUES:
        raise ValueError(
            f"unknown save_policy {save_policy!r}; "
            f"expected one of {sorted(_SAVE_POLICY_VALUES)}"
        )

    when = started_at or datetime.now(timezone.utc)
    cid = (chat_id or new_chat_id()).strip()
    if not cid:
        raise ValueError("chat_id cannot be empty")

    fm = ChatFrontmatter(
        chat_id=cid,
        status="active",
        visibility=visibility,
        save_policy=save_policy,
        anchor=anchor,
        profile=profile,
        model=model,
        temperature=temperature,
        started_at=when.isoformat().replace("+00:00", "Z"),
        last_message_at=when.isoformat().replace("+00:00", "Z"),
        turn_count=0,
    )

    seed_topic = topic or anchor.title or anchor.path or cid
    raw_path = build_chat_path(
        vault_dir,
        started_at=when,
        topic=seed_topic,
        anchor_ref=anchor.path or cid,
    )
    path = ensure_unique_path(raw_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    title = anchor.title or topic or "untitled"
    path.write_text(render_initial_chat(fm, title), encoding="utf-8")
    return path, fm


# ---------------------------------------------------------------
# Turn append
# ---------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _render_turn(
    *,
    role: str,
    body: str,
    timestamp: str,
    turn_number: int | None,
    status: str = "ok",
    interruption_reason: str = "",
    manifest_lines: list[str] | None = None,
) -> str:
    """Render one ``## User`` or ``## Assistant`` block as markdown.

    ``manifest_lines`` is rendered as a ``<!-- context-manifest
    ... -->`` HTML comment before the body — present on assistant
    turns, omitted on user turns.  Interrupted assistant turns get
    a ``status: interrupted, reason: <r>`` comment instead.
    """
    if role == "user":
        header = f"## User · {timestamp}"
    else:
        suffix = f" · turn-{turn_number}" if turn_number is not None else ""
        if status == "interrupted":
            suffix += " · interrupted"
        elif status == "error":
            suffix += " · error"
        header = f"## Assistant · {timestamp}{suffix}"

    parts: list[str] = [header, ""]

    if role == "assistant":
        if status == "interrupted":
            reason = interruption_reason or "client_disconnected"
            parts.extend(
                [
                    f"<!-- status: interrupted, reason: {reason} -->",
                    "",
                ]
            )
        elif manifest_lines:
            parts.append("<!-- context-manifest")
            parts.extend(f"  {line}" for line in manifest_lines)
            parts.extend(["-->", ""])

    body_text = body.rstrip("\n")
    if body_text:
        parts.append(body_text)
        parts.append("")
    parts.append("")  # extra blank line between turns
    return "\n".join(parts)


def append_turn(
    path: Path,
    *,
    role: str,
    body: str,
    timestamp: str | None = None,
    turn_number: int | None = None,
    status: str = "ok",
    interruption_reason: str = "",
    manifest_lines: list[str] | None = None,
    update_frontmatter: bool = True,
) -> ChatFrontmatter:
    """Append a turn to ``path`` atomically.

    Writes the new turn + an updated frontmatter (``turn_count``
    increments by 1, ``last_message_at`` advances to ``timestamp``)
    via a ``.pending`` rename so a crash never leaves a torn file.

    Returns the post-write :class:`ChatFrontmatter`.

    Raises :class:`ValueError` when:

    * ``path`` doesn't exist or isn't a chat transcript
    * ``role`` isn't ``"user"`` or ``"assistant"``
    * ``status`` isn't ``"ok"`` / ``"interrupted"`` / ``"error"``
    """
    if role not in _ROLE_VALUES:
        raise ValueError(f"unknown role {role!r}; expected one of {sorted(_ROLE_VALUES)}")
    if status not in _TURN_STATUS_VALUES:
        raise ValueError(
            f"unknown turn status {status!r}; " f"expected one of {sorted(_TURN_STATUS_VALUES)}"
        )
    if not path.is_file():
        raise ValueError(f"cannot append_turn: {path} does not exist")

    text = path.read_text(encoding="utf-8")
    raw, body_text = _split_frontmatter(text)
    if not raw:
        raise ValueError(f"cannot append_turn: {path} has no frontmatter")
    if _read_top_level_value(raw, "type") != CHAT_TYPE:
        raise ValueError(f"cannot append_turn: {path} is not type: {CHAT_TYPE}")
    fm_current = parse_chat(path)
    if fm_current is None:
        raise ValueError(f"cannot append_turn: {path} frontmatter is malformed")

    ts = (timestamp or _iso_now()).strip()
    new_turn = _render_turn(
        role=role,
        body=body,
        timestamp=ts,
        turn_number=turn_number,
        status=status,
        interruption_reason=interruption_reason,
        manifest_lines=manifest_lines,
    )

    updated_fm = fm_current
    if update_frontmatter:
        updated_fm = ChatFrontmatter(
            chat_id=fm_current.chat_id,
            status=fm_current.status,
            visibility=fm_current.visibility,
            save_policy=fm_current.save_policy,
            anchor=fm_current.anchor,
            profile=fm_current.profile,
            model=fm_current.model,
            temperature=fm_current.temperature,
            started_at=fm_current.started_at,
            last_message_at=ts,
            turn_count=fm_current.turn_count + 1,
            schema_version=fm_current.schema_version,
        )
        new_raw = _ordered_dump(_frontmatter_to_yaml_dict(updated_fm))
    else:
        new_raw = raw

    if not body_text.endswith("\n"):
        body_text = body_text + "\n"
    new_body = body_text + new_turn
    new_text = f"---\n{new_raw}\n---\n\n{new_body.lstrip(chr(10))}"

    _atomic_write(path, new_text)
    return updated_fm


def mark_interrupted(
    path: Path,
    *,
    partial_body: str,
    turn_number: int | None = None,
    reason: str = "client_disconnected",
    timestamp: str | None = None,
) -> ChatFrontmatter:
    """Append a partial assistant turn with ``status: interrupted``.

    Convenience wrapper around :func:`append_turn`.  The partial
    text that streamed before the abort is preserved verbatim;
    the operator can re-send via the same endpoint to retry and
    a new ``status: ok`` assistant turn lands after the interrupted
    one.
    """
    return append_turn(
        path,
        role="assistant",
        body=partial_body,
        timestamp=timestamp,
        turn_number=turn_number,
        status="interrupted",
        interruption_reason=reason,
        manifest_lines=None,
    )


# ---------------------------------------------------------------
# Atomic write + pending block
# ---------------------------------------------------------------


def _atomic_write(path: Path, text: str) -> None:
    """Replace ``path`` atomically.

    Writes to a sibling temp file in the same directory (so the
    ``os.replace`` is a same-filesystem rename) then renames over
    the target.  Same pattern the rest of OVP uses for canonical
    file writes — keeps Obsidian's filewatch from seeing a partial
    transcript.
    """
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.stem + ".",
        suffix=".tmp",
        dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextmanager
def pending_chat_block(path: Path) -> Iterator["PendingChat"]:
    """Buffer assistant tokens in memory; flush atomically on exit.

    Yields a :class:`PendingChat` whose ``append`` method records
    streamed tokens.  Two completion paths:

    1. The context exits normally with ``pending.commit(...)`` —
       ``append_turn`` writes the full assistant turn.
    2. The context exits via an exception OR without ``commit`` —
       ``mark_interrupted`` writes the partial buffer as a
       ``status: interrupted`` turn.

    The intentional design: at no point is the in-progress reply
    visible to readers via the canonical markdown file.  The
    operator sees streaming tokens via the SSE pipe (BL-086); the
    transcript only ever shows committed turns.
    """
    state = PendingChat(path=path)
    try:
        yield state
        if not state._committed:
            # Context exited without commit — treat as interrupted.
            mark_interrupted(
                path,
                partial_body=state.buffer,
                turn_number=state.turn_number,
                reason=state.reason or "no_commit",
                timestamp=state.timestamp,
            )
    except BaseException:
        try:
            mark_interrupted(
                path,
                partial_body=state.buffer,
                turn_number=state.turn_number,
                reason=state.reason or "exception",
                timestamp=state.timestamp,
            )
        except Exception:
            logger.exception(
                "chat_fileops: failed to write interrupted turn for %s",
                path,
            )
        raise


@dataclass
class PendingChat:
    """In-progress assistant turn buffer (see :func:`pending_chat_block`).

    Not a public schema — exposed only as the value yielded by the
    context manager.  Treat as an internal scratchpad.
    """

    path: Path
    buffer: str = ""
    turn_number: int | None = None
    reason: str = ""
    timestamp: str | None = None
    _committed: bool = False

    def append(self, token: str) -> None:
        """Buffer streamed token text.  No I/O — fast hot path."""
        if token:
            self.buffer += token

    def commit(
        self,
        *,
        manifest_lines: list[str] | None = None,
        timestamp: str | None = None,
    ) -> ChatFrontmatter:
        """Write the buffered turn as a ``status: ok`` assistant turn."""
        ts = timestamp or self.timestamp
        fm = append_turn(
            self.path,
            role="assistant",
            body=self.buffer,
            timestamp=ts,
            turn_number=self.turn_number,
            manifest_lines=manifest_lines,
        )
        self._committed = True
        return fm
