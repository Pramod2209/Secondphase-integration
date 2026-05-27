from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, List
import os

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.datastructures import FormData
from starlette.requests import Request

from .api import build_job_profile, screen_bulk, screen_single
from .embedder import Embedder
from .extractor import load_taxonomy
from .resources import detect_and_tune

# Monkey-patch Request.form() to increase file limits
_original_form = Request.form

async def _patched_form(self):
    """Override form parsing with higher limits."""
    if not hasattr(self, '_form'):
        # Import here to avoid circular imports
        from starlette.formparsers import FormParser, MultiPartParser
        
        # Get the form parser class from the scope
        assert (
            self.scope['type'] == 'http'
        ), 'Form data can only be accessed for "http" scope'
        
        if self.scope['method'] not in ('POST', 'PUT', 'PATCH'):
            self._form = FormData()
        else:
            content_type = self.scope.get('headers', {})
            form_charset = 'utf-8'
            
            # Determine which parser to use
            for key, value in self.scope.get('headers', []):
                if key == b'content-type':
                    content_type_str = value.decode('utf-8')
                    if 'multipart/form-data' in content_type_str:
                        # Use MultiPartParser with higher limits
                        parser = MultiPartParser(
                            self.scope,
                            self.receive,
                            upload_dir=None,
                            max_size=5 * 1024 * 1024 * 1024,  # 5GB
                            max_files=10000,  # Allow up to 10000 files
                        )
                    else:
                        # Use FormParser
                        parser = FormParser(
                            self.scope,
                            self.receive,
                            max_size=5 * 1024 * 1024 * 1024,  # 5GB
                        )
                    self._form = await parser.parse()
                    break
            else:
                # Default fallback
                parser = FormParser(
                    self.scope,
                    self.receive,
                    max_size=5 * 1024 * 1024 * 1024,  # 5GB
                )
                self._form = await parser.parse()
    
    return self._form

# Replace the form property
Request.form = property(lambda self: _patched_form(self))

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
    embedder = Embedder(batch_size=resources.embedding_batch_size)
    embedder.encode_single("warmup text")


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
    debug: bool = Form(True),
) -> Any:
    if not resume.filename:
        raise HTTPException(status_code=400, detail="Missing resume filename")

    with tempfile.TemporaryDirectory(prefix="ats_single_api_") as temp_dir:
        file_path = Path(temp_dir) / resume.filename
        file_path.write_bytes(await resume.read())

        job = build_job_profile(
            title=title,
            description=description,
            required_skills=_split_csv(required_skills),
            preferred_skills=_split_csv(preferred_skills),
            min_experience_years=float(min_experience_years),
            required_education=required_education,
        )

        result = await screen_single(str(file_path), job, debug=debug)
        return _to_jsonable(asdict(result))


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
        }
        return response_data
