"""HTTP API for the screening pipeline (FastAPI).

Endpoints
  GET  /api/health                  liveness + capabilities (OCR, .doc support, semantic)
  POST /api/single                  score one uploaded resume
  POST /api/bulk                    score up to ATS_MAX_FILES_PER_REQUEST uploaded resumes
  GET  /api/progress/{request_id}   progress of a running /api/bulk request (client-supplied id)
  POST /api/bulk-parallel-folder    score a folder on this machine; disabled unless
                                    ATS_ALLOWED_FOLDER_ROOTS lists the permitted directories

All POST endpoints require the header "X-ATS-Client" (any value). Browsers cannot send custom
headers cross-origin without a CORS preflight, which is only allowed for the configured UI
origins, so other websites cannot trigger screening runs on this machine.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging

from . import config
from .api import JobValidationError, build_job_profile, list_resume_files, screen_files, screen_single
from .logger import get_logger
from .orchestrator import get_progress
from .parser import _antiword_path, ocr_available
from .resources import describe, detect_and_tune
from .skills import load_taxonomy_index

logger = get_logger(__name__)
CLIENT_HEADER = "x-ats-client"
_CHUNK = 1024 * 1024


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_taxonomy_index()
    for name in ("pdfminer", "pdfminer.high_level", "PIL"):
        logging.getLogger(name).setLevel(logging.ERROR)
    resources = detect_and_tune()
    logger.info("ATS API starting: %s ocr=%s doc=%s", describe(resources), ocr_available(), bool(_antiword_path()))
    loop = asyncio.get_running_loop()
    if config.WARMUP_PARSE_POOL:
        from .orchestrator import warm_pool

        started = await loop.run_in_executor(None, warm_pool, resources.parse_workers)
        logger.info("Parser workers ready: %s", started)
    if config.WARMUP_EMBEDDER:
        from .embedder import Embedder, EmbeddingUnavailable

        try:
            await loop.run_in_executor(None, Embedder.get_model)
        except EmbeddingUnavailable as exc:
            logger.warning("Embedder warmup failed: %s", exc)
    yield


app = FastAPI(title="ATS API", version="2.1.0", lifespan=lifespan)


@app.middleware("http")
async def guard_requests(request: Request, call_next):
    if request.method == "POST":
        if CLIENT_HEADER not in request.headers:
            return JSONResponse(status_code=403, content={"detail": "Missing X-ATS-Client header."})
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > config.MAX_REQUEST_SIZE_MB * 1024 * 1024:
            return JSONResponse(status_code=413,
                                content={"detail": f"Request too large (max {config.MAX_REQUEST_SIZE_MB} MB)."})
    return await call_next(request)


# Added after the guard so CORS is the outermost layer (error responses still carry CORS headers).
_origins = [o.strip() for o in os.environ.get("ATS_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
            if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


_job_semaphore: Optional[asyncio.Semaphore] = None


def _semaphore() -> asyncio.Semaphore:
    global _job_semaphore
    if _job_semaphore is None:
        _job_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_JOBS)
    return _job_semaphore


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)


def _to_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=_json_default))


def safe_display_name(filename: Optional[str]) -> str:
    """Basename of a client-supplied filename with path components and control characters removed."""
    name = re.split(r"[\\/]", filename or "")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip().strip(".")
    return name[:200] or "unnamed"


def _job_from_form(title: str, description: str, required_skills: str, preferred_skills: str,
                   min_experience_years: str, required_education: str):
    try:
        return build_job_profile(title=title, description=description, required_skills=required_skills,
                                 preferred_skills=preferred_skills, min_experience_years=min_experience_years,
                                 required_education=required_education)
    except JobValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _validate_top_n(top_n: str) -> int:
    try:
        value = int(str(top_n).strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="top_n must be a whole number.") from exc
    if not 1 <= value <= 100000:
        raise HTTPException(status_code=422, detail="top_n must be between 1 and 100000.")
    return value


async def _save_upload(upload: UploadFile, target: Path) -> Tuple[bool, int]:
    """Stream an upload to disk. Returns (within_size_limit, bytes_written)."""
    limit = config.MAX_FILE_SIZE_MB * 1024 * 1024
    written = 0
    with open(target, "wb") as fh:
        while True:
            chunk = await upload.read(_CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                break
            fh.write(chunk)
    if written > limit:
        target.unlink(missing_ok=True)
        return False, written
    return True, written


async def _store_uploads(uploads: List[UploadFile], temp_dir: Path):
    """Save uploads under generated names (never the client's path). Returns (files, rejected)."""
    from .orchestrator import _build_result
    from .worker import FileAnalysis

    files: List[Tuple[str, str]] = []
    rejected = []
    seen: dict[str, int] = {}
    for index, upload in enumerate(uploads):
        display = safe_display_name(upload.filename)
        seen[display.lower()] = seen.get(display.lower(), 0) + 1
        if seen[display.lower()] > 1:  # same name uploaded twice: keep both, label the copies
            display = f"{display} [{seen[display.lower()]}]"
        suffix = Path(safe_display_name(upload.filename)).suffix.lower()[:10]
        target = temp_dir / f"{index:05d}{suffix if re.fullmatch(r'\.[a-z0-9]+', suffix or '') else ''}"
        ok, size = await _save_upload(upload, target)
        if ok:
            files.append((str(target), display))
        else:
            analysis = FileAnalysis(index=index, display_path=display, file_name=display, ok=False,
                                    error=f"file_too_large: exceeds {config.MAX_FILE_SIZE_MB} MB limit")
            rejected.append(_build_result(analysis, "failed", None, None, None, False, 0.0))  # type: ignore[arg-type]
    return files, rejected


def _bulk_response(output) -> dict:
    data = _to_jsonable(asdict(output))
    data["execution_summary"] = {
        "duration_seconds": round(output.time_taken_seconds, 2),
        "total_submitted": output.total_submitted,
        "total_processed": output.total_processed,
        "total_filtered": output.total_filtered,
        "total_failed": output.total_failed,
        "throughput_resumes_per_second": round(output.throughput_per_second, 2),
        "semantic_enabled": bool(output.summary_stats.get("semantic_enabled", False)),
    }
    return data


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "ocr_available": ocr_available(),
        "doc_supported": bool(_antiword_path()),
        "semantic_bulk": config.USE_SEMANTIC_EMBEDDING_BULK,
        "prefilter": config.PREFILTER_ENABLED,
        "max_files_per_request": config.MAX_FILES_PER_REQUEST,
        "max_file_size_mb": config.MAX_FILE_SIZE_MB,
        "folder_screening_enabled": bool(config.ALLOWED_FOLDER_ROOTS),
    }


@app.get("/api/progress/{request_id}")
async def progress(request_id: str) -> dict[str, Any]:
    entry = get_progress(request_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown request id")
    return entry


@app.post("/api/single")
async def single_screen(
    resume: UploadFile = File(...),
    title: str = Form(""),
    description: str = Form(""),
    required_skills: str = Form(""),
    preferred_skills: str = Form(""),
    min_experience_years: str = Form("0"),
    required_education: str = Form("any"),
    debug: bool = Form(False),
) -> Any:
    if not resume.filename:
        raise HTTPException(status_code=400, detail="No resume provided")
    job = _job_from_form(title, description, required_skills, preferred_skills, min_experience_years,
                         required_education)
    display = safe_display_name(resume.filename)
    async with _semaphore():
        with tempfile.TemporaryDirectory(prefix="ats_single_api_") as temp_dir:
            suffix = Path(display).suffix.lower()
            target = Path(temp_dir) / f"resume{suffix if re.fullmatch(r'\.[a-z0-9]+', suffix or '') else ''}"
            ok, _ = await _save_upload(resume, target)
            if not ok:
                raise HTTPException(status_code=413, detail=f"File exceeds {config.MAX_FILE_SIZE_MB} MB limit")
            output = await screen_single(resume_path=str(target), job=job, debug=debug, display_path=display)
    return _to_jsonable(asdict(output))


@app.post("/api/bulk")
async def bulk_screen(
    resumes: List[UploadFile] = File(...),
    title: str = Form(""),
    description: str = Form(""),
    required_skills: str = Form(""),
    preferred_skills: str = Form(""),
    min_experience_years: str = Form("0"),
    required_education: str = Form("any"),
    top_n: str = Form("10"),
    request_id: str = Form(""),
) -> Any:
    if not resumes:
        raise HTTPException(status_code=400, detail="No resumes provided")
    if len(resumes) > config.MAX_FILES_PER_REQUEST:
        raise HTTPException(status_code=413, detail=f"Too many files (max {config.MAX_FILES_PER_REQUEST} per request)")
    job = _job_from_form(title, description, required_skills, preferred_skills, min_experience_years,
                         required_education)
    top = _validate_top_n(top_n)
    key = request_id.strip()[:64] or None
    async with _semaphore():
        with tempfile.TemporaryDirectory(prefix="ats_bulk_api_") as temp_dir:
            files, rejected = await _store_uploads(resumes, Path(temp_dir))
            output = await screen_files(files, job, top_n=top, progress_key=key, extra_failures=rejected)
    return _bulk_response(output)


def _folder_allowed(folder: Path) -> bool:
    try:
        resolved = folder.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    for root in config.ALLOWED_FOLDER_ROOTS:
        try:
            if resolved.is_relative_to(Path(root).resolve()):
                return True
        except (OSError, RuntimeError):
            continue
    return False


@app.post("/api/bulk-parallel-folder")
async def bulk_screen_folder(
    folder_path: str = Form(...),
    title: str = Form(""),
    description: str = Form(""),
    required_skills: str = Form(""),
    preferred_skills: str = Form(""),
    min_experience_years: str = Form("0"),
    required_education: str = Form("any"),
    top_n: str = Form("10"),
    request_id: str = Form(""),
) -> Any:
    """Screen a folder on this machine (no upload limits). Only folders inside ATS_ALLOWED_FOLDER_ROOTS."""
    if not config.ALLOWED_FOLDER_ROOTS:
        raise HTTPException(status_code=403, detail="Folder screening is disabled. Set ATS_ALLOWED_FOLDER_ROOTS "
                                                    "to the directories that may be screened.")
    folder = Path(folder_path)
    if not _folder_allowed(folder):
        raise HTTPException(status_code=403, detail="Folder is not inside an allowed root (ATS_ALLOWED_FOLDER_ROOTS).")
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail="Folder not found")
    job = _job_from_form(title, description, required_skills, preferred_skills, min_experience_years,
                         required_education)
    top = _validate_top_n(top_n)
    files = list_resume_files(str(folder))
    if not files:
        raise HTTPException(status_code=400, detail="No files found in folder")
    async with _semaphore():
        output = await screen_files(files, job, top_n=top, progress_key=request_id.strip()[:64] or None)
    return _bulk_response(output)
