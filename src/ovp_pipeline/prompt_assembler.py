"""
Thin prompt-assembly hook (Phase 32).

Today this module exists so any caller that drops canonical objects into a
prompt routes through a single emit point — keeping the Phase 32 reuse-event
ledger honest. The API is intentionally narrow:

  assemble(*, vault_dir, pack, object_ids, slot_specs, consumer_ref, ...) -> str

``slot_specs`` is the list of textual slots (already rendered upstream by the
caller); ``object_ids`` is the canonical object set the prompt depends on.
``assemble`` joins the slots, emits one ``trusted_reuse_event`` per resolved
object_id (surface=``prompt``), and returns the joined prompt text.

Phase 37 will grow this into a real compiler primitive that knows how to
fetch slots from the truth store. For now the API shape is a placeholder
that lets us wire reuse events from any prompt site without touching a
per-caller emitter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from .reuse_emitter import emit_reuse_events_for_object_ids


def assemble(
    *,
    vault_dir: Path | str,
    pack: str,
    slot_specs: Sequence[str],
    object_ids: Iterable[str],
    consumer_ref: str = "",
    separator: str = "\n\n",
    session_id: str | None = None,
) -> str:
    """Join ``slot_specs`` and emit one prompt-surface reuse event per object_id."""
    text = separator.join(str(slot) for slot in slot_specs if slot)
    object_id_list = [str(oid) for oid in object_ids if oid]
    if object_id_list:
        emit_reuse_events_for_object_ids(
            vault_dir,
            pack=pack,
            object_ids=object_id_list,
            surface="prompt",
            consumer_ref=consumer_ref,
            session_id=session_id,
        )
    return text
