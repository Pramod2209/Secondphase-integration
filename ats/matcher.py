from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from .schema import JobProfile
from .skills import RELATED, match_skills

PREFERRED_BONUS_PER_SKILL = 2.0
PREFERRED_BONUS_CAP = 10.0


def cosine(resume_embedding: Optional[np.ndarray], jd_embedding: Optional[np.ndarray]) -> Optional[float]:
    """Cosine similarity of two (normalized) vectors; None if unavailable or degenerate."""
    if resume_embedding is None or jd_embedding is None:
        return None
    a = np.asarray(resume_embedding, dtype=np.float32)
    b = np.asarray(jd_embedding, dtype=np.float32)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if a.shape != b.shape or denom == 0.0 or not math.isfinite(denom):
        return None
    value = float(np.dot(a, b) / denom)
    return value if math.isfinite(value) else None


@dataclass
class JobSkillMatch:
    matched: List[str] = field(default_factory=list)            # required skills with credit > 0
    missing: List[str] = field(default_factory=list)            # required skills without creditable evidence
    matched_preferred: List[str] = field(default_factory=list)  # preferred skills with credit > 0
    bonus: float = 0.0                                          # preferred-skill bonus (skills score points)
    required_credit: float = 0.0                                # sum of credits over required skills
    evidence: List[Dict[str, Any]] = field(default_factory=list)


def match_job_skills(text: str, job: JobProfile, anchors: Optional[Sequence[int]] = None) -> JobSkillMatch:
    """Evidence for the job's skills in the resume text (see skills.match_skills for the tiers)."""
    preferred = [s for s in job.preferred_skills if s not in job.required_skills]
    found = match_skills(text, [*job.required_skills, *preferred], anchors)
    out = JobSkillMatch()
    pref_credit = 0.0
    for kind, skills in (("required", job.required_skills), ("preferred", preferred)):
        for skill in skills:
            ev = found.get(skill)
            if ev is not None:
                item = {"skill": skill, "kind": kind, "tier": ev.tier, "evidence": ev.evidence,
                        "credit": round(ev.credit, 2), "strength": "weak" if ev.weak else "explicit"}
                if ev.weak:
                    item["note"] = ev.weak
                out.evidence.append(item)
            credited = ev is not None and ev.tier != RELATED and ev.credit > 0
            if kind == "required":
                if credited:
                    out.matched.append(skill)
                    out.required_credit += ev.credit
                else:
                    out.missing.append(skill)
            elif credited:
                out.matched_preferred.append(skill)
                pref_credit += ev.credit
    out.bonus = min(pref_credit * PREFERRED_BONUS_PER_SKILL, PREFERRED_BONUS_CAP)
    return out
