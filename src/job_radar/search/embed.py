"""Embedding models behind one tiny interface."""

from __future__ import annotations

import hashlib
import re
from typing import Literal, Protocol

import numpy as np

Kind = Literal["query", "passage"]

MODELS = {
    # multilingual (job ads are German and English), small enough for a laptop CPU
    "minilm": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "e5": "intfloat/multilingual-e5-small",
}
DEFAULT_MODEL = "e5"


class Embedder(Protocol):
    name: str
    dim: int

    def encode(self, texts: list[str], kind: Kind) -> np.ndarray: ...


def l2_normalise(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


class STEmbedder:
    """sentence-transformers model. e5 models need 'query: ' / 'passage: ' prefixes."""

    def __init__(self, key: str = DEFAULT_MODEL, batch_size: int = 32) -> None:
        from sentence_transformers import SentenceTransformer

        self.name = key
        self.model_id = MODELS.get(key, key)
        self._model = SentenceTransformer(self.model_id, device="cpu")
        dim_fn = getattr(self._model, "get_embedding_dimension", None) or self._model.get_sentence_embedding_dimension
        self.dim = int(dim_fn())
        self.batch_size = batch_size
        self._prefix = "e5" in self.model_id

    def encode(self, texts: list[str], kind: Kind) -> np.ndarray:
        if self._prefix:
            texts = [f"{kind}: {t}" for t in texts]
        vecs = self._model.encode(texts, batch_size=self.batch_size, show_progress_bar=len(texts) > 200,
                                  convert_to_numpy=True, normalize_embeddings=True)
        return vecs.astype(np.float32)


class HashingEmbedder:
    """Deterministic bag-of-words hashing embedder. For tests and offline smoke runs only."""

    def __init__(self, dim: int = 256) -> None:
        self.name = "hashing"
        self.dim = dim

    def encode(self, texts: list[str], kind: Kind) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"[a-zäöüß0-9+#]+", t.lower()):
                h = int(hashlib.md5(w.encode()).hexdigest(), 16)
                out[i, h % self.dim] += 1.0 if (h >> 64) & 1 else -1.0
        return l2_normalise(out)


def get_embedder(key: str = DEFAULT_MODEL) -> Embedder:
    if key == "hashing":
        return HashingEmbedder()
    return STEmbedder(key)
