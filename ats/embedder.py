from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from sentence_transformers import SentenceTransformer
import torch

from . import config
from .logger import get_logger

logger = get_logger(__name__)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text[:2000].encode("utf-8", errors="ignore")).hexdigest()[:16]


class Embedder:
    _model: SentenceTransformer | None = None
    _active_device: str | None = None

    def __init__(self, batch_size: int) -> None:
        self.batch_size = batch_size
        self.cache_dir = config.CACHE_DIR / "embeddings"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_model(cls) -> SentenceTransformer:
        if cls._model is None:
            device = config.EMBEDDING_DEVICE
            if device == "cuda" and not torch.cuda.is_available():
                logger.warning("CUDA requested but not available — falling back to CPU")
                device = "cpu"

            cls._model = SentenceTransformer(config.EMBEDDING_MODEL, device=device)
            cls._active_device = device
        return cls._model

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.npy"

    def encode_batch(self, texts: List[str]) -> Tuple[np.ndarray, int]:
        if not texts:
            return np.empty((0, 384), dtype=np.float32), 0

        keys = [_text_hash(text) for text in texts]
        result_map: Dict[int, np.ndarray] = {}
        uncached_indices: List[int] = []
        uncached_texts: List[str] = []
        uncached_keys: List[str] = []
        cache_hits = 0

        for idx, (key, text) in enumerate(zip(keys, texts)):
            cache_file = self._cache_path(key)
            if cache_file.exists():
                result_map[idx] = np.load(cache_file)
                cache_hits += 1
            else:
                uncached_indices.append(idx)
                uncached_texts.append(text)
                uncached_keys.append(key)

        if uncached_texts:
            model = self.get_model()
            fresh = model.encode(
                uncached_texts,
                batch_size=self.batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
            for pos, emb in enumerate(fresh):
                idx = uncached_indices[pos]
                key = uncached_keys[pos]
                result_map[idx] = emb.astype(np.float32)
                np.save(self._cache_path(key), result_map[idx])

        ordered = np.array([result_map[i] for i in range(len(texts))], dtype=np.float32)
        return ordered, cache_hits

    def encode_single(self, text: str) -> Tuple[np.ndarray, bool]:
        key = _text_hash(text)
        cache_file = self._cache_path(key)
        if cache_file.exists():
            return np.load(cache_file), True

        arr, _ = self.encode_batch([text])
        return arr[0], False


def encode_batch_worker(texts: List[str], batch_size: int) -> Tuple[np.ndarray, int]:
    return Embedder(batch_size=batch_size).encode_batch(texts)


def encode_single_worker(text: str, batch_size: int) -> Tuple[np.ndarray, bool]:
    return Embedder(batch_size=batch_size).encode_single(text)
