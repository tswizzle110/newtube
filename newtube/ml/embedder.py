"""Turn text into vectors so "what is this video about" can be compared.

Two embedders behind one interface:

* ``HashingEmbedder``: no model, no dependencies. Hashes words (and word pairs) into a fixed-size vector, so videos
  that share vocabulary are close. Crude, but it works from the first minute, and it is what the tests use.
* ``SentenceTransformerEmbedder``: a real multilingual text model run on the GPU (or CPU). It loads ONLY from a
  folder on this machine and is forced offline, so it can never download anything or phone home. Install the
  libraries with ``uv sync --extra ml`` and put the model folder in ``<data_dir>/models/<embedding_model>``.

Every vector is unit length, so the dot product of two vectors is their cosine similarity.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path
from typing import Protocol

_WORD = re.compile(r"\w+", re.UNICODE)


class ModelNotInstalled(Exception):
    """The model folder or the ML libraries are missing. The message says what to do."""


class Embedder(Protocol):
    name: str          # identifies the vectors; stored with them
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


class HashingEmbedder:
    def __init__(self, dim: int = 256) -> None:
        self.dim, self.name = dim, f"hashing-{dim}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            v = [0.0] * self.dim
            words = [w.lower() for w in _WORD.findall(text)]
            for token in words + [f"{a} {b}" for a, b in zip(words, words[1:])]:
                h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")
                v[h % self.dim] += 1.0 if (h >> 32) & 1 else -1.0         # signed hashing keeps unrelated words apart
            out.append(normalize(v))
        return out


class SentenceTransformerEmbedder:
    def __init__(self, model_dir: Path, device: str = "auto") -> None:
        if not model_dir.is_dir():
            raise ModelNotInstalled(
                f"model folder not found: {model_dir}\n"
                f"Download the model files yourself and copy the folder there (see docs/RUNNING.md, 'Embeddings').")
        # Never reach the internet: this must hold even if some library tries.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        try:
            import torch
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise ModelNotInstalled("the ML libraries are not installed: run `uv sync --extra ml`") from e
        self.device = ("cuda" if torch.cuda.is_available() else "cpu") if device == "auto" else device
        self._model = SentenceTransformer(str(model_dir), device=self.device)
        self.dim = int(self._model.get_sentence_embedding_dimension())
        self.name = f"st-{model_dir.name}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
        return [[float(x) for x in v] for v in vecs]


def load_embedder(settings) -> Embedder:
    """The configured embedder: the built-in hashing one unless ``embedding_model`` names a folder."""
    if not settings.embedding_model:
        return HashingEmbedder()
    return SentenceTransformerEmbedder(settings.data_dir / "models" / settings.embedding_model, settings.embedding_device)
