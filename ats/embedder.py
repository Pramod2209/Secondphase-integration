"""MiniLM sentence embeddings with a versioned on-disk cache.

* The model is loaded once per process (lazily) and shared; it is never copied into workers.
* all-MiniLM-L6-v2 reads at most 256 word-pieces per input, so a resume is reduced to its most
  relevant sections (summary, skills, experience first) and split into up to
  EMBEDDING_MAX_CHUNKS chunks; the resume vector is the normalized mean of the chunk vectors.
* Cache files hold only vectors, keyed by model + version + chunk text hash.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

from . import config
from .logger import get_logger

logger = get_logger(__name__)

EMBEDDING_DIM = 384
_SECTION_PRIORITY = {"summary": 0, "skills": 1, "experience": 2, "unknown": 3, "projects": 4, "internship": 5,
                     "education": 6}


class EmbeddingUnavailable(RuntimeError):
    """The embedding model could not be loaded (e.g. offline and not cached)."""


def semantic_chunks(text: str, max_chunks: Optional[int] = None, chunk_words: Optional[int] = None) -> List[str]:
    """Section-prioritized word chunks of a resume for embedding (personal details etc. dropped)."""
    from .extractor import split_sections

    max_chunks = max_chunks or config.EMBEDDING_MAX_CHUNKS
    chunk_words = chunk_words or config.EMBEDDING_CHUNK_WORDS
    lines = split_sections(text or "")
    ordered = sorted(
        (i for i, (sec, _) in enumerate(lines) if sec in _SECTION_PRIORITY),
        key=lambda i: (_SECTION_PRIORITY[lines[i][0]], i),
    )
    words: List[str] = []
    budget = max_chunks * chunk_words
    for i in ordered:
        words.extend(lines[i][1].split())
        if len(words) >= budget:
            break
    if not words:  # nothing classified: fall back to the raw text
        words = (text or "").split()[:budget]
    words = words[:budget]
    return [" ".join(words[i:i + chunk_words]) for i in range(0, len(words), chunk_words)] or []


def job_text(title: str, description: str, required: Sequence[str], preferred: Sequence[str]) -> str:
    """Text the job vector is built from: title + description. Skills are added only when there is
    no description, because skill overlap is already scored separately (40%) and would be double-counted."""
    parts = [title.strip(), description.strip()]
    if not description.strip() and (required or preferred):
        parts.append("Skills: " + ", ".join([*required, *preferred]))
    return ". ".join(p for p in parts if p)


def _cache_key(chunks: Sequence[str]) -> str:
    payload = "\x1f".join([config.EMBEDDING_VERSION, config.EMBEDDING_MODEL, *chunks])
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()[:24]


class Embedder:
    _model = None
    _active_device: Optional[str] = None
    _lock = threading.Lock()

    def __init__(self, batch_size: int = 32) -> None:
        self.batch_size = max(1, int(batch_size))
        self.cache_dir = config.CACHE_DIR / "embeddings"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_model(cls):
        if cls._model is not None:
            return cls._model
        with cls._lock:
            if cls._model is not None:
                return cls._model
            try:
                import torch
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingUnavailable(f"sentence-transformers not installed: {exc}") from exc
            device = config.EMBEDDING_DEVICE
            if device == "cuda" and not torch.cuda.is_available():
                logger.warning("CUDA requested but not available - falling back to CPU")
                device = "cpu"
            try:  # prefer the local copy: no network access when the model is already cached
                cls._model = SentenceTransformer(config.EMBEDDING_MODEL, device=device, local_files_only=True)
            except Exception:
                try:
                    cls._model = SentenceTransformer(config.EMBEDDING_MODEL, device=device)
                except Exception as exc:
                    raise EmbeddingUnavailable(f"could not load {config.EMBEDDING_MODEL}: {exc}") from exc
            cls._active_device = device
            return cls._model

    @staticmethod
    def set_threads(n: int) -> None:
        try:
            import torch

            torch.set_num_threads(max(1, int(n)))
        except Exception:
            pass

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.npy"

    def _load_cached(self, key: str) -> Optional[np.ndarray]:
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            arr = np.load(path)
            return arr if arr.shape == (EMBEDDING_DIM,) and np.all(np.isfinite(arr)) else None
        except Exception:
            return None

    def _save_cached(self, key: str, vector: np.ndarray) -> None:
        path = self._cache_path(key)
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with open(tmp, "wb") as fh:
                np.save(fh, vector)
            os.replace(tmp, path)
        except Exception:
            tmp.unlink(missing_ok=True)

    def embed_documents(self, docs: Sequence[Sequence[str]]) -> Tuple[List[Optional[np.ndarray]], int]:
        """Embed documents given as chunk lists. Returns (vectors, cache_hits); empty docs -> None."""
        results: List[Optional[np.ndarray]] = [None] * len(docs)
        keys = [_cache_key(chunks) if chunks else "" for chunks in docs]
        pending: List[int] = []
        hits = 0
        for i, key in enumerate(keys):
            if not key:
                continue
            cached = self._load_cached(key)
            if cached is not None:
                results[i] = cached
                hits += 1
            else:
                pending.append(i)
        if pending:
            model = self.get_model()
            flat: List[str] = []
            owners: List[int] = []
            for i in pending:
                for chunk in docs[i]:
                    flat.append(chunk)
                    owners.append(i)
            vectors = model.encode(flat, batch_size=self.batch_size, show_progress_bar=False,
                                   convert_to_numpy=True, normalize_embeddings=True)
            sums: dict[int, np.ndarray] = {}
            for owner, vec in zip(owners, vectors):
                sums[owner] = sums.get(owner, 0) + vec.astype(np.float32)
            for i in pending:
                vec = sums[i]
                norm = float(np.linalg.norm(vec))
                if norm > 0 and np.isfinite(norm):
                    vec = (vec / norm).astype(np.float32)
                    results[i] = vec
                    self._save_cached(keys[i], vec)
        return results, hits

    def embed_text(self, text: str, chunk_words: Optional[int] = None) -> Optional[np.ndarray]:
        words = (text or "").split()
        if not words:
            return None
        size = chunk_words or config.EMBEDDING_CHUNK_WORDS
        chunks = [" ".join(words[i:i + size]) for i in range(0, len(words), size)][: config.EMBEDDING_MAX_CHUNKS]
        return self.embed_documents([chunks])[0][0]
