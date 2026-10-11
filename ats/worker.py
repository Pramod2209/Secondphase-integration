"""Per-file analysis, executed in parser worker processes (and in-process for single mode).

Only lightweight modules are imported here (no torch), so worker processes stay small.
Everything a resume contributes to scoring is computed here except the embedding vector.
"""
from __future__ import annotations

import hashlib
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import List, Optional

from . import config
from .embedder import semantic_chunks
from .extractor import extract_all
from .matcher import match_job_skills
from .parser import parse_document
from .schema import JobProfile


@dataclass
class FileAnalysis:
    index: int
    display_path: str
    file_name: str
    ok: bool
    error: Optional[str] = None
    content_hash: str = ""
    ocr_used: bool = False
    skills: List[str] = field(default_factory=list)
    experience_years: float = 0.0
    education_level: str = "unknown"
    name: str = ""
    name_source: str = "filename"
    matched_required: List[str] = field(default_factory=list)
    missing_required: List[str] = field(default_factory=list)
    matched_preferred: List[str] = field(default_factory=list)
    preferred_bonus: float = 0.0
    required_credit: float = 0.0
    skill_evidence: List[dict] = field(default_factory=list)
    chunks: List[str] = field(default_factory=list)
    text_preview: str = ""
    parse_time: float = 0.0
    extract_time: float = 0.0


def watch_parent(parent_pid: int, interval: float = 3.0) -> None:
    """Pool initializer: exit this worker when the server process is gone.

    If the server is force-killed (e.g. the launcher's restart step), its atexit pool shutdown never
    runs and idle workers would otherwise linger indefinitely."""
    import os
    import threading
    import time

    import psutil

    def _watch() -> None:
        while True:
            time.sleep(interval)
            if not psutil.pid_exists(parent_pid):
                os._exit(0)

    threading.Thread(target=_watch, name="ats-parent-watch", daemon=True).start()


def ping() -> int:
    """No-op used to start worker processes ahead of the first request."""
    import os

    return os.getpid()


def analyze_file(index: int, path: str, display_path: str, job: JobProfile, want_chunks: bool,
                 preview_chars: int = 0) -> FileAnalysis:
    """Never raises: any failure is returned as ok=False with a reason."""
    file_name = Path(display_path).name
    result = FileAnalysis(index=index, display_path=display_path, file_name=file_name, ok=False)
    try:
        p = Path(path)
        size = p.stat().st_size
        if size > config.MAX_FILE_SIZE_MB * 1024 * 1024:
            result.error = f"file_too_large: {size / 1024 / 1024:.1f} MB exceeds {config.MAX_FILE_SIZE_MB} MB limit"
            return result
        result.content_hash = hashlib.sha256(p.read_bytes()).hexdigest()[:16]

        parsed = parse_document(path)
        result.parse_time = parsed.elapsed
        result.ocr_used = parsed.ocr_used
        if parsed.error:
            result.error = parsed.error
            return result

        start = perf_counter()
        features = extract_all(parsed.text, display_text=parsed.display_text, file_name=file_name)
        match = match_job_skills(parsed.text, job, features.skill_anchors)
        result.skills = features.skills
        result.experience_years = features.experience_years
        result.education_level = features.education_level
        result.name = features.name
        result.name_source = features.name_source
        result.matched_required, result.missing_required = match.matched, match.missing
        result.matched_preferred, result.preferred_bonus = match.matched_preferred, match.bonus
        result.required_credit, result.skill_evidence = match.required_credit, match.evidence
        if want_chunks:
            result.chunks = semantic_chunks(parsed.text)
        if preview_chars:
            result.text_preview = parsed.text[:preview_chars]
        result.extract_time = perf_counter() - start
        result.ok = True
        return result
    except Exception as exc:  # defensive: one bad file must never break a batch
        result.ok = False
        result.error = f"internal_error: {type(exc).__name__}: {str(exc)[:200]}"
        result.text_preview = ""
        tb = traceback.format_exc(limit=3)
        from .logger import get_logger

        get_logger(__name__).warning("Analysis failed for %s: %s", file_name, tb)
        return result
