from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from . import config
from .extractor import normalize_required_education
from .orchestrator import run_bulk, run_single
from .schema import ATSOutput, JobProfile, ResumeResult, ResumeResultResponse, ScoreBreakdown
from .skills import clean_skill, normalize_skill_list

# OS/editor artefacts that are never resumes (not reported).
_IGNORED_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
MAX_SKILLS = 100


class JobValidationError(ValueError):
    pass


def _resume_result_to_response(result: ResumeResult, include_preview: bool = False) -> ResumeResultResponse:
    """Internal ResumeResult -> API/export object (no resume text, no embedding)."""
    return ResumeResultResponse(
        candidate_id=result.candidate_id,
        file_path=result.file_path,
        file_name=result.file_name or Path(result.file_path).name,
        name=result.name,
        name_source=result.name_source,
        extracted_skills=result.extracted_skills,
        experience_years=result.experience_years,
        education_level=result.education_level,
        scores=result.scores,
        missing_skills=result.missing_skills,
        matched_skills=result.matched_skills,
        matched_preferred_skills=result.matched_preferred_skills,
        explanation=result.explanation,
        status=result.status,
        stage_timings=result.stage_timings,
        error_message=result.error_message,
        ocr_used=result.ocr_used,
        rank=result.rank,
        duplicate_of=result.duplicate_of,
        skill_evidence=result.skill_evidence,
        text_preview=(result.parsed_text or None) if include_preview else None,
    )


def _strip_embeddings(output: ATSOutput) -> ATSOutput:
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


def _split_skills(value) -> List[str]:
    if value is None:
        return []
    items = value.split(",") if isinstance(value, str) else list(value)
    return [clean_skill(str(s)) for s in items if clean_skill(str(s))]


def build_job_profile(
    title: str,
    description: str,
    required_skills: Sequence[str] | str,
    preferred_skills: Sequence[str] | str,
    min_experience_years: float,
    required_education: str,
    job_id: Optional[str] = None,
) -> JobProfile:
    """Validate and normalize a job. Raises JobValidationError with a user-facing message."""
    title = (title or "").strip()
    description = (description or "").strip()
    required = _split_skills(required_skills)
    preferred = _split_skills(preferred_skills)
    if len(required) + len(preferred) > MAX_SKILLS:
        raise JobValidationError(f"Too many skills (max {MAX_SKILLS} required+preferred).")
    if any(len(s) > 80 for s in required + preferred):
        raise JobValidationError("A skill name is longer than 80 characters; separate skills with commas.")
    try:
        min_exp = float(min_experience_years if min_experience_years not in (None, "") else 0)
    except (TypeError, ValueError) as exc:
        raise JobValidationError("min_experience_years must be a number.") from exc
    if not math.isfinite(min_exp) or min_exp < 0 or min_exp > 50:
        raise JobValidationError("min_experience_years must be between 0 and 50.")
    education = normalize_required_education(required_education or "any")
    if education is None:
        raise JobValidationError(
            "required_education must be one of: any, diploma, associate, bachelor, master, phd.")
    if not (title or description or required or preferred):
        raise JobValidationError("The job needs at least a title, a description or some skills.")
    req = normalize_skill_list(required)
    pref = [s for s in normalize_skill_list(preferred) if s not in req]
    return JobProfile(
        job_id=job_id or str(uuid.uuid4()),
        title=title,
        description=description,
        required_skills=req,
        preferred_skills=pref,
        min_experience_years=min_exp,
        required_education=education,
    )


def _to_serializable(obj):
    if hasattr(obj, "tolist"):
        return obj.tolist()
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    return str(obj)


def _export_json(output: ATSOutput, job: JobProfile, output_json_path: Optional[str]) -> Optional[str]:
    """Save results (scores/extraction only; never resume text or embeddings)."""
    if not output_json_path and not config.SAVE_RESULTS_JSON:
        return None
    if output_json_path:
        path = Path(output_json_path)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = config.OUTPUT_DIR / f"results_{output.job_id}_{stamp}.json"
    payload = asdict(_strip_embeddings(output) if output.all_results and isinstance(output.all_results[0], ResumeResult)
                     else output)
    payload["job"] = asdict(job)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_to_serializable), encoding="utf-8")
    return str(path)


def _without_file_name(message: str, file_path: str) -> str:
    """Remove the file's name (full path, base name, stem) from a diagnostic message."""
    name = Path(file_path)
    for part in sorted({file_path, name.name, name.stem}, key=len, reverse=True):
        if len(part) >= 3:
            message = re.sub(re.escape(part), "<file>", message, flags=re.IGNORECASE)
    return message


def _failure_record(r: ResumeResult) -> dict:
    if config.SAVE_RESULTS_JSON:  # results are saved with file names anyway
        return {"file_path": r.file_path, "error_message": r.error_message, "stage_timings": r.stage_timings}
    # Saving disabled: no file name (it often holds the candidate's name). candidate_id is random and is also
    # in the API response, so a reviewer can match a log line to the on-screen row during the session.
    return {"candidate_id": r.candidate_id, "file_type": Path(r.file_path).suffix.lower()[:10],
            "error_message": _without_file_name(r.error_message or "", r.file_path),
            "stage_timings": r.stage_timings}


def _write_failure_log(output: ATSOutput) -> Optional[str]:
    failed = [r for r in output.all_results if r.status == "failed"]
    if not failed:
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = config.RUNS_DIR / f"failed_files_{stamp}_{output.job_id[:8]}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for r in failed:
            fh.write(json.dumps(_failure_record(r)) + "\n")
    return str(path)


def list_resume_files(resume_folder: str) -> List[Tuple[str, str]]:
    """Every file under the folder (recursive) as (absolute_path, path_relative_to_folder).

    Unsupported types are included on purpose so they are reported as failures, not dropped."""
    folder = Path(resume_folder)
    if not folder.exists() or not folder.is_dir():
        raise ValueError(f"Invalid resume folder: {resume_folder}")
    items = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name in _IGNORED_NAMES or name.startswith("~$") or name.startswith("."):
            continue
        items.append((str(path), path.relative_to(folder).as_posix()))
    items.sort(key=lambda item: item[1].lower())
    return items


async def screen_files(
    files: Sequence[Tuple[str, str]],
    job: JobProfile,
    top_n: int = config.DEFAULT_TOP_N,
    output_json_path: Optional[str] = None,
    progress_key: Optional[str] = None,
    extra_failures: Optional[List[ResumeResult]] = None,
) -> ATSOutput:
    """Screen explicit (disk_path, display_path) pairs. Returns the API form (no text/embeddings)."""
    output = await run_bulk(resume_paths=list(files), job=job, top_n=top_n, progress_key=progress_key,
                            extra_failures=extra_failures)
    try:
        output.summary_stats["results_path"] = _export_json(output, job, output_json_path)
    except OSError as exc:
        output.summary_stats["results_path"] = None
        output.summary_stats.setdefault("warnings", []).append(f"Could not save results file: {exc}")
    try:
        output.summary_stats["failed_log_path"] = _write_failure_log(output)
    except OSError:
        output.summary_stats["failed_log_path"] = None
    return _strip_embeddings(output)


async def screen_bulk(
    resume_folder: str,
    job: JobProfile,
    top_n: int = config.DEFAULT_TOP_N,
    output_json_path: Optional[str] = None,
    progress_key: Optional[str] = None,
) -> ATSOutput:
    return await screen_files(list_resume_files(resume_folder), job, top_n=top_n,
                              output_json_path=output_json_path, progress_key=progress_key)


async def screen_single(resume_path: str, job: JobProfile, debug: bool = False,
                        display_path: Optional[str] = None) -> ResumeResultResponse:
    result = await run_single(resume_path=resume_path, job=job, debug=debug, display_path=display_path)
    return _resume_result_to_response(result, include_preview=debug)


__all__ = [
    "screen_bulk",
    "screen_files",
    "screen_single",
    "build_job_profile",
    "list_resume_files",
    "JobValidationError",
    "JobProfile",
    "ResumeResult",
    "ResumeResultResponse",
    "ATSOutput",
    "ScoreBreakdown",
]
