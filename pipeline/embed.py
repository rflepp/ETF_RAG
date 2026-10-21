"""bge-small-en-v1.5 embeddings, L2-normalised and int8-quantised.

The same model runs at query time on Cloudflare Workers AI (@cf/baai/bge-small-en-v1.5).
Queries get the bge retrieval instruction prefix; passages are embedded as-is.
"""
from __future__ import annotations

import base64
from functools import lru_cache

import numpy as np

MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding
    return TextEmbedding(MODEL)


def embed(texts: list[str], batch_size: int = 64) -> np.ndarray:
    if not texts:
        return np.zeros((0, DIM), dtype=np.float32)
    vecs = np.array(list(_model().embed(texts, batch_size=batch_size)), dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12
    return vecs


def embed_query(q: str) -> np.ndarray:
    return embed([QUERY_PREFIX + q])[0]


def quantize(vecs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-vector symmetric int8. Returns (int8 matrix, float32 scales) with v ~= q * scale."""
    scales = np.abs(vecs).max(axis=1) / 127.0
    scales[scales == 0] = 1.0
    q = np.clip(np.round(vecs / scales[:, None]), -127, 127).astype(np.int8)
    return q, scales.astype(np.float32)


def b64(q_row: np.ndarray) -> str:
    return base64.b64encode(q_row.tobytes()).decode("ascii")
