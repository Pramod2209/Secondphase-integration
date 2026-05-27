from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import config
from .orchestrator import run_bulk, run_single
from .schema import ATSOutput, JobProfile, ResumeResult, ResumeResultResponse, ScoreBreakdown


def _resume_result_to_response(result: ResumeResult) -> ResumeResultResponse:
    """Convert internal ResumeResult (with embedding) to API response (without embedding)."""
    return ResumeResultResponse(
        candidate_id=result.candidate_id,
        file_path=result.file_path,
        name=result.name,
        parsed_text=result.parsed_text,
        extracted_skills=result.extracted_skills,
        experience_years=result.experience_years,
        education_level=result.education_level,
        scores=result.scores,
        missing_skills=result.missing_skills,
        matched_skills=result.matched_skills,
        explanation=result.explanation,
        status=result.status,
        stage_timings=result.stage_timings,
        error_message=result.error_message,
    )


def _strip_embeddings(output: ATSOutput) -> ATSOutput:
    """Convert ATSOutput with ResumeResult to use ResumeResultResponse (strips embeddings)."""
    return ATSOutput(
        job_id=output.job_id,
        total_submitted=output.total_submitted,
        total_processed=output.total_processed,
        total_filtered=output.total_filtered,
        total_failed=output.total_failed,
        time_taken_seconds=output.time_taken_seconds,
        throughput_per_second=output.throughput_per_second,
        top_candidates=[_resume_result_to_response(r) for r in output.top_candidates],
        all_results=[_resume_result_to_response(r) for r in output.all_results],
        summary_stats=output.summary_stats,
    )


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
    # Log failed files for triage
    try:
        failed = [r for r in output.all_results if r.status == "failed"]
        failed_log_path = None
        if failed:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = config.RUNS_DIR / f"failed_files_{stamp}.jsonl"
            with path.open("w", encoding="utf-8") as fh:
                for r in failed:
                    payload = {
                        "file_path": r.file_path,
                        "error_message": r.error_message,
                        "stage_timings": r.stage_timings,
                    }
                    fh.write(json.dumps(payload, default=_to_serializable) + "\n")
            failed_log_path = str(path)
        output.summary_stats["failed_log_path"] = failed_log_path
    except Exception:
        pass
    # Strip embeddings from API response (but keep in saved JSON for debugging)
    return _strip_embeddings(output)


async def screen_single(resume_path: str, job: JobProfile, debug: bool = True) -> ResumeResultResponse:
    result = await run_single(resume_path=resume_path, job=job, debug=debug)
    # Strip embedding from API response
    return _resume_result_to_response(result)


__all__ = [
    "screen_bulk",
    "screen_single",
    "build_job_profile",
    "JobProfile",
    "ResumeResult",
    "ResumeResultResponse",
    "ATSOutput",
    "ScoreBreakdown",
]
