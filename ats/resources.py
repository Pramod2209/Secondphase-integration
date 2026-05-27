from __future__ import annotations

import os

import psutil
from rich.console import Console
from rich.table import Table

from .schema import SystemResources


def detect_and_tune() -> SystemResources:
    cpu_cores = max(os.cpu_count() or 1, 1)
    available_ram_gb = psutil.virtual_memory().available / (1024 ** 3)

    # Low-RAM systems should prioritize fewer workers to avoid memory pressure.
    if available_ram_gb < 4:
        default_thread_workers = min(max(cpu_cores, 8), 24)
        default_process_workers = 2
    elif available_ram_gb < 8:
        default_thread_workers = min(max(cpu_cores, 12), 32)
        default_process_workers = min(max(cpu_cores // 4, 2), 4)
    else:
        default_thread_workers = min(cpu_cores * 2, 64)
        default_process_workers = min(max(cpu_cores // 2, 2), 12)

    # Allow environment overrides for experimentation and tuning
    thread_workers = int(os.environ.get("ATS_THREAD_WORKERS", default_thread_workers))
    process_workers = int(os.environ.get("ATS_PROCESS_WORKERS", default_process_workers))

    # Aggressive embedding batch sizes when RAM is available; fall back for low-memory systems
    if os.environ.get("ATS_EMBEDDING_BATCH_SIZE"):
        embedding_batch_size = int(os.environ.get("ATS_EMBEDDING_BATCH_SIZE"))
    else:
        if available_ram_gb >= 32:
            embedding_batch_size = 256
        elif available_ram_gb >= 16:
            embedding_batch_size = 128
        elif available_ram_gb >= 8:
            embedding_batch_size = 64
        elif available_ram_gb >= 4:
            embedding_batch_size = 32
        else:
            embedding_batch_size = 16

    # Larger batch sizes on many cores; keep moderate defaults for low RAM.
    if os.environ.get("ATS_BATCH_SIZE"):
        batch_size = int(os.environ.get("ATS_BATCH_SIZE"))
    else:
        if available_ram_gb < 4:
            batch_size = 120
        elif cpu_cores >= 32:
            batch_size = 300
        elif cpu_cores >= 16:
            batch_size = 200
        elif cpu_cores >= 8:
            batch_size = 150
        elif cpu_cores >= 4:
            batch_size = 100
        else:
            batch_size = 50

    # Number of batches to run concurrently. Higher on many-core machines but bounded.
    if os.environ.get("ATS_MAX_CONCURRENT_BATCHES"):
        max_concurrent_batches = int(os.environ.get("ATS_MAX_CONCURRENT_BATCHES"))
    else:
        max_concurrent_batches = min(max(cpu_cores // 3, 2), 12)
        # if RAM is very low, be conservative
        if available_ram_gb < 4:
            max_concurrent_batches = min(max_concurrent_batches, 4)

    tuned = SystemResources(
        cpu_cores=cpu_cores,
        available_ram_gb=round(available_ram_gb, 2),
        batch_size=batch_size,
        max_concurrent_batches=max_concurrent_batches,
        thread_workers=thread_workers,
        process_workers=process_workers,
        embedding_batch_size=embedding_batch_size,
    )

    table = Table(title="System Resources Detected")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("CPU Cores", str(tuned.cpu_cores))
    table.add_row("Available RAM (GB)", f"{tuned.available_ram_gb:.2f}")
    table.add_row("Batch Size", str(tuned.batch_size))
    table.add_row("Max Concurrent Batches", str(tuned.max_concurrent_batches))
    table.add_row("Thread Workers", str(tuned.thread_workers))
    table.add_row("Process Workers", str(tuned.process_workers))
    table.add_row("Embedding Batch Size", str(tuned.embedding_batch_size))
    Console().print(table)

    return tuned
