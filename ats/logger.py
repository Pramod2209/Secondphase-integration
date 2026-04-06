from __future__ import annotations

import logging
import time
from datetime import datetime

from rich.logging import RichHandler

from . import config


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    log_format = "[%(levelname)s] [%(asctime)s] [%(name)s] %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    console_handler = RichHandler(rich_tracebacks=True, show_path=False)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))

    log_file = config.LOG_DIR / f"ats_{datetime.now().strftime('%Y%m%d')}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger


class Timer:
    def __enter__(self) -> "Timer":
        self.start = time.perf_counter()
        self.elapsed = 0.0
        return self

    def __exit__(self, *args: object) -> None:
        self.elapsed = time.perf_counter() - self.start
