from __future__ import annotations

import json
import re
from functools import lru_cache
from datetime import datetime
from typing import Dict, List, Tuple

from dateutil import parser as date_parser

from . import config
from .schema import ExtractionResult

EDUCATION_LEVELS = {
    "phd": 5,
    "doctorate": 5,
    "ph.d": 5,
    "master": 4,
    "m.tech": 4,
    "m.s.": 4,
    "mba": 4,
    "m.e.": 4,
    "bachelor": 3,
    "b.tech": 3,
    "b.e.": 3,
    "b.s.": 3,
    "diploma": 2,
    "associate": 2,
    "none": 1,
}

_MONTH_PATTERN = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"

_DATE_PATTERN = re.compile(
    rf"\b(?P<start>(?:{_MONTH_PATTERN}\s+)?\d{{4}})\s*(?:-|–|to)?\s*(?P<end>(?:{_MONTH_PATTERN}\s+)?\d{{4}}|present|current)\b",
    re.IGNORECASE,
)

_EXP_PATTERNS = [
    re.compile(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years|year|yrs|yr)"),
    re.compile(r"over\s+(\d+(?:\.\d+)?)\s*(?:years|year|yrs|yr)"),
    re.compile(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*(?:years|year|yrs|yr)"),
]

_EDUCATION_PATTERNS: List[Tuple[str, List[str]]] = [
    ("phd", [r"\bph\.?d\b", r"\bdoctorate\b"]),
    (
        "master",
        [
            r"\bmaster'?s?\b",
            r"\bm\s*\.?\s*tech\b",
            r"\bm\s*\.?\s*s\b",
            r"\bmba\b",
            r"\bm\s*\.?\s*e\b",
        ],
    ),
    (
        "bachelor",
        [
            r"\bbachelor'?s?\b",
            r"\bb\s*\.?\s*tech\b",
            r"\bb\s*\.?\s*e\b",
            r"\bb\s*\.?\s*sc\b",
            r"\bbs\b",
        ],
    ),
    ("diploma", [r"\bdiploma\b"]),
    ("associate", [r"\bassociate\b"]),
]

_EDUCATION_CONTEXT_TERMS = {
    "college",
    "university",
    "school",
    "institute",
    "bachelor",
    "master",
    "phd",
    "cgpa",
    "gpa",
    "hsc",
    "sslc",
    "education",
}

_WORK_CONTEXT_TERMS = {
    "intern",
    "internship",
    "engineer",
    "developer",
    "software",
    "worked",
    "work",
    "experience",
    "company",
    "employment",
    "role",
}


def _normalize_for_search(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


@lru_cache(maxsize=1)
def load_taxonomy() -> Dict[str, List[str]]:
    if config.SKILL_TAXONOMY_PATH.exists():
        return json.loads(config.SKILL_TAXONOMY_PATH.read_text(encoding="utf-8"))
    return {}


def _fallback_spacy_skills(text: str) -> List[str]:
    import spacy

    nlp = spacy.load("en_core_web_sm")
    doc = nlp(text)
    labels = {"ORG", "PRODUCT"}
    skills = sorted({ent.text.lower().strip() for ent in doc.ents if ent.label_ in labels and ent.text.strip()})
    return skills


def extract_skills(text: str, taxonomy: Dict[str, List[str]]) -> List[str]:
    if not taxonomy:
        return _fallback_spacy_skills(text)

    lower_text = text.lower()
    found: List[str] = []
    for canonical, aliases in taxonomy.items():
        variants = [canonical, *(aliases or [])]
        for alias in variants:
            alias_pattern = re.escape(alias.lower())
            if re.search(rf"\b{alias_pattern}\b", lower_text):
                found.append(canonical.lower())
                break
    return sorted(set(found))


def _merge_ranges(ranges: List[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda x: x[0])
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def extract_experience(text: str) -> float:
    lower_text = text.lower()
    normalized_text = _normalize_for_search(text)

    strategy1 = 0.0
    for pattern in _EXP_PATTERNS:
        for match in pattern.findall(lower_text):
            if isinstance(match, tuple):
                values = [float(v) for v in match if v]
                if values:
                    strategy1 = max(strategy1, max(values))
            else:
                strategy1 = max(strategy1, float(match))

    ranges: List[Tuple[datetime, datetime]] = []
    now = datetime.utcnow()
    for match in _DATE_PATTERN.finditer(normalized_text):
        start_raw = match.group("start")
        end_raw = match.group("end")
        ctx_start = max(0, match.start() - 80)
        ctx_end = min(len(normalized_text), match.end() + 80)
        context = normalized_text[ctx_start:ctx_end]

        has_edu_context = any(term in context for term in _EDUCATION_CONTEXT_TERMS)
        has_work_context = any(term in context for term in _WORK_CONTEXT_TERMS)

        # Ignore academic periods unless the same window also looks like work history.
        if has_edu_context and not has_work_context:
            continue

        try:
            start_dt = date_parser.parse(start_raw, default=datetime(1900, 1, 1))
            if end_raw.lower() in {"present", "current"}:
                end_dt = now
            else:
                end_dt = date_parser.parse(end_raw, default=datetime(1900, 1, 1))
            if start_dt > now:
                continue
            if end_dt > now:
                end_dt = now
            if end_dt > start_dt:
                ranges.append((start_dt, end_dt))
        except Exception:
            continue

    strategy2 = 0.0
    for start_dt, end_dt in _merge_ranges(ranges):
        strategy2 += (end_dt - start_dt).days / 365.25

    return round(max(strategy1, strategy2), 2)


def extract_education(text: str) -> str:
    lower_text = text.lower()
    normalized_text = _normalize_for_search(text)

    education_checks = [
        ("phd", [r"\bph\s*d\b", r"\bdoctorate\b"]),
        (
            "master",
            [
                r"\bmaster'?s?\b",
                r"\bm\s*\.?\s*tech\b",
                r"\bm\s*\.?\s*s\b",
                r"\bm\s*\.?\s*sc\b",
                r"\bmba\b",
                r"\bm\s*\.?\s*e\b",
            ],
        ),
        (
            "bachelor",
            [
                r"\bbachelor'?s?\b",
                r"\bb\s*\.?\s*tech\b",
                r"\bb\s*\.?\s*e\b",
                r"\bb\s*\.?\s*sc\b",
                r"\bb\s*\.?\s*s\b",
                r"\bbs\b",
            ],
        ),
        ("diploma", [r"\bdiploma\b"]),
        ("associate", [r"\bassociate\b"]),
    ]

    for label, patterns in education_checks:
        for pattern in patterns:
            if re.search(pattern, lower_text) or re.search(pattern, normalized_text):
                return label
    return "none"


def extract_all(text: str, taxonomy: Dict[str, List[str]]) -> ExtractionResult:
    return ExtractionResult(
        skills=extract_skills(text, taxonomy),
        experience_years=extract_experience(text),
        education_level=extract_education(text),
    )


def extract_batch(texts: List[str], taxonomy: Dict[str, List[str]]) -> List[ExtractionResult]:
    return [extract_all(text, taxonomy) for text in texts]
