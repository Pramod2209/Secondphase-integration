from __future__ import annotations

import os

import psutil
from rich.console import Console
from rich.table import Table

from .schema import SystemResources


def detect_and_tune() -> SystemResources:
    cpu_cores = max(os.cpu_count() or 1, 1)
    available_ram_gb = psutil.virtual_memory().available / (1024 ** 3)

    thread_workers = min(cpu_cores * 2, 16)
    process_workers = max(cpu_cores - 1, 2)

    if available_ram_gb >= 16:
        embedding_batch_size = 128
    elif available_ram_gb >= 8:
        embedding_batch_size = 64
    else:
        embedding_batch_size = 32

    if cpu_cores >= 8:
        batch_size = 100
    elif cpu_cores >= 4:
        batch_size = 50
    else:
        batch_size = 25

    max_concurrent_batches = max(cpu_cores // 2, 2)

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
