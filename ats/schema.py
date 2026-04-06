from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np


@dataclass
class JobProfile:
    job_id: str
    title: str
    description: str
    required_skills: List[str]
    preferred_skills: List[str]
    min_experience_years: float
    required_education: str


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


@dataclass
class ResumeResult:
    candidate_id: str
    file_path: str
    name: str
    parsed_text: str
    extracted_skills: List[str]
    experience_years: float
    education_level: str
    embedding: Optional[np.ndarray]
    scores: ScoreBreakdown
    missing_skills: List[str]
    matched_skills: List[str]
    explanation: str
    status: str
    stage_timings: Dict[str, float]
    error_message: Optional[str] = None


@dataclass
class ATSOutput:
    job_id: str
    total_submitted: int
    total_processed: int
    total_filtered: int
    total_failed: int
    time_taken_seconds: float
    throughput_per_second: float
    top_candidates: List[ResumeResult]
    all_results: List[ResumeResult]
    summary_stats: Dict[str, Any]


@dataclass
class SystemResources:
    cpu_cores: int
    available_ram_gb: float
    batch_size: int
    max_concurrent_batches: int
    thread_workers: int
    process_workers: int
    embedding_batch_size: int
