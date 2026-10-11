"""Conservative prefilter.

A resume is filtered out of the ranking only when the job lists skills and *none* of the
required or preferred skills can be found anywhere in the resume text (using the same
alias/boundary-aware matcher as scoring). Anything else is ranked. Filtered resumes are still
reported with their reason, never dropped silently. Disable with ATS_PREFILTER=0.
"""
from __future__ import annotations

from typing import List

from . import config
from .schema import JobProfile

FILTER_REASON = "Filtered: none of the job's required or preferred skills were found in the resume"


def passes_prefilter(matched_required: List[str], matched_preferred: List[str], job: JobProfile,
                     enabled: bool | None = None) -> bool:
    enabled = config.PREFILTER_ENABLED if enabled is None else enabled
    if not enabled:
        return True
    if not job.required_skills and not job.preferred_skills:
        return True
    return bool(matched_required or matched_preferred)
