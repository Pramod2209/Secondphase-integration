from __future__ import annotations

from typing import Dict, List

from . import config
from .extractor import EDUCATION_LEVELS
from .schema import JobProfile, ResumeResult, ScoreBreakdown


def compute_scores(
    matched_skills: List[str],
    missing_skills: List[str],
    preferred_bonus: float,
    experience_years: float,
    cosine_similarity: float,
    education_level: str,
    job: JobProfile,
) -> ScoreBreakdown:
    if len(job.required_skills) == 0:
        skills_score = 100.0
    else:
        skills_score = (len(matched_skills) / len(job.required_skills)) * 100.0
        skills_score = min(skills_score + preferred_bonus, 100.0)

    if job.min_experience_years == 0:
        experience_score = 100.0
    else:
        experience_score = min((experience_years / job.min_experience_years) * 100.0, 100.0)

    semantic_score = max(0.0, min(cosine_similarity * 100.0, 100.0))

    required_level = EDUCATION_LEVELS.get(job.required_education.lower(), 1)
    actual_level = EDUCATION_LEVELS.get(education_level.lower(), 1)
    if actual_level >= required_level:
        education_score = 100.0
    elif actual_level == required_level - 1:
        education_score = 70.0
    else:
        education_score = 40.0

    final = (
        config.WEIGHT_SKILLS * skills_score
        + config.WEIGHT_EXPERIENCE * experience_score
        + config.WEIGHT_SEMANTIC * semantic_score
        + config.WEIGHT_EDUCATION * education_score
    )

    return ScoreBreakdown(
        skills=round(skills_score, 2),
        experience=round(experience_score, 2),
        semantic=round(semantic_score, 2),
        education=round(education_score, 2),
        final=round(final, 2),
    )


def generate_explanation(result: ResumeResult, job: JobProfile) -> str:
    matched_count = len(result.matched_skills)
    required_count = len(job.required_skills)
    preferred_hits = sorted(set(result.extracted_skills) & set(job.preferred_skills))

    strengths: List[str] = [
        (
            f"Matched {matched_count}/{required_count} required skills: "
            f"{', '.join(result.matched_skills[:10]) or 'none'}"
        ),
        (
            f"{result.experience_years:.1f} years experience "
            f"(required: {job.min_experience_years:g}+)"
        ),
        f"Semantic alignment with JD: {result.scores.semantic / 100.0:.2f}",
    ]
    if preferred_hits:
        strengths.append(f"Matched preferred skills: {', '.join(preferred_hits[:10])}")

    gaps: List[str] = []
    if result.missing_skills:
        gaps.append(f"Missing required skills: {', '.join(result.missing_skills[:10])}")
    if result.education_level.lower() != job.required_education.lower() and job.required_education.lower() != "any":
        gaps.append(
            f"Education: {result.education_level} (required: {job.required_education})"
        )
    if not gaps:
        gaps.append("No critical gaps identified")

    if result.scores.final >= 80:
        recommendation = "Strong shortlist. Schedule technical round."
    elif result.scores.final >= 60:
        recommendation = "Borderline profile. Manual review recommended."
    else:
        recommendation = "Below threshold. Unlikely fit for current role."

    lines = [
        "Candidate Evaluation",
        f"Candidate : {result.name}",
        f"Role      : {job.title}",
        f"Score     : {result.scores.final:.2f}%",
        "",
        "Strengths:",
        *[f"- {line}" for line in strengths],
        "",
        "Gaps:",
        *[f"- {line}" for line in gaps],
        "",
        f"Recommendation: {recommendation}",
    ]
    return "\n".join(lines)
