from __future__ import annotations

from typing import List, Tuple

import numpy as np

from .schema import JobProfile


def match(
    resume_embedding: np.ndarray,
    jd_embedding: np.ndarray,
    resume_skills: List[str],
    job: JobProfile,
) -> Tuple[float, List[str], List[str], float]:
    cosine_similarity = float(np.dot(resume_embedding, jd_embedding))

    resume_skill_set = set(resume_skills)
    required_set = set(job.required_skills)
    preferred_set = set(job.preferred_skills)

    matched_skills = sorted(resume_skill_set & required_set)
    missing_skills = sorted(required_set - resume_skill_set)

    matched_preferred = resume_skill_set & preferred_set
    preferred_bonus = min(len(matched_preferred) * 2.0, 10.0)

    return cosine_similarity, matched_skills, missing_skills, preferred_bonus
