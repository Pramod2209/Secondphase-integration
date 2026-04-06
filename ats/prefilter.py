from __future__ import annotations

import re
from collections import Counter
from typing import Set

from .schema import JobProfile


def _top_content_words(text: str, limit: int = 10) -> Set[str]:
    words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9+#\-.]{2,}\b", text.lower())
    stop_words = {
        "with", "and", "for", "the", "you", "your", "our", "are", "this", "that",
        "from", "have", "will", "their", "role", "team", "years", "experience", "work",
        "using", "skills", "required", "preferred", "candidate", "looking", "about", "into",
    }
    filtered = [w for w in words if w not in stop_words]
    return {w for w, _ in Counter(filtered).most_common(limit)}


def prefilter(raw_text: str, job: JobProfile, min_hits: int = 2) -> bool:
    raw = (raw_text or "").lower()
    if not raw.strip():
        return False

    keywords = {s.lower() for s in job.required_skills}
    keywords.update(_top_content_words(job.description, limit=10))
    hits = sum(1 for kw in keywords if kw and kw in raw)
    return hits >= min_hits
