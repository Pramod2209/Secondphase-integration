from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import config
from .orchestrator import run_bulk, run_single
from .schema import ATSOutput, JobProfile, ResumeResult, ScoreBreakdown


def build_job_profile(
    title: str,
    description: str,
    required_skills: List[str],
    preferred_skills: List[str],
    min_experience_years: float,
    required_education: str,
    job_id: Optional[str] = None,
) -> JobProfile:
    return JobProfile(
        job_id=job_id or str(uuid.uuid4()),
        title=title.strip(),
        description=description.strip(),
        required_skills=[s.lower().strip() for s in required_skills],
        preferred_skills=[s.lower().strip() for s in preferred_skills],
        min_experience_years=float(min_experience_years),
        required_education=required_education.lower().strip(),
    )


def _to_serializable(obj):
    if hasattr(obj, "tolist"):
        return obj.tolist()
    if hasattr(obj, "__dict__"):
        return asdict(obj)
    return obj


def _export_json(output: ATSOutput, output_json_path: Optional[str]) -> str:
    if output_json_path:
        path = Path(output_json_path)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = config.OUTPUT_DIR / f"results_{output.job_id}_{stamp}.json"

    payload = asdict(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_to_serializable), encoding="utf-8")
    return str(path)


async def screen_bulk(
    resume_folder: str,
    job: JobProfile,
    top_n: int = config.DEFAULT_TOP_N,
    output_json_path: Optional[str] = None,
) -> ATSOutput:
    folder = Path(resume_folder)
    if not folder.exists() or not folder.is_dir():
        raise ValueError(f"Invalid resume folder: {resume_folder}")

    resume_paths = [
        str(path)
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in {".pdf", ".docx", ".txt"}
    ]
    resume_paths.sort(key=lambda item: item.lower())

    output = await run_bulk(resume_paths=resume_paths, job=job, top_n=top_n)
    _export_json(output, output_json_path)
    return output


async def screen_single(resume_path: str, job: JobProfile, debug: bool = True) -> ResumeResult:
    return await run_single(resume_path=resume_path, job=job, debug=debug)


__all__ = [
    "screen_bulk",
    "screen_single",
    "build_job_profile",
    "JobProfile",
    "ResumeResult",
    "ATSOutput",
    "ScoreBreakdown",
]
