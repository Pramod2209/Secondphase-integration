from __future__ import annotations

import math
from typing import List, Optional

from . import config
from .extractor import EDUCATION_LEVELS, normalize_required_education
from .schema import JobProfile, ResumeResult, ScoreBreakdown


def _finite(value: Optional[float], default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, _finite(value)))


def education_score(education_level: str, required_education: str) -> float:
    """100 if requirement met, 70 if one level short, 40 if lower, 0 if no evidence of education."""
    required = normalize_required_education(required_education) or "any"
    if required == "any":
        return 100.0
    actual = EDUCATION_LEVELS.get((education_level or "unknown").lower(), 0)
    needed = EDUCATION_LEVELS[required]
    if actual <= 0:
        return 0.0
    if actual >= needed:
        return 100.0
    if actual == needed - 1:
        return 70.0
    return 40.0


def experience_is_relevant(matched_skills: List[str], preferred_bonus: float, job: JobProfile) -> bool:
    """Experience counts only if the job lists no skills or at least one of them was found."""
    if not job.required_skills and not job.preferred_skills:
        return True
    return bool(matched_skills) or _finite(preferred_bonus) > 0


def compute_scores(
    matched_skills: List[str],
    missing_skills: List[str],
    preferred_bonus: float,
    experience_years: float,
    cosine_similarity: Optional[float],
    education_level: str,
    job: JobProfile,
    semantic_enabled: bool = True,
    required_credit: Optional[float] = None,
) -> ScoreBreakdown:
    """required_credit: summed evidence credit of the required skills (exact/alias 1.0, category
    partial, see skills.tier_credit); defaults to one full credit per matched skill."""
    required_count = len(job.required_skills)
    credit = len(matched_skills) if required_credit is None else _clamp(required_credit, 0.0, float(required_count))
    if required_count == 0:
        skills_score = 100.0  # nothing required: neutral
    else:
        skills_score = (credit / required_count) * 100.0 + _clamp(preferred_bonus, 0.0, 10.0)
    skills_score = _clamp(skills_score)

    min_exp = max(0.0, _finite(job.min_experience_years))
    years = max(0.0, _finite(experience_years))
    experience_score = 100.0 if min_exp == 0 else _clamp(years / min_exp * 100.0)
    if not experience_is_relevant(matched_skills, preferred_bonus, job):
        # Years in an unrelated field (no required/preferred skill found at all) are not evidence of
        # relevant experience; otherwise a cook with 10 years outranks a partial match with 2.
        experience_score = 0.0

    semantic_available = semantic_enabled and cosine_similarity is not None and math.isfinite(_finite(cosine_similarity, float("nan")))
    semantic_score = _clamp(_finite(cosine_similarity) * 100.0) if semantic_available else 0.0

    edu_score = education_score(education_level, job.required_education)

    weights = {
        "skills": config.WEIGHT_SKILLS,
        "experience": config.WEIGHT_EXPERIENCE,
        "semantic": config.WEIGHT_SEMANTIC if semantic_available else 0.0,
        "education": config.WEIGHT_EDUCATION,
    }
    total = sum(weights.values()) or 1.0  # re-normalize when the semantic stage is unavailable
    final = (
        weights["skills"] * skills_score
        + weights["experience"] * experience_score
        + weights["semantic"] * semantic_score
        + weights["education"] * edu_score
    ) / total

    return ScoreBreakdown(
        skills=round(skills_score, 2),
        experience=round(experience_score, 2),
        semantic=round(semantic_score, 2),
        education=round(edu_score, 2),
        final=round(_clamp(final), 2),
    )


def describe_match(skill: str, evidence: Optional[dict]) -> str:
    """'sql (via mysql: specific tool for this skill, 75% credit)', 'kubernetes (as "k8s")', 'python'."""
    if not evidence:
        return skill
    if evidence.get("strength") == "weak":  # only when weak evidence is configured to earn credit
        return f"{skill} (weak: {evidence['evidence']} as {evidence.get('note', 'context only')}, {evidence['credit']:.0%} credit)"
    if evidence.get("tier") == "exact":
        return skill
    if evidence["tier"] == "alias":
        return f'{skill} (as "{evidence["evidence"]}")'
    if evidence["tier"] == "category":
        return f"{skill} (via {evidence['evidence']}: specific tool for this skill, {evidence['credit']:.0%} credit)"
    return skill


def related_line(skill_evidence: List[dict]) -> str:
    """Reviewer note for evidence that earns no credit: related skills and weak contextual mentions."""
    related = [f"{e['evidence']} (for {e['skill']})" for e in skill_evidence if e.get("tier") == "related"]
    weak = [f"{e['evidence']} for {e['skill']} ({e.get('note', 'context only')})" for e in skill_evidence
            if e.get("strength") == "weak" and e.get("tier") != "related" and not e.get("credit")]
    parts = []
    if related:
        parts.append("Related but not equivalent, not counted: " + ", ".join(related[:8]))
    if weak:
        parts.append("Weak evidence, not counted: " + ", ".join(weak[:8]))
    return ". ".join(parts)


def generate_explanation(result: ResumeResult, job: JobProfile, semantic_enabled: bool = True) -> str:
    matched_count = len(result.matched_skills)
    required_count = len(job.required_skills)
    preferred_hits = result.matched_preferred_skills

    evidence = {(e["skill"], e["kind"]): e for e in result.skill_evidence}
    if required_count:
        shown = [describe_match(s, evidence.get((s, "required"))) for s in result.matched_skills[:10]]
        skills_line = f"Matched {matched_count}/{required_count} required skills: {', '.join(shown) or 'none'}"
    else:
        skills_line = "No required skills specified"
    if job.min_experience_years > 0:
        exp_line = f"{result.experience_years:.1f} years experience (required: {job.min_experience_years:g}+)"
    else:
        exp_line = f"{result.experience_years:.1f} years experience (no minimum required)"
    if job.reference_date:
        exp_line += f"; 'present' counted to {job.reference_date}"
    strengths: List[str] = [skills_line, exp_line]
    if semantic_enabled:
        strengths.append(f"Semantic alignment with JD: {result.scores.semantic / 100.0:.2f}")
    else:
        strengths.append("Semantic alignment: not computed (semantic scoring disabled)")
    if preferred_hits:
        shown = [describe_match(s, evidence.get((s, "preferred"))) for s in preferred_hits[:10]]
        strengths.append(f"Matched preferred skills: {', '.join(shown)}")
    related = related_line(result.skill_evidence)

    gaps: List[str] = []
    if result.missing_skills:
        gaps.append(f"Missing required skills: {', '.join(result.missing_skills[:10])}")
    if not experience_is_relevant(result.matched_skills, 2.0 if preferred_hits else 0.0, job):
        gaps.append("No required or preferred skill found, so experience is not counted as relevant")
    elif job.min_experience_years > 0 and result.experience_years < job.min_experience_years:
        gaps.append(f"Experience below requirement ({result.experience_years:.1f} < {job.min_experience_years:g} years)")
    required_edu = normalize_required_education(job.required_education) or "any"
    if required_edu != "any" and result.scores.education < 100:
        found = result.education_level if result.education_level != "unknown" else "not found in resume"
        gaps.append(f"Education: {found} (required: {required_edu})")
    if related:
        gaps.append(related)
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
