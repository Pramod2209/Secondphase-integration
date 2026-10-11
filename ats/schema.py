from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class JobProfile:
    job_id: str
    title: str
    description: str
    required_skills: List[str]  # canonical skill names (see skills.normalize_skill_list)
    preferred_skills: List[str]
    min_experience_years: float
    required_education: str  # any/diploma/associate/bachelor/master/phd


@dataclass
class ScoreBreakdown:
    skills: float
    experience: float
    semantic: float
    education: float
    final: float


@dataclass
class ExtractionResult:
    skills: List[str]
    experience_years: float
    education_level: str
    name: str = ""
    name_source: str = "filename"
    skill_anchors: List[int] = field(default_factory=list)


@dataclass
class ResumeResult:
    """Internal per-resume result. parsed_text/embedding never leave the process."""

    candidate_id: str
    file_path: str  # display path: original upload name or path relative to the screened folder
    name: str
    parsed_text: str
    extracted_skills: List[str]
    experience_years: float
    education_level: str
    embedding: Optional[Any]
    scores: ScoreBreakdown
    missing_skills: List[str]
    matched_skills: List[str]
    explanation: str
    status: str  # processed | filtered | failed
    stage_timings: Dict[str, float]
    error_message: Optional[str] = None
    file_name: str = ""
    name_source: str = "filename"
    matched_preferred_skills: List[str] = field(default_factory=list)
    ocr_used: bool = False
    rank: Optional[int] = None
    content_hash: str = ""
    duplicate_of: Optional[str] = None
    # One entry per job skill with evidence: {skill, kind (required|preferred), tier, evidence, credit}.
    # tier: exact | alias | category (partial credit) | related (no credit; for the reviewer).
    skill_evidence: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ResumeResultResponse:
    """Response/export schema: no resume text, no embedding vectors."""

    candidate_id: str
    file_path: str
    file_name: str
    name: str
    name_source: str
    extracted_skills: List[str]
    experience_years: float
    education_level: str
    scores: ScoreBreakdown
    missing_skills: List[str]
    matched_skills: List[str]
    matched_preferred_skills: List[str]
    explanation: str
    status: str
    stage_timings: Dict[str, float]
    error_message: Optional[str] = None
    ocr_used: bool = False
    rank: Optional[int] = None
    duplicate_of: Optional[str] = None
    skill_evidence: List[Dict[str, Any]] = field(default_factory=list)
    text_preview: Optional[str] = None  # only populated for single-resume debug responses


@dataclass
class ATSOutput:
    job_id: str
    total_submitted: int
    total_processed: int
    total_filtered: int
    total_failed: int
    time_taken_seconds: float
    throughput_per_second: float
    top_candidates: List[Any]
    all_results: List[Any]
    summary_stats: Dict[str, Any]


@dataclass
class SystemResources:
    cpu_cores: int
    available_ram_gb: float
    parse_workers: int
    embedding_batch_size: int
    embedding_threads: int
