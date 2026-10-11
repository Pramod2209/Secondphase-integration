from __future__ import annotations

import os

import psutil

from .schema import SystemResources

# Approximate resident memory of one parser worker process (pdfminer + python-docx, no torch).
_PARSE_WORKER_MB = 120
_RESERVED_MB = 900  # main process incl. MiniLM model + headroom


def _env_int(*names: str) -> int | None:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            try:
                return max(1, int(value))
            except ValueError:
                pass
    return None


def detect_and_tune() -> SystemResources:
    """Pick worker counts from CPU and free RAM. Parsing (pure-Python pdfminer) is the bottleneck, so it
    runs in separate processes; embeddings run in the main process using torch's own threads."""
    cpu_cores = max(os.cpu_count() or 1, 1)
    available_ram_gb = psutil.virtual_memory().available / (1024 ** 3)

    by_ram = int((available_ram_gb * 1024 - _RESERVED_MB) // _PARSE_WORKER_MB)
    default_workers = max(1, min(cpu_cores - 1, 16, by_ram))
    parse_workers = _env_int("ATS_PARSE_WORKERS", "ATS_PROCESS_WORKERS") or default_workers

    embedding_batch_size = _env_int("ATS_EMBEDDING_BATCH_SIZE") or 32
    embedding_threads = _env_int("ATS_EMBEDDING_THREADS") or max(1, min(8, cpu_cores - parse_workers, cpu_cores // 2))

    return SystemResources(
        cpu_cores=cpu_cores,
        available_ram_gb=round(available_ram_gb, 2),
        parse_workers=parse_workers,
        embedding_batch_size=embedding_batch_size,
        embedding_threads=embedding_threads,
    )


def describe(resources: SystemResources) -> str:
    return (f"cpu={resources.cpu_cores} free_ram={resources.available_ram_gb:.1f}GB "
            f"parse_workers={resources.parse_workers} embed_batch={resources.embedding_batch_size} "
            f"embed_threads={resources.embedding_threads}")
