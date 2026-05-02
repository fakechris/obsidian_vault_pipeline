"""Semantic embedding backend for OVP knowledge index.

Uses ``mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ`` (1024-dim) on Apple
Silicon via MLX, with a pure-hash fallback for environments where MLX is
unavailable (CI, Intel Mac, Linux).

Backend resolution order (first that imports successfully wins):
  1. ``mlx_embeddings.utils.load`` — preferred, encoder-aware pooling
     (returns ``text_embeds`` directly for embedding models)
  2. ``mlx_lm.load`` — fallback, uses last-token hidden state
     (works for Qwen3-Embedding because the 4-bit DWQ model exposes a
     decoder shape, but pooling is approximate)
  3. BLAKE2b 128-dim bucket hash — deterministic, no model needed

The module is intentionally lazy-loaded: the first call to :func:`embed_text`
triggers model download / load (≈3 s on M-series).  Subsequent calls run in
≈50–100 ms per chunk.

Thread safety: MLX is **not** thread-safe.  All inference goes through a
module-level ``threading.Lock`` to prevent concurrent GPU access.

Dimension consistency:
  Use :func:`assert_consistent_with` at startup (or before reindex) to detect
  the case where ``page_embeddings`` was previously written with a different
  backend (e.g. hash 128-dim and now MLX 1024-dim).  Mixing breaks cosine
  similarity since the BLOB layouts are incompatible.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
from array import array

logger = logging.getLogger(__name__)

EMBEDDING_DIMENSIONS = 1024
EMBEDDING_MODEL = "qwen3-embedding-0.6b-4bit"
_MODEL_HF_ID = "mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ"

HASH_EMBEDDING_DIMENSIONS = 128
HASH_EMBEDDING_MODEL = "local-hash-v1"

_mlx_lock = threading.Lock()
_loaded_model = None
_loaded_tokenizer = None
_backend: str | None = None      # "mlx_embeddings" | "mlx_lm" | "hash"


def _load_mlx_model() -> None:
    """Load the MLX model and tokenizer once, caching globally.

    Tries ``mlx_embeddings.utils.load`` first (preferred for embedding
    models — handles encoder pooling and exposes ``text_embeds``).  Falls
    back to ``mlx_lm.load`` if mlx_embeddings is not installed (less
    accurate pooling but still works in practice for the 4-bit DWQ build).
    Final fallback is the deterministic BLAKE2b hash backend.
    """
    global _loaded_model, _loaded_tokenizer, _backend
    if _backend is not None:
        return

    # Try mlx_embeddings first (designed for embedding models)
    try:
        from mlx_embeddings.utils import load as mlx_emb_load  # type: ignore[import-untyped]

        _loaded_model, _loaded_tokenizer = mlx_emb_load(_MODEL_HF_ID)
        _backend = "mlx_embeddings"
        logger.info("Loaded %s via mlx_embeddings", _MODEL_HF_ID)
        return
    except ImportError:
        logger.debug("mlx_embeddings not available, trying mlx_lm fallback")
    except Exception:
        logger.warning(
            "mlx_embeddings load failed, trying mlx_lm fallback",
            exc_info=True,
        )

    # Fallback to mlx_lm (older API, last-token pooling)
    try:
        from mlx_lm import load as mlx_load  # type: ignore[import-untyped]

        _loaded_model, _loaded_tokenizer = mlx_load(_MODEL_HF_ID)
        _backend = "mlx_lm"
        logger.info(
            "Loaded %s via mlx_lm (less accurate pooling — install "
            "mlx-embeddings for proper encoder behaviour)",
            _MODEL_HF_ID,
        )
        return
    except Exception:
        logger.warning(
            "MLX unavailable — falling back to local-hash-v1 "
            "(install mlx-embeddings or mlx-lm for semantic embeddings)",
            exc_info=True,
        )
        _backend = "hash"


def embed_text(text: str) -> bytes:
    """Return an L2-normalised embedding as a ``float32`` BLOB.

    On Apple Silicon with ``mlx-embeddings`` (or ``mlx-lm``) installed,
    uses the Qwen3-Embedding model (1024-dim).  Otherwise falls back to
    the legacy BLAKE2b bucket hash (128-dim).
    """
    if _backend is None:
        with _mlx_lock:
            _load_mlx_model()

    if _backend in ("mlx_embeddings", "mlx_lm"):
        return _embed_text_mlx(text)
    return _embed_text_hash(text, HASH_EMBEDDING_DIMENSIONS)


def get_dimensions() -> int:
    """Return the active embedding dimensionality.

    Triggers lazy model load on first call.
    """
    if _backend is None:
        with _mlx_lock:
            _load_mlx_model()
    return EMBEDDING_DIMENSIONS if _backend in ("mlx_embeddings", "mlx_lm") else HASH_EMBEDDING_DIMENSIONS


def get_model_name() -> str:
    """Return the active embedding model identifier.

    Triggers lazy model load on first call.
    """
    if _backend is None:
        with _mlx_lock:
            _load_mlx_model()
    return EMBEDDING_MODEL if _backend in ("mlx_embeddings", "mlx_lm") else HASH_EMBEDDING_MODEL


def get_backend() -> str:
    """Return the active backend identifier.

    One of: ``"mlx_embeddings"``, ``"mlx_lm"``, ``"hash"``.
    """
    if _backend is None:
        with _mlx_lock:
            _load_mlx_model()
    return _backend or "hash"


def assert_consistent_with(stored_model: str, stored_dim: int) -> tuple[bool, str]:
    """Check whether the active backend is compatible with stored embeddings.

    Returns ``(consistent, message)``.  When inconsistent, the caller should
    typically warn the user and recommend running a full reindex
    (``ovp-knowledge-index --vault-dir <vault>``) before issuing similarity
    queries — mixing 128-dim hash BLOBs with 1024-dim MLX BLOBs in the same
    table will silently produce nonsense cosine scores.

    Special case: an empty database (``stored_model == ""`` or
    ``stored_dim == 0``) is considered consistent — there is no incumbent
    to disagree with.
    """
    if not stored_model or stored_dim == 0:
        return (True, "page_embeddings is empty — no consistency check needed")

    active_model = get_model_name()
    active_dim = get_dimensions()

    if stored_model == active_model and stored_dim == active_dim:
        return (True, f"page_embeddings matches active backend ({active_model}, dim={active_dim})")

    msg = (
        f"page_embeddings dimension mismatch: stored={stored_model} "
        f"({stored_dim}d), active={active_model} ({active_dim}d). "
        f"Cosine similarity queries will produce wrong results until "
        f"page_embeddings is rebuilt. Run: ovp-knowledge-index --vault-dir <vault>"
    )
    return (False, msg)


def _embed_text_mlx(text: str) -> bytes:
    """Encode *text* via Qwen3-Embedding on MLX, returning a float32 BLOB.

    For ``mlx_embeddings`` backend, prefers ``output.text_embeds`` (the
    model's own pooled representation).  Falls back to last-hidden-state
    last-token pooling for ``mlx_lm``.
    """
    import mlx.core as mx  # type: ignore[import-untyped]

    with _mlx_lock:
        ids = _loaded_tokenizer.encode(text)
        if len(ids) > 512:
            ids = ids[:512]
        input_ids = mx.array([ids])

        if _backend == "mlx_embeddings":
            # mlx_embeddings exposes a model-aware forward that returns
            # an object with `.text_embeds` (preferred) or `.last_hidden_state`.
            outputs = _loaded_model(input_ids)
            if hasattr(outputs, "text_embeds"):
                vec = outputs.text_embeds[0]
            elif hasattr(outputs, "last_hidden_state"):
                # Mean-pool over sequence dim for encoder-style output
                last_hidden = outputs.last_hidden_state[0]
                vec = mx.mean(last_hidden, axis=0)
            else:
                # Fall back to direct array (some custom models return tensor directly)
                vec = outputs[0] if hasattr(outputs, "__getitem__") else outputs
        else:
            # mlx_lm path — use last token's hidden state (decoder convention)
            hidden = _loaded_model.model(input_ids)
            vec = hidden[:, -1, :][0]

        # L2-normalise
        norm = mx.sqrt(mx.sum(vec * vec))
        normalised = vec / mx.maximum(norm, mx.array(1e-12))
        mx.eval(normalised)

        flat = normalised.tolist()

    return array("f", flat).tobytes()


# ── Legacy hash fallback ────────────────────────────────────────────────

_TOKENIZE_RE = re.compile(r"[a-z0-9]+")


def _embed_text_hash(text: str, dimensions: int = 128) -> bytes:
    """BLAKE2b bucket hash — deterministic, no model needed."""
    tokens = _TOKENIZE_RE.findall(text.lower())
    vector = [0.0] * dimensions
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign

    norm = math.sqrt(sum(v * v for v in vector))
    if norm > 0:
        vector = [v / norm for v in vector]
    return array("f", vector).tobytes()
