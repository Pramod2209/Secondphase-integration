from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

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
        return _to_jsonable(asdict(output))
