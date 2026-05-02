"""Semantic embedding backend for OVP knowledge index.

Uses ``mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ`` (1024-dim) on Apple
Silicon via MLX, with a pure-hash fallback for environments where MLX is
unavailable (CI, Intel Mac, Linux).

The module is intentionally lazy-loaded: the first call to :func:`embed_text`
triggers model download / load (≈3 s on M-series).  Subsequent calls run in
≈50–100 ms per chunk.

Thread safety: MLX is **not** thread-safe.  All inference goes through a
module-level ``threading.Lock`` to prevent concurrent GPU access.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
from array import array
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

EMBEDDING_DIMENSIONS = 1024
EMBEDDING_MODEL = "qwen3-embedding-0.6b-4bit"
_MODEL_HF_ID = "mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ"

HASH_EMBEDDING_DIMENSIONS = 128
HASH_EMBEDDING_MODEL = "local-hash-v1"

_mlx_lock = threading.Lock()
_loaded_model = None
_loaded_tokenizer = None
_backend: str | None = None


def _load_mlx_model():
    """Load the MLX model and tokenizer once, caching globally."""
    global _loaded_model, _loaded_tokenizer, _backend
    if _backend is not None:
        return

    try:
        from mlx_lm import load as mlx_load  # type: ignore[import-untyped]

        _loaded_model, _loaded_tokenizer = mlx_load(_MODEL_HF_ID)
        _backend = "mlx"
        logger.info("Loaded %s via MLX", _MODEL_HF_ID)
    except Exception:
        logger.warning(
            "MLX unavailable — falling back to local-hash-v1 (install mlx-lm for semantic embeddings)",
            exc_info=True,
        )
        _backend = "hash"


def embed_text(text: str) -> bytes:
    """Return an L2-normalised embedding as a ``float32`` BLOB.

    On Apple Silicon with ``mlx-lm`` installed, uses the Qwen3-Embedding model
    (1024-dim).  Otherwise falls back to the legacy BLAKE2b bucket hash
    (128-dim).
    """
    if _backend is None:
        with _mlx_lock:
            _load_mlx_model()

    if _backend == "mlx":
        return _embed_text_mlx(text)
    return _embed_text_hash(text, HASH_EMBEDDING_DIMENSIONS)


def get_dimensions() -> int:
    """Return the active embedding dimensionality."""
    if _backend is None:
        with _mlx_lock:
            _load_mlx_model()
    return EMBEDDING_DIMENSIONS if _backend == "mlx" else HASH_EMBEDDING_DIMENSIONS


def get_model_name() -> str:
    """Return the active embedding model identifier."""
    if _backend is None:
        with _mlx_lock:
            _load_mlx_model()
    return EMBEDDING_MODEL if _backend == "mlx" else HASH_EMBEDDING_MODEL


def _embed_text_mlx(text: str) -> bytes:
    """Encode *text* via Qwen3-Embedding on MLX, returning a float32 BLOB."""
    import mlx.core as mx  # type: ignore[import-untyped]

    with _mlx_lock:
        ids = _loaded_tokenizer.encode(text)
        if len(ids) > 512:
            ids = ids[:512]
        input_ids = mx.array([ids])

        hidden = _loaded_model.model(input_ids)
        last_hidden = hidden[:, -1, :]

        norm = mx.sqrt(mx.sum(last_hidden * last_hidden, axis=-1, keepdims=True))
        normalized = last_hidden / mx.maximum(norm, mx.array(1e-12))
        mx.eval(normalized)

        vec = normalized[0].tolist()

    return array("f", vec).tobytes()


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
