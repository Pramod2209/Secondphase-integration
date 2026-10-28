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
import zipfile
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
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
                   min_experience_years: str, required_education: str, reference_date: str = ""):
    try:
        return build_job_profile(title=title, description=description, required_skills=required_skills,
                                 preferred_skills=preferred_skills, min_experience_years=min_experience_years,
                                 required_education=required_education, reference_date=reference_date)
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


async def _save_upload(upload: UploadFile, target: Path,
                       size_limit_bytes: Optional[int] = None) -> Tuple[bool, int]:
    """Stream an upload to disk. Returns (within_size_limit, bytes_written).
    size_limit_bytes defaults to MAX_FILE_SIZE_MB; pass a larger value for ZIPs."""
    if size_limit_bytes is None:
        size_limit_bytes = config.MAX_FILE_SIZE_MB * 1024 * 1024
    written = 0
    with open(target, "wb") as fh:
        while True:
            chunk = await upload.read(_CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > size_limit_bytes:
                break
            fh.write(chunk)
    if written > size_limit_bytes:
        target.unlink(missing_ok=True)
        return False, written
    return True, written


_RESUME_SUFFIXES = {".pdf", ".docx", ".doc", ".txt"}
_IGNORED_ZIP_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}


# Max size for a ZIP upload and for a single resume file extracted from a ZIP.
_ZIP_SIZE_LIMIT = 500 * 1024 * 1024  # 500 MB
_ZIP_MEMBER_SIZE_LIMIT = _ZIP_SIZE_LIMIT


def _extract_zip(zip_path: Path, extract_dir: Path, zip_display: str) -> Tuple[List[Tuple[str, str]], list]:
    """Extract a ZIP and return (resume_files, rejected).

    - Non-resume entries are silently skipped.
    - Members are streamed out in 1 MB chunks so RAM usage is flat regardless of file size.
    - The per-member limit is _ZIP_MEMBER_SIZE_LIMIT (defaults to MAX_REQUEST_SIZE_MB, i.e. 2 GB).
    """
    from .orchestrator import _build_result
    from .worker import FileAnalysis

    files: List[Tuple[str, str]] = []
    rejected = []
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            for idx, member in enumerate(members):
                raw_name = Path(member.filename).name
                if (not raw_name
                        or raw_name.lower() in _IGNORED_ZIP_NAMES
                        or raw_name.startswith(".")
                        or raw_name.startswith("~$")):
                    continue
                suffix = Path(raw_name).suffix.lower()
                if suffix not in _RESUME_SUFFIXES:
                    continue  # skip non-resume files silently

                display = f"{zip_display}/{raw_name}"
                safe_name = re.sub(r"[^\w.\-]", "_", raw_name)
                dest = extract_dir / safe_name
                # Avoid collisions within the same ZIP
                counter = 1
                while dest.exists():
                    dest = extract_dir / f"{Path(safe_name).stem}_{counter}{suffix}"
                    counter += 1

                # Stream-extract in chunks to avoid loading the whole file into RAM
                written = 0
                oversized = False
                try:
                    with zf.open(member) as src, open(dest, "wb") as dst:
                        while True:
                            buf = src.read(_CHUNK)
                            if not buf:
                                break
                            written += len(buf)
                            if written > _ZIP_MEMBER_SIZE_LIMIT:
                                oversized = True
                                break
                            dst.write(buf)
                except Exception as exc:
                    dest.unlink(missing_ok=True)
                    analysis = FileAnalysis(index=idx, display_path=display, file_name=raw_name, ok=False,
                                           error=f"extract_error: {exc}")
                    rejected.append(_build_result(analysis, "failed", None, None, None, False, 0.0))  # type: ignore[arg-type]
                    continue

                if oversized:
                    dest.unlink(missing_ok=True)
                    limit_mb = _ZIP_MEMBER_SIZE_LIMIT // (1024 * 1024)
                    analysis = FileAnalysis(index=idx, display_path=display, file_name=raw_name, ok=False,
                                           error=f"file_too_large: exceeds {limit_mb} MB limit")
                    rejected.append(_build_result(analysis, "failed", None, None, None, False, 0.0))  # type: ignore[arg-type]
                    continue

                files.append((str(dest), display))

    except zipfile.BadZipFile:
        analysis = FileAnalysis(index=0, display_path=zip_display, file_name=zip_display, ok=False,
                               error="bad_zip: not a valid ZIP archive")
        rejected.append(_build_result(analysis, "failed", None, None, None, False, 0.0))  # type: ignore[arg-type]
    return files, rejected


async def _store_uploads(uploads: List[UploadFile], temp_dir: Path):
    """Save uploads under generated names (never the client's path). Returns (files, rejected).
    ZIP files are transparently extracted; resume files inside are queued for screening."""
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
        ext = suffix if re.fullmatch(r'\.[a-z0-9]+', suffix or '') else ''
        target = temp_dir / f"{index:05d}{ext}"
        ok, size = await _save_upload(
            upload, target,
            # ZIPs get their own generous limit (500 MB) so large archives aren't rejected before extraction
            size_limit_bytes=_ZIP_SIZE_LIMIT if ext == ".zip" else None,
        )
        if not ok:
            analysis = FileAnalysis(index=index, display_path=display, file_name=display, ok=False,
                                    error=f"file_too_large: exceeds {config.MAX_FILE_SIZE_MB} MB limit")
            rejected.append(_build_result(analysis, "failed", None, None, None, False, 0.0))  # type: ignore[arg-type]
            continue
        if ext == ".zip":
            # Extract ZIP contents into a subfolder and collect resume files
            zip_extract_dir = temp_dir / f"{index:05d}_zip"
            zip_extract_dir.mkdir(exist_ok=True)
            zfiles, zrejected = _extract_zip(target, zip_extract_dir, display)
            files.extend(zfiles)
            rejected.extend(zrejected)
            target.unlink(missing_ok=True)  # remove the raw ZIP; no longer needed
        else:
            files.append((str(target), display))
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
        if re.fullmatch(r"[a-z0-9]{6,14}-[a-z0-9]{4,14}", request_id):
            return {"stage": "uploading", "analyzed": 0, "total": 0}
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
    reference_date: str = Form(""),
    debug: bool = Form(False),
) -> Any:
    if not resume.filename:
        raise HTTPException(status_code=400, detail="No resume provided")
    job = _job_from_form(title, description, required_skills, preferred_skills, min_experience_years,
                         required_education, reference_date)
    display = safe_display_name(resume.filename)
    async with _semaphore():
        with tempfile.TemporaryDirectory(prefix="ats_single_api_") as temp_dir:
            suffix = Path(display).suffix.lower()
            ext = suffix if re.fullmatch(r'\.[a-z0-9]+', suffix or '') else ''
            target = Path(temp_dir) / f"resume{ext}"
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
    reference_date: str = Form(""),
    top_n: str = Form("10"),
    request_id: str = Form(""),
) -> Any:
    if not resumes:
        raise HTTPException(status_code=400, detail="No resumes provided")
    if len(resumes) > config.MAX_FILES_PER_REQUEST:
        raise HTTPException(status_code=413, detail=f"Too many files (max {config.MAX_FILES_PER_REQUEST} per request)")
    job = _job_from_form(title, description, required_skills, preferred_skills, min_experience_years,
                         required_education, reference_date)
    top = _validate_top_n(top_n)
    key = request_id.strip()[:64] or None
    if key:
        from .orchestrator import _progress
        _progress(key, total=len(resumes), stage="uploading")
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
    reference_date: str = Form(""),
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
                         required_education, reference_date)
    top = _validate_top_n(top_n)
    files = list_resume_files(str(folder))
    if not files:
        raise HTTPException(status_code=400, detail="No files found in folder")
    async with _semaphore():
        output = await screen_files(files, job, top_n=top, progress_key=request_id.strip()[:64] or None)
    return _bulk_response(output)


# ----------------------------------------------------------------------------- Resume Extractor Endpoints


async def _extract_upload_batch(files: List[UploadFile], temp_dir: Path) -> List[Any]:
    """Save uploads preserving original filenames and extract ZIPs cleanly. Returns list of (file_path, display_name)."""
    candidate_files: List[Any] = []
    for idx, upload in enumerate(files):
        raw_name = safe_display_name(upload.filename)
        suffix = Path(raw_name).suffix.lower()
        dest = temp_dir / f"upload_{idx:05d}{suffix}"

        ok, _ = await _save_upload(upload, dest, size_limit_bytes=_ZIP_SIZE_LIMIT)
        if not ok:
            continue

        if suffix == ".zip":
            unzip_folder = temp_dir / f"unpacked_{idx:05d}"
            unzip_folder.mkdir(parents=True, exist_ok=True)
            try:
                with zipfile.ZipFile(dest, "r") as zf:
                    members = [m for m in zf.infolist() if not m.is_dir()]
                    for m_idx, member in enumerate(members):
                        member_name = Path(member.filename).name
                        if (not member_name
                                or member_name.lower() in _IGNORED_ZIP_NAMES
                                or member_name.startswith(".")
                                or member_name.startswith("~$")):
                            continue
                        m_ext = Path(member_name).suffix.lower()
                        if m_ext not in _RESUME_SUFFIXES:
                            continue
                        safe_stem = re.sub(r"[^\w.\-]", "_", Path(member_name).stem)[:80] or f"doc_{m_idx}"
                        member_dest = unzip_folder / f"{m_idx:05d}_{safe_stem}{m_ext}"
                        try:
                            with zf.open(member) as src, open(member_dest, "wb") as dst:
                                while True:
                                    buf = src.read(_CHUNK)
                                    if not buf:
                                        break
                                    dst.write(buf)
                            candidate_files.append((str(member_dest), member_name))
                        except Exception as m_exc:
                            logger.warning("Could not extract member %s from %s: %s", member_name, raw_name, m_exc)
            except Exception as exc:
                logger.warning("Could not unzip %s: %s", raw_name, exc)
            finally:
                dest.unlink(missing_ok=True)
        elif suffix in _RESUME_SUFFIXES:
            candidate_files.append((str(dest), raw_name))

    return candidate_files


@app.post("/api/extract")
async def extract_candidates(
    files: List[UploadFile] = File(...),
    request_id: str = Form(""),
) -> Any:
    """Extract structured candidate profile details from resumes and/or ZIP archives."""
    from time import perf_counter
    from .data_extractor import process_all as extract_process_all, FINAL_HEADERS
    from .orchestrator import _progress

    if not files:
        raise HTTPException(status_code=400, detail="No files provided for extraction")
    if len(files) > config.MAX_FILES_PER_REQUEST:
        raise HTTPException(status_code=413, detail=f"Too many files (max {config.MAX_FILES_PER_REQUEST} per request)")

    t0 = perf_counter()
    if request_id:
        _progress(request_id, total=len(files), analyzed=0, stage="uploading")

    async with _semaphore():
        with tempfile.TemporaryDirectory(prefix="ats_extract_api_") as temp_dir:
            candidate_files = await _extract_upload_batch(files, Path(temp_dir))
            total_cand = len(candidate_files)
            logger.info("Extractor API start: files=%s request_id=%s", total_cand, request_id or "-")
            if request_id:
                _progress(request_id, total=total_cand, analyzed=0, stage="extracting")

            def on_prog(done, total):
                if request_id:
                    _progress(request_id, total=total, analyzed=done, stage="extracting")

            loop = asyncio.get_running_loop()
            records = await loop.run_in_executor(None, extract_process_all, candidate_files, on_prog)

    elapsed = round(perf_counter() - t0, 2)
    logger.info("Extractor API complete: extracted=%s time=%ss", len(records), elapsed)
    if request_id:
        _progress(request_id, total=len(records), analyzed=len(records), stage="completed")

    return {
        "status": "ok",
        "total_extracted": len(records),
        "headers": FINAL_HEADERS,
        "records": records,
    }


@app.post("/api/extract-folder")
async def extract_folder_candidates(
    folder_path: str = Form(...),
    request_id: str = Form(""),
) -> Any:
    """Extract candidate records directly from a local folder on this machine."""
    from time import perf_counter
    from .data_extractor import extract_from_folder, FINAL_HEADERS
    from .orchestrator import _progress

    folder = Path(folder_path.strip().strip('"').strip("'"))
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Folder not found: {folder_path}")

    t0 = perf_counter()
    if request_id:
        _progress(request_id, total=0, analyzed=0, stage="scanning_folder")

    def on_prog(done, total):
        if request_id:
            _progress(request_id, total=total, analyzed=done, stage="extracting")

    logger.info("Extractor folder start: folder=%s request_id=%s", folder, request_id or "-")
    loop = asyncio.get_running_loop()
    records = await loop.run_in_executor(None, extract_from_folder, str(folder), on_prog)

    elapsed = round(perf_counter() - t0, 2)
    logger.info("Extractor folder complete: extracted=%s time=%ss", len(records), elapsed)
    if request_id:
        _progress(request_id, total=len(records), analyzed=len(records), stage="completed")

    return {
        "status": "ok",
        "total_extracted": len(records),
        "headers": FINAL_HEADERS,
        "records": records,
    }


@app.post("/api/extract/excel")
async def extract_and_download_excel(
    files: List[UploadFile] = File(...),
    request_id: str = Form(""),
) -> Any:
    """Extract resumes and return a professionally formatted .xlsx file directly."""
    from time import perf_counter
    from .data_extractor import process_all as extract_process_all, build_excel_bytes
    from .orchestrator import _progress

    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    t0 = perf_counter()
    if request_id:
        _progress(request_id, total=len(files), analyzed=0, stage="uploading")

    async with _semaphore():
        with tempfile.TemporaryDirectory(prefix="ats_extract_excel_") as temp_dir:
            candidate_files = await _extract_upload_batch(files, Path(temp_dir))
            total_cand = len(candidate_files)
            logger.info("Extractor Excel start: files=%s request_id=%s", total_cand, request_id or "-")
            if request_id:
                _progress(request_id, total=total_cand, analyzed=0, stage="extracting")

            def on_prog(done, total):
                if request_id:
                    _progress(request_id, total=total, analyzed=done, stage="extracting")

            loop = asyncio.get_running_loop()
            records = await loop.run_in_executor(None, extract_process_all, candidate_files, on_prog)

            if request_id:
                _progress(request_id, total=len(records), analyzed=len(records), stage="formatting_excel")

            excel_bytes = await loop.run_in_executor(None, build_excel_bytes, records)

    elapsed = round(perf_counter() - t0, 2)
    logger.info("Extractor Excel complete: extracted=%s time=%ss", len(records), elapsed)
    if request_id:
        _progress(request_id, total=len(records), analyzed=len(records), stage="completed")

    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="extracted_resumes.xlsx"'},
    )


@app.post("/api/extract/download-excel")
async def download_excel_from_records(
    request: Request,
) -> Any:
    """Accept extracted candidate records as JSON and return styled .xlsx bytes."""
    from .data_extractor import build_excel_bytes

    try:
        body = await request.json()
        records = body.get("records", []) if isinstance(body, dict) else (body if isinstance(body, list) else [])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}")

    excel_bytes = await asyncio.get_running_loop().run_in_executor(None, build_excel_bytes, records)
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="extracted_resumes.xlsx"'},
    )
