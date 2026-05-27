from __future__ import annotations

import asyncio
import json
import tempfile
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
import logging
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from . import config
from .api import build_job_profile, screen_bulk, screen_single
from .embedder import Embedder
from .extractor import load_taxonomy
from .resources import detect_and_tune

app = FastAPI(title="ATS API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middleware to increase upload size limit to 5GB
class MaxUploadSizeMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_upload_size: int):
        super().__init__(app)
        self.max_upload_size = max_upload_size

    async def dispatch(self, request, call_next):
        if request.method == 'POST':
            if 'content-length' in request.headers:
                content_length = int(request.headers['content-length'])
                if content_length > self.max_upload_size:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"File too large. Max size: {self.max_upload_size / (1024**3):.1f}GB"}
                    )
        return await call_next(request)

app.add_middleware(MaxUploadSizeMiddleware, max_upload_size=5 * 1024 * 1024 * 1024)


@app.on_event("startup")
async def warmup() -> None:
    resources = detect_and_tune()
    load_taxonomy()
    if config.WARMUP_EMBEDDER:
        embedder = Embedder(batch_size=resources.embedding_batch_size)
        embedder.encode_single("warmup text")
    # Suppress noisy PDF/font warnings from third-party libs when possible
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    logging.getLogger("pdfminer.high_level").setLevel(logging.ERROR)
    logging.getLogger("PIL").setLevel(logging.ERROR)
    logging.getLogger("reportlab").setLevel(logging.ERROR)


def _split_csv(value: str) -> List[str]:
    return [item.strip().lower() for item in (value or "").split(",") if item.strip()]


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)


def _to_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=_json_default))


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/single")
async def single_screen(
    resume: UploadFile = File(...),
    title: str = Form(...),
    description: str = Form(...),
    required_skills: str = Form(""),
    preferred_skills: str = Form(""),
    min_experience_years: float = Form(0.0),
    required_education: str = Form("any"),
) -> Any:
    if not resume.filename:
        raise HTTPException(status_code=400, detail="No resume provided")

    with tempfile.TemporaryDirectory(prefix="ats_single_api_") as temp_dir:
        temp_path = Path(temp_dir)
        resume_path = temp_path / resume.filename
        resume_path.write_bytes(await resume.read())

        job = build_job_profile(
            title=title,
            description=description,
            required_skills=_split_csv(required_skills),
            preferred_skills=_split_csv(preferred_skills),
            min_experience_years=float(min_experience_years),
            required_education=required_education,
        )

        output = await screen_single(
            resume_path=str(resume_path),
            job=job,
        )
        return _to_jsonable(asdict(output))


@app.post("/api/bulk")
async def bulk_screen(
    resumes: List[UploadFile] = File(...),
    title: str = Form(...),
    description: str = Form(...),
    required_skills: str = Form(""),
    preferred_skills: str = Form(""),
    min_experience_years: float = Form(0.0),
    required_education: str = Form("any"),
    top_n: int = Form(10),
) -> Any:
    if not resumes:
        raise HTTPException(status_code=400, detail="No resumes provided")

    with tempfile.TemporaryDirectory(prefix="ats_bulk_api_") as temp_dir:
        temp_path = Path(temp_dir)
        for upload in resumes:
            if upload.filename:
                (temp_path / upload.filename).write_bytes(await upload.read())

        job = build_job_profile(
            title=title,
            description=description,
            required_skills=_split_csv(required_skills),
            preferred_skills=_split_csv(preferred_skills),
            min_experience_years=float(min_experience_years),
            required_education=required_education,
        )

        output = await screen_bulk(
            resume_folder=str(temp_path),
            job=job,
            top_n=int(top_n),
            output_json_path=None,
        )
        response_data = _to_jsonable(asdict(output))
        response_data["execution_summary"] = {
            "duration_seconds": round(output.time_taken_seconds, 2),
            "total_submitted": output.total_submitted,
            "total_processed": output.total_processed,
            "total_filtered": output.total_filtered,
            "throughput_resumes_per_second": round(output.throughput_per_second, 2),
            "semantic_enabled": bool(output.summary_stats.get("semantic_enabled", False)),
        }
        return response_data


async def _process_batch_folder(
    batch_folder: str,
    job: Any,
    top_n: int,
    batch_id: int,
) -> tuple[Any, float]:
    """Process a single batch folder and return results with timing."""
    batch_start = time.time()
    output = await screen_bulk(
        resume_folder=batch_folder,
        job=job,
        top_n=int(top_n),
        output_json_path=None,
    )
    batch_duration = time.time() - batch_start
    return output, batch_duration


@app.post("/api/bulk-parallel")
async def bulk_screen_parallel(
    resumes: List[UploadFile] = File(...),
    title: str = Form(...),
    description: str = Form(...),
    required_skills: str = Form(""),
    preferred_skills: str = Form(""),
    min_experience_years: float = Form(0.0),
    required_education: str = Form("any"),
    top_n: int = Form(10),
) -> Any:
    """
    Process bulk resumes in parallel batches to optimize throughput.
    Automatically splits large uploads into batches and processes concurrently.
    """
    if not resumes:
        raise HTTPException(status_code=400, detail="No resumes provided")

    overall_start = time.time()

    with tempfile.TemporaryDirectory(prefix="ats_bulk_parallel_") as temp_dir:
        temp_path = Path(temp_dir)
        
        # Write all uploaded files
        for upload in resumes:
            if upload.filename:
                (temp_path / upload.filename).write_bytes(await upload.read())

        # Get all resume files
        resume_files = [
            f for f in temp_path.glob("*")
            if f.is_file() and f.suffix.lower() in {".pdf", ".docx", ".txt"}
        ]
        
        total_files = len(resume_files)
        
        # Determine optimal batch size (max 500 files per batch)
        batch_size = 500
        num_batches = (total_files + batch_size - 1) // batch_size
        
        # Create batch folders
        batch_folders = []
        for batch_id in range(num_batches):
            batch_folder = temp_path / f"batch_{batch_id}"
            batch_folder.mkdir()
            
            start_idx = batch_id * batch_size
            end_idx = min(start_idx + batch_size, total_files)
            batch_files = resume_files[start_idx:end_idx]
            
            # Copy files to batch folder
            import shutil
            for file in batch_files:
                shutil.copy2(file, batch_folder / file.name)
            
            batch_folders.append((batch_folder, batch_id, len(batch_files)))

        # Build job profile
        job = build_job_profile(
            title=title,
            description=description,
            required_skills=_split_csv(required_skills),
            preferred_skills=_split_csv(preferred_skills),
            min_experience_years=float(min_experience_years),
            required_education=required_education,
        )

        # Process all batches in parallel
        batch_tasks = [
            _process_batch_folder(str(batch_folder), job, top_n, batch_id)
            for batch_folder, batch_id, _ in batch_folders
        ]
        batch_results = await asyncio.gather(*batch_tasks)

        # Aggregate results
        all_candidates = []
        total_submitted = 0
        total_processed = 0
        total_filtered = 0
        total_failed = 0
        batch_timings = []

        for idx, (output, duration) in enumerate(batch_results):
            batch_size_actual = batch_folders[idx][2]
            batch_timings.append({
                "batch_id": idx,
                "files_in_batch": batch_size_actual,
                "duration_seconds": round(duration, 2),
                "throughput": round(batch_size_actual / duration, 2) if duration > 0 else 0,
            })
            
            total_submitted += output.total_submitted
            total_processed += output.total_processed
            total_filtered += output.total_filtered
            total_failed += output.total_failed
            
            # Collect top candidates from each batch
            all_candidates.extend(output.top_candidates)

        # Sort all candidates by score and take top N
        all_candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
        top_candidates = all_candidates[:int(top_n)]

        overall_duration = time.time() - overall_start
        throughput = total_processed / overall_duration if overall_duration > 0 else 0

        response_data = {
            "total_submitted": total_submitted,
            "total_processed": total_processed,
            "total_filtered": total_filtered,
            "total_failed": total_failed,
            "top_candidates": _to_jsonable(top_candidates),
            "execution_summary": {
                "duration_seconds": round(overall_duration, 2),
                "total_submitted": total_submitted,
                "total_processed": total_processed,
                "total_filtered": total_filtered,
                "throughput_resumes_per_second": round(throughput, 2),
                "batch_processing": {
                    "num_batches": num_batches,
                    "batch_size": batch_size,
                    "batch_details": batch_timings,
                    "parallelization": "Batches processed concurrently",
                },
            },
        }
        
        return response_data


@app.post("/api/bulk-parallel-folder")
async def bulk_screen_parallel_folder(
    folder_path: str = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    required_skills: str = Form(""),
    preferred_skills: str = Form(""),
    min_experience_years: float = Form(0.0),
    required_education: str = Form("any"),
    top_n: int = Form(10),
) -> Any:
    """
    Process bulk resumes from a folder path in parallel batches.
    Bypasses HTTP multipart limits by reading files directly from disk.
    Perfect for processing 2000+ resume files efficiently.
    """
    import shutil
    
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Folder not found: {folder_path}")

    overall_start = time.time()

    # Get all resume files from the folder recursively
    resume_files = [
        f for f in folder.rglob("*")
        if f.is_file() and f.suffix.lower() in {".pdf", ".docx", ".txt"}
    ]
    resume_files.sort()
    
    total_files = len(resume_files)
    if total_files == 0:
        raise HTTPException(status_code=400, detail="No resume files found in folder")
    
    # Determine optimal batch size (max 300 files per batch for stability)
    batch_size = 300
    num_batches = (total_files + batch_size - 1) // batch_size
    
    print(f"[PARALLEL] Processing {total_files} files in {num_batches} batches of {batch_size}...")
    
    # Build job profile once (shared across all batches)
    job = build_job_profile(
        title=title,
        description=description,
        required_skills=_split_csv(required_skills),
        preferred_skills=_split_csv(preferred_skills),
        min_experience_years=float(min_experience_years),
        required_education=required_education,
    )

    # Create a temporary directory to hold batch subdirectories
    with tempfile.TemporaryDirectory(prefix="ats_parallel_batches_") as batch_root:
        batch_root_path = Path(batch_root)
        batch_folders = []
        
        # Create batch subdirectories and copy files
        for batch_id in range(num_batches):
            start_idx = batch_id * batch_size
            end_idx = min(start_idx + batch_size, total_files)
            batch_files = resume_files[start_idx:end_idx]
            
            # Create batch folder
            batch_folder = batch_root_path / f"batch_{batch_id}"
            batch_folder.mkdir()
            
            # Copy batch files
            for file_path in batch_files:
                shutil.copy2(file_path, batch_folder / file_path.name)
            
            batch_folders.append((batch_folder, batch_id, len(batch_files)))

        # Create batch processing tasks
        batch_tasks = [
            _process_batch_folder(str(batch_folder), job, top_n, batch_id)
            for batch_folder, batch_id, _ in batch_folders
        ]

        # Process all batches in parallel
        print(f"[PARALLEL] Starting {num_batches} parallel batch tasks...")
        batch_results = await asyncio.gather(*batch_tasks)
        print(f"[PARALLEL] All batches completed")

        # Aggregate results
        all_candidates = []
        total_submitted = 0
        total_processed = 0
        total_filtered = 0
        total_failed = 0
        batch_timings = []

        for batch_id, (output, duration) in enumerate(batch_results):
            batch_size_actual = batch_folders[batch_id][2]
            batch_timings.append({
                "batch_id": batch_id,
                "files_in_batch": batch_size_actual,
                "duration_seconds": round(duration, 2),
                "throughput": round(batch_size_actual / duration, 2) if duration > 0 else 0,
            })
            
            total_submitted += output.total_submitted
            total_processed += output.total_processed
            total_filtered += output.total_filtered
            total_failed += output.total_failed
            
            # Collect top candidates from each batch
            all_candidates.extend(output.top_candidates)

        # Sort all candidates by score and take top N
        # Convert to dicts if needed
        candidates_as_dicts = []
        for cand in all_candidates:
            if isinstance(cand, dict):
                cand_dict = cand
            else:
                # Convert dataclass/object to dict
                cand_dict = asdict(cand) if is_dataclass(cand) else vars(cand)
            
            # Ensure score field is present for sorting
            if "score" not in cand_dict and "scores" in cand_dict:
                scores_obj = cand_dict["scores"]
                if isinstance(scores_obj, dict):
                    cand_dict["score"] = scores_obj.get("final", 0)
                else:
                    cand_dict["score"] = scores_obj.final if hasattr(scores_obj, "final") else 0
            
            candidates_as_dicts.append(cand_dict)
        
        candidates_as_dicts.sort(key=lambda x: x.get("score", 0), reverse=True)
        top_candidates = candidates_as_dicts[:int(top_n)]

        overall_duration = time.time() - overall_start
        throughput = total_processed / overall_duration if overall_duration > 0 else 0

        response_data = {
            "total_submitted": total_submitted,
            "total_processed": total_processed,
            "total_filtered": total_filtered,
            "total_failed": total_failed,
            "top_candidates": _to_jsonable(top_candidates),
            "execution_summary": {
                "duration_seconds": round(overall_duration, 2),
                "total_submitted": total_submitted,
                "total_processed": total_processed,
                "total_filtered": total_filtered,
                "throughput_resumes_per_second": round(throughput, 2),
                "folder_input": {
                    "source_path": str(folder_path),
                    "total_files_found": total_files,
                },
                "batch_processing": {
                    "num_batches": num_batches,
                    "batch_size": batch_size,
                    "batch_details": batch_timings,
                    "parallelization": f"{num_batches} batches processed concurrently",
                },
            },
        }
        
        return response_data
