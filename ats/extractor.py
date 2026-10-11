"""Deterministic extraction of experience, education, candidate name and skills from resume text.

Input text is the parser's normalized form: lowercase ASCII, one source line per line.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from . import config
from .schema import ExtractionResult
from .skills import scan_taxonomy

# ----------------------------------------------------------------------------- education levels

EDUCATION_LEVELS: Dict[str, int] = {
    "phd": 5,
    "master": 4,
    "bachelor": 3,
    "associate": 2,
    "diploma": 2,
    "school": 1,
    "unknown": 0,
    # accepted spellings for a *required* education level
    "any": 0,
    "none": 0,
    "doctorate": 5,
    "masters": 4,
    "master's": 4,
    "postgraduate": 4,
    "pg": 4,
    "bachelors": 3,
    "bachelor's": 3,
    "graduate": 3,
    "undergraduate": 3,
    "ug": 3,
    "degree": 3,
    "associate's": 2,
    "high school": 1,
}
CANONICAL_LEVEL = {"phd": "phd", "doctorate": "phd", "master": "master", "masters": "master", "master's": "master",
                   "postgraduate": "master", "pg": "master", "bachelor": "bachelor", "bachelors": "bachelor",
                   "bachelor's": "bachelor", "graduate": "bachelor", "undergraduate": "bachelor", "ug": "bachelor",
                   "degree": "bachelor", "associate": "associate", "associate's": "associate", "diploma": "diploma",
                   "school": "school", "high school": "school", "any": "any", "none": "any", "": "any"}


def normalize_required_education(value: str) -> Optional[str]:
    """Map a requested education level to a canonical label, or None if unrecognised."""
    return CANONICAL_LEVEL.get(re.sub(r"\s+", " ", (value or "").strip().lower()))


# ----------------------------------------------------------------------------- sections

_SECTION_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("education", re.compile(r"^(?:educational|academic|education|scholastic|qualifications?)\b.{0,30}$|"
                             r"^(?:education|academic|educational)\s*(?:qualifications?|details|background|profile|"
                             r"credentials|history|chronicle)?\s*:?$")),
    ("experience", re.compile(r"^(?:work|professional|employment|career|job|industry|relevant|organi[sz]ational)?\s*"
                              r"(?:experience|history|employment)(?:\s*(?:details|summary|history|record))?\s*:?$|"
                              r"^experience\b.{0,25}$|^work\s+history.*$|^employment\b.{0,25}$")),
    ("projects", re.compile(r"^(?:academic|key|major|minor|personal|professional|college|final\s+year)?\s*"
                            r"projects?(?:\s*(?:details|undertaken|handled|experience|work|summary))?\s*:?$")),
    ("internship", re.compile(r"^(?:internships?|industrial\s+training|in-?plant\s+training|trainings?)\b.{0,25}$")),
    ("skills", re.compile(r"^(?:technical|key|core|it|computer|software|professional)?\s*(?:skills?|skill\s*set|"
                          r"competenc(?:y|ies)|expertise)\b.{0,25}$")),
    ("summary", re.compile(r"^(?:professional|career|executive)?\s*(?:summary|profile|objective|about\s+me|synopsis)"
                           r"\b.{0,15}$")),
    ("other", re.compile(r"^(?:certifications?|certificates?|achievements?|awards?|personal\s+(?:details|information|"
                         r"profile|data)|declaration|hobbies|interests|languages\s+known|references?|"
                         r"extra[\s-]?curricular.*|co[\s-]?curricular.*|strengths?)\b.{0,20}$")),
]


def _collapse_spaced_letters(line: str) -> str:
    # "e d u c a t i o n" -> "education" (decorative letter spacing seen in many PDFs)
    if re.fullmatch(r"(?:[a-z&] )+[a-z&]", line):
        return line.replace(" ", "")
    return line


def split_sections(text: str) -> List[Tuple[str, str]]:
    """Return [(section, line)] where section is the most recent heading (or 'unknown')."""
    out: List[Tuple[str, str]] = []
    current = "unknown"
    for raw in text.splitlines():
        line = raw.strip()
        probe = _collapse_spaced_letters(re.sub(r"[^a-z&/\- ]", " ", line).strip())
        probe = re.sub(r"\s+", " ", probe)
        if 0 < len(probe) <= 45 and len(probe.split()) <= 5:
            for name, pattern in _SECTION_PATTERNS:
                if pattern.match(probe):
                    current = name
                    break
        out.append((current, line))
    return out


# ----------------------------------------------------------------------------- experience

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
           "nov": 11, "dec": 12}
_MONTH_RE = r"(?:jan(?:uary)?|feb(?:ruary|uary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|" \
            r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE = (
    rf"(?:\d{{1,2}}(?:st|nd|rd|th)?\s*[\-\s']?\s*(?:{_MONTH_RE})\.?,?\s*[\-\s]?\s*\d{{4}}"  # 20th june 2011, 09-may-2014, 17'sep 2018
    rf"|(?<![\d.])\d{{4}}\s+(?:{_MONTH_RE})\b\.?"  # 2023 jun (year first)
    rf"|(?:{_MONTH_RE})\.?\s*(?:'|[/,]|-(?=\s*\d{{4}}))?\s*(?:\d{{1,2}}(?:st|nd|rd|th)?,?\s*)?(?:\d{{4}}|\d{{2}})"  # jan 2020, jan'20, feb - 2015
    r"|\d{1,2}\s*[/.\-]\s*\d{1,2}\s*[/.\-]\s*\d{4}"  # 18-04-2021
    r"|\d{1,2}\s*[/.\-]\s*\d{4}"  # 04/2021
    r"|(?<![\d.])\d{4}(?!\d|\.\d))"  # 2021 (a sentence-ending "2021." is fine; "2021.5" is not)
)
_PRESENT = r"(?:present|current(?:ly)?|now|till\s*(?:date|now)|to\s*date|date|ongoing|today|continuing)"
_RANGE = re.compile(
    rf"(?P<start>{_DATE})\s*(?:(?P<sep>-+|~|to|till|until|through|upto)\s*(?P<end>{_DATE}|{_PRESENT})"
    rf"|\s+(?P<end2>{_PRESENT}|(?:{_MONTH_RE})\.?\s*(?:'|[/,]|-(?=\s*\d{{4}}))?\s*(?:\d{{4}}|\d{{2}})(?!\d)))"
)
_DATE_ONLY = re.compile(_DATE)
_YEAR_ONLY = re.compile(r"\s*(?:19|20)\d\d\s*")
_EDU_LINE = re.compile(
    r"\b(?:universit|college|institute|school|academy|b\.?\s?tech|m\.?\s?tech|b\.\s?e\b|bachelor|master'?s|degree|"
    r"diploma|polytechnic|cgpa|gpa|percentage|hsc|ssc|sslc|cbse|icse|matric|xii|10th|12th|graduat|"
    r"class(?:es)?\s+(?:x|xi|xii|10|11|12)\b|passed\s+class|"
    r"b\.?\s?sc|m\.?\s?sc|b\.?\s?com|m\.?\s?com|mba|mca|bca|ph\.?\s?d|semester|pursuing)"
)
# Employment words: a line containing these is a job even if it also names a school/college.
_JOB_LINE = re.compile(
    r"\b(?:m/s|pvt|ltd|limited|technologies|solutions|services|inc|llp|corporation|company|engineer|developer|"
    r"manager|executive|officer|analyst|consultant|associate|supervisor|lead|designation|role|employer|"
    r"teacher|lecturer|professor|faculty|tutor|trainer|accountant|technician|operator|coordinator|specialist|"
    r"administrator|assistant|head|worked|working|employed|fellow|fellowship|researcher|scientist)\b"
)
# Professional qualifications awarded by an examining body: dated like jobs but are education
# ("Chartered Accountant (CA) - ICAI | June 2015 - May 2022").
_QUALIFICATION_LINE = re.compile(r"\b(?:icai|icwai|icmai|icsi|acca|institute\s+of\s+(?:chartered|cost|company)|"
                                 r"ca\s+(?:inter|intermediate|final|foundation)|board\s+of\s+(?:intermediate|secondary|"
                                 r"higher))\b")
# College / volunteer activities that carry role words ("coordinator", "head") and date ranges.
_ACTIVITY_LINE = re.compile(r"\b(?:committee|club|alumni|student|class\s+representative|fest|nss|ncc|volunteer\w*|"
                            r"chapter|council|cultural|sports|hostel|placement\s+cell)\b")
_EMPLOYER_WORDS = re.compile(r"\b(?:pvt|private|ltd|limited|inc|llp|llc|corporation|corp|gmbh|m/s)\b")
_INTERN_LINE = re.compile(r"\b(?:intern|internship|apprentice(?:ship)?|industrial\s+training|summer\s+training)\b")

_EXP_NUMBER = r"(\d{1,2}(?:\.\d{1,2})?)\s*\+?\s*(?:years?|yrs?|yr)\.?(?:\s*(?:and\s*|&\s*)?(\d{1,2})\s*(?:months?|mos?|mths?|m)\b)?"
_EXPLICIT_PATTERNS = [
    # "5+ years of (professional|hands-on ...) experience", "3 years 6 months experience in"
    re.compile(_EXP_NUMBER + r"\s*(?:of\s+)?(?:[a-z\-/&]+\s+){0,4}?(?:experience|exp\b|work\s+experience)"),
    # "experience: 5 years", "total experience of 4.5 yrs", "experience - 2 yrs 6 months"
    re.compile(r"\b(?:experience|exp)\s*(?:of|:|-|is|in\s+total|overall)?\s*(?:about|around|over|nearly|approx\.?|"
               r"approximately|more\s+than|almost)?\s*:?\s*" + _EXP_NUMBER),
    # "over 10 years in the industry", "10+ years in it industry"
    re.compile(r"(?:over|more\s+than|nearly|around|about)\s+" + _EXP_NUMBER + r"\s+in\s+(?:the\s+)?(?:it\s+|software\s+)?industry"),
    # "SQL developer with 10 years in database ...", "engineer with over 6 years of hands-on ..."
    re.compile(r"\bwith\s+(?:over\s+|more\s+than\s+|nearly\s+|around\s+|about\s+|almost\s+)?" + _EXP_NUMBER +
               r"\s+(?:of|in)\b"),
]
_EXPLICIT_MONTHS = re.compile(r"(\d{1,2})\s*(?:months?|mos?)\s*(?:of\s+)?(?:[a-z\-/&]+\s+){0,3}?experience")


# Per-screening-run override ("resumes received on"), set by the worker for the file it is analysing.
_REFERENCE_OVERRIDE: ContextVar[Optional[datetime]] = ContextVar("ats_reference_date", default=None)


def reference_date() -> datetime:
    """'Present'/'current' in a resume means this date: the run's reference date if given, else
    ATS_REFERENCE_DATE, else today."""
    return _REFERENCE_OVERRIDE.get() or config.REFERENCE_DATE or datetime.now()


@contextmanager
def using_reference_date(value: Optional[datetime]):
    token = _REFERENCE_OVERRIDE.set(value)
    try:
        yield
    finally:
        _REFERENCE_OVERRIDE.reset(token)


def _parse_date(token: str, is_end: bool) -> Optional[datetime]:
    token = token.strip().lower().replace("'", " ")
    token = re.sub(rf"^({_MONTH_RE})\.?\s*[-/,]\s*", r"\1 ", token)  # "feb - 2015" -> "feb 2015"
    now = reference_date()
    if re.fullmatch(_PRESENT, token):
        return now
    m = re.match(rf"\d{{1,2}}(?:st|nd|rd|th)?\s*[\-\s]?\s*({_MONTH_RE})\.?,?\s*[\-\s]?\s*(\d{{4}})$", token)
    if m:
        return _valid(int(m.group(2)), _MONTHS[m.group(1)[:3]])
    m = re.match(rf"(\d{{4}})\s+({_MONTH_RE})\.?$", token)  # "2023 jun"
    if m:
        return _valid(int(m.group(1)), _MONTHS[m.group(2)[:3]])
    m = re.match(rf"({_MONTH_RE})\.?\s*(?:\d{{1,2}}(?:st|nd|rd|th)?,?\s*)?(\d{{4}}|\d{{2}})$", token)
    if m:
        month = _MONTHS[m.group(1)[:3]]
        year = int(m.group(2))
        if year < 100:
            year += 2000 if year <= now.year % 100 + 1 else 1900
        return _valid(year, month)
    m = re.match(r"(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{4})$", token)
    if m:
        a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        month = b if b <= 12 else a  # dd-mm-yyyy (Indian/European) unless impossible
        return _valid(year, month)
    m = re.match(r"(\d{1,2})\s*[/.\-]\s*(\d{4})$", token)
    if m:
        return _valid(int(m.group(2)), int(m.group(1)))
    m = re.match(r"(\d{4})$", token)
    if m:
        return _valid(int(m.group(1)), 12 if is_end else 1)
    return None


def _valid(year: int, month: int) -> Optional[datetime]:
    now = reference_date()
    if not (1965 <= year <= now.year) or not (1 <= month <= 12):
        return None
    dt = datetime(year, month, 1)
    return min(dt, now)


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


_VERSION_BEFORE = re.compile(r"\b(?:server|windows|office|studio|version|ver|v|excel|oracle|sql|tally|sap|autocad|"
                             r"vista|xp|ios|android|java|python|php|dynamics|navision|catia|creo|solidworks)\s*"
                             r"(?:from\s+)?$")
_EMPLOYMENT_SECTIONS = ("experience", "summary", "unknown")


def _range_matches(lines: List[Tuple[str, str]]):
    """Yield (line_index, match, line_text) for date ranges, including ranges split across two lines
    by table layouts ("4yrs (2004" / "to 2007)")."""
    for idx, (_, line) in enumerate(lines):
        for m in _RANGE.finditer(line):
            yield idx, m, line
        if idx + 1 < len(lines):
            nxt = lines[idx + 1][1]
            joined = f"{line} {nxt}"
            for m in _RANGE.finditer(joined, max(0, len(line) - 40)):
                if m.start() < len(line) < m.end():
                    yield idx, m, joined
            # Date columns: a start date alone on one line and the end date alone on the next
            # ("06/08/2021" / "13/01/2023"). Year-only pairs are excluded: those are usually lists.
            if (_DATE_ONLY.fullmatch(line.strip(" .,:|")) and not re.fullmatch(r"\d{4}", line.strip(" .,:|"))
                    and (_DATE_ONLY.fullmatch(nxt.strip(" .,:|")) or re.fullmatch(_PRESENT, nxt.strip(" .,:|")))
                    and not re.fullmatch(r"\d{4}", nxt.strip(" .,:|"))):
                pair = f"{line.strip(' .,:|')} - {nxt.strip(' .,:|')}"
                m = _RANGE.fullmatch(pair)
                if m:
                    yield idx, m, pair


def _intern_entry_header(lines: List[Tuple[str, str]], idx: int) -> bool:
    """True if the entry this date line belongs to is titled as an internship: looks back up to
    3 lines ("Student Intern" / "Globex Ltd" / "Feb 24 - Jun 24"), stopping at the previous dated entry."""
    for back in range(1, 4):
        j = idx - back
        if j < 0:
            break
        prev = lines[j][1]
        if _RANGE.search(prev):
            break
        if _INTERN_LINE.search(prev):
            return True
    return False


def employment_ranges(text: str) -> List[Tuple[datetime, datetime]]:
    """Date ranges that look like employment (not education, projects, internships, versions)."""
    lines = split_sections(text)
    ranges: List[Tuple[datetime, datetime]] = []
    for idx, m, line in _range_matches(lines):
        section = lines[idx][0]
        start_raw = m.group("start")
        end_raw = m.group("end") or m.group("end2")
        sep = m.group("sep")
        # Year-only ranges need an explicit separator; bare "2015 2017" is usually a list of years.
        if sep is None and re.fullmatch(r"\d{4}", start_raw.strip()) and not re.fullmatch(_PRESENT, end_raw.strip()):
            continue
        if _VERSION_BEFORE.search(line[: m.start()]):  # "SQL Server 2000-2019"
            continue
        neighbourhood = " ".join(l for _, l in lines[max(0, idx - 1): idx + 2])
        if _INTERN_LINE.search(line) or (_INTERN_LINE.search(neighbourhood) and not _JOB_LINE.search(line)):
            continue
        if _intern_entry_header(lines, idx) or _QUALIFICATION_LINE.search(line):
            continue
        if _ACTIVITY_LINE.search(line) and not _EMPLOYER_WORDS.search(line):
            continue  # "Alumni Committee Coordinator, Regional Institute (Dec 2021 - May 2023)": student activity
        if (_YEAR_ONLY.fullmatch(start_raw) and _YEAR_ONLY.fullmatch(end_raw or "") and not _JOB_LINE.search(line)
                and _EDU_LINE.search(" ".join(l for _, l in lines[max(0, idx - 2): idx + 3]))):
            continue  # a bare "2016 - 2020" within two lines of a degree, college or CGPA is the course, not a job
        if section not in _EMPLOYMENT_SECTIONS:
            # Headings are sometimes mis-detected (two-column PDFs); keep a range outside an employment
            # section only when its own line (or neighbours, for 'other') clearly describe a job.
            if section == "education":
                # The title/employer often sits on the 1-2 lines above the dates; nothing next to them may be education.
                evidence = " ".join([line, *(l for _, l in lines[max(0, idx - 2): idx])])
                if not _JOB_LINE.search(evidence) or _EDU_LINE.search(neighbourhood):
                    continue
            else:  # the employer/title usually sits on the 1-3 lines above the dates
                evidence = " ".join([neighbourhood, *(l for _, l in lines[max(0, idx - 3): idx])])
            if not _JOB_LINE.search(evidence) or _EDU_LINE.search(line):
                continue
            # Internship and catch-all sections (responsibilities, achievements, personal details) hold
            # many dated non-jobs ("Fest Coordinator"); there a company marker is required.
            if section in ("internship", "other") and not _EMPLOYER_WORDS.search(evidence):
                continue
        else:
            if _EDU_LINE.search(line) and not _JOB_LINE.search(line):
                continue
            if _EDU_LINE.search(neighbourhood) and not _JOB_LINE.search(neighbourhood):
                continue
        start = _parse_date(start_raw, is_end=False)
        end = _parse_date(end_raw, is_end=True)
        # "2019 - 2021" means about two years: compare year-only bounds at the same month.
        if start and end and re.fullmatch(r"\d{4}", start_raw.strip()) and re.fullmatch(r"\d{4}", end_raw.strip()):
            end = end.replace(month=1) if end.year > start.year else end
        if start is None or end is None or end <= start:
            continue
        if (end - start).days > 45 * 365:
            continue
        ranges.append((start, end))
    return ranges


# Statements about an employer/firm ("a firm having over 40 years of experience", "we bring 25+ years").
_THIRD_PARTY = re.compile(r"\b(?:we|our|us|firm|company|organi[sz]ation|group|he|she|his|her|they|their|client|"
                          r"clients|bank|institute|brand|legacy|established|founded|since)\b[^.\n]{0,60}$")


def explicit_experience(text: str) -> float:
    """Largest self-declared total ("5+ years of experience", "experience: 2 yrs 6 months")."""
    best = 0.0
    for pattern in _EXPLICIT_PATTERNS:
        for m in pattern.finditer(text):
            years = float(m.group(1))
            months = float(m.group(2)) / 12.0 if m.group(2) else 0.0
            value = years + months
            before = text[max(0, m.start() - 80): m.start()]
            if re.search(r"\b(?:age|aged)\b[^\n]{0,15}$", before) or _THIRD_PARTY.search(before):
                continue
            if re.search(r"\b(?:he|she|his|her|they|their)\b", text[m.end(): m.end() + 40]):
                continue  # third-person bio of someone else ("... 30 years industry experience. He founded")
            if 0 < value <= 45:
                best = max(best, value)
    if best == 0.0:
        for m in _EXPLICIT_MONTHS.finditer(text):
            best = max(best, min(float(m.group(1)), 60.0) / 12.0)
    return best


_DURATION_LINE = re.compile(r"^\s*(?:period|duration|tenure|total\s+period)\s*[:\-]+\s*(\d{1,2}(?:\.\d)?)\s*"
                            r"(?:\+\s*)?(?:years?|yrs?)(?:\s*(\d{1,2})\s*(?:months?|mos?))?\b")


def per_job_durations(text: str) -> float:
    """Sum of per-job "Period: 4 years" lines inside the experience section (tabular resumes)."""
    total = 0.0
    for section, line in split_sections(text):
        if section != "experience":
            continue
        m = _DURATION_LINE.match(line)
        if m:
            total += float(m.group(1)) + (float(m.group(2)) / 12.0 if m.group(2) else 0.0)
    return total if total <= 45 else 0.0


def extract_experience(text: str) -> float:
    """Total professional experience in years (overlaps merged, education/projects/internships excluded)."""
    # GPA values ("7.77/10", "cgpa: 8.1") are removed first so they are never read as years. The word itself
    # stays: it marks "2016 - 2020" on the neighbouring line as a degree, not a job.
    clean = re.sub(r"\b(c?gpa|cumulative\s+gpa|cumulative\s+grade|grade)\b[:\s\-]*[\d.]+(?:\s*(?:out\s+of|/)\s*[\d.]+)?",
                   r" \1 ", text)
    clean = re.sub(r"\b\d{1,2}\.\d{1,2}\s*/\s*10\b", " ", clean)
    from_ranges = sum((end - start).days / 365.25 for start, end in _merge_ranges(employment_ranges(clean)))
    explicit = explicit_experience(clean)
    durations = per_job_durations(clean)
    return round(max(0.0, min(45.0, max(from_ranges, explicit, durations))), 2)


# ----------------------------------------------------------------------------- education

_FIELD = (r"(?:computer|comp|cs|cse|it|ece|eee|ece|mech|mechanical|electrical|electronics|civil|chemical|"
          r"information|software|data|science|sciences|engineering|engg|technology|tech|mathematics|maths|math|"
          r"physics|chemistry|biology|biotech|biotechnology|commerce|business|management|finance|accounting|"
          r"economics|statistics|arts|english|history|psychology|automobile|aeronautical|aerospace|instrumentation|"
          r"telecommunication|communication|production|industrial|textile|marine|agriculture|pharmacy|nursing|"
          r"applications|vlsi|embedded|power|structural|thermal|manufacturing|design|marketing|hr|human|"
          r"international|supply|logistics|operations|analytics|ai|artificial|machine|cyber|network|"
          r"electronic|education|social|mass|journalism|law|architecture|food|environmental|petroleum|mining|"
          r"metallurgy|geology|zoology|botany|microbiology|biochemistry|genetics|electrical)")
# Stricter field list for undotted abbreviations ("be", "me", "ms", "ba", "bs") that are also English words.
_FIELD_STRICT = (r"(?:\(\s*[a-z&/.\- ]{2,30}\)|computer\s+(?:science|engineering|applications)|cse|ece|eee|mech|"
                 r"mechanical|electrical|electronics|civil|chemical|information\s+technology|engineering|engg|"
                 r"mathematics|maths|physics|chemistry|biotech|biotechnology|commerce|automobile|aeronautical|"
                 r"instrumentation|telecommunication|vlsi|embedded|structural|thermal|manufacturing|production\s+"
                 r"engineering|industrial\s+engineering|textile|data\s+science|finance|economics|statistics|english|"
                 r"psychology|accounting|power\s+(?:systems|electronics)|communication\s+systems)")
_SOFTWARE_AFTER = r"(?!\s*[-.]?\s*(?:office|word|excel|powerpoint|ppt|access|outlook|project|teams|dos|windows|" \
                  r"visio|paint|sql|azure|dynamics|365|fabric|sharepoint|navision)\b)"
_DEGREE_CONTEXT = re.compile(r"\b(?:universit|college|institute|degree|graduat|cgpa|gpa|percentage|passed|"
                             r"passing|batch|semester|pursuing|completed|specialization|major|honou?rs|school\s+of)")

# (level, pattern, needs_context): needs_context patterns are abbreviations that are also
# ordinary words ("me", "be", "ms", "ma", "ba"); they count only when followed by a field of
# study or when the same line carries academic context.
_EDU_RULES: List[Tuple[str, re.Pattern, bool]] = [
    ("phd", re.compile(r"\bph\.?\s?d\b|\bdoctor\s+of\s+philosophy\b|\bdoctorate\b|\bd\.\s?phil\b"), False),
    ("master", re.compile(r"\bmaster'?s?\s+(?:of|in)\s+(?:" + _FIELD + r"|philosophy|laws?|fine\s+arts|public|"
                          r"pharmacy)\b|\bmaster'?s?\s+degree\b|(?<!scrum )\bmasters\b(?!\s+(?:data|cylinder))|"
                          r"\bpost\s*-?\s*graduat(?:e|ion)\b(?!\s*(?:diploma|certificat|course|program))"), False),
    ("master", re.compile(r"\bm\.?\s?tech\b|\bmba\b|\bmca\b|\bm\.\s?sc\b|\bmsc\b|\bm\.?\s?com\b|\bpgdm\b|\bm\.\s?pharm|"
                          r"\bllm\b|\bm\.\s?arch\b|\bm\.\s?e\b\.?|\bm\.\s?s\b\.?" + _SOFTWARE_AFTER + r"|\bm\.\s?a\b\.?"
                          r"(?=\s*(?:\(|in\b|-|,)?\s*" + _FIELD + ")"), False),
    ("master", re.compile(r"\b(?:me|ms|ma|m\s?s|m\s?e)\b" + _SOFTWARE_AFTER + r"\s*(?:in\b|-|,|:)?\s*" + _FIELD_STRICT + r"(?![a-z])"), False),
    ("master", re.compile(r"(?:^|[|,;(])\s*(?:me|ms|m\.?e\.?|m\.?s\.?)\s*(?:$|[|,;)])"), True),
    ("bachelor", re.compile(r"\bbachelor'?s?\s+(?:of|in)\b|\bbachelor'?s?\s+degree\b|\bbachelor'?s?\s+" + _FIELD +
                            r"|\bunder\s*-?\s*graduat(?:e|ion)\b|\bundergraduate\b|\bgraduation\b(?=.{0,40}"
                            r"(?:b\.|bachelor|engineering|science|commerce|arts|universit|college))"), False),
    ("bachelor", re.compile(r"\bb\.?\s?tech\b|\bb\.\s?sc\b|\bbsc\b|\bb\.?\s?com\b|\bbca\b|\bbba\b|\bbbm\b|\bb\.?\s?arch\b|"
                            r"\bb\.?\s?pharm|\bllb\b|\bmbbs\b|\bbds\b|\bb\.\s?ed\b|\bb\.\s?e\b\.?|\bb\.\s?s\b\.?|"
                            r"\bb\.\s?a\b\.?(?=\s*(?:\(|in\b|-|,)?\s*" + _FIELD + ")"), False),
    ("bachelor", re.compile(r"\b(?:be|bs|ba|b\s?e|b\s?s)\b\s*(?:in\b|-|,|:)?\s*" + _FIELD_STRICT + r"(?![a-z])"), False),
    ("bachelor", re.compile(r"(?:^|[|,;(])\s*(?:be|bs|b\.?e\.?|b\.?s\.?)\s*(?:$|[|,;)])"), True),
    ("bachelor", re.compile(r"\bbachelor'?s?\b"), True),
    # "BE Computer at Sample University", "ME Power Electronics, Anna University": broad field list, but only
    # on lines that also carry academic context (university/college/CGPA...)
    ("bachelor", re.compile(r"\b(?:be|b\s?e)\b\s*(?:in\b|-|,|:)?\s*" + _FIELD + r"\b"), True),
    ("master", re.compile(r"\b(?:me|m\s?e)\b\s*(?:in\b|-|,|:)?\s*" + _FIELD + r"\b"), True),
    # "Completed Computer Science and Engineering with 7.10 CGPA" (degree named only by its course)
    ("bachelor", re.compile(r"^(?!.*\b(?:diploma|polytechnic|iti|certificat\w*|course)\b).*\b(?:completed|graduated|"
                            r"graduating)\b[^\n]{0,30}\b(?:engineering|computer\s+science|information\s+technology)\b"),
     False),
    ("associate", re.compile(r"\bassociate'?s?\s+degree\b|\bassociate\s+of\s+(?:arts|science|applied)"), False),
    ("diploma", re.compile(r"\bdiploma\b|\bpolytechnic\b|\bpost\s*-?\s*graduate\s+diploma\b|\bpg\s*diploma\b"), False),
    ("school", re.compile(r"\b(?:hsc|ssc|sslc|cbse|icse|matriculation|higher\s+secondary|senior\s+secondary|"
                          r"secondary\s+school|high\s+school|12th|10th|class\s+(?:x|xii|10|12)\b|xii\b)"), False),
]
_MARITAL = re.compile(r"\b(?:marital|status)\b[^\n]{0,15}$")


def extract_education(text: str) -> str:
    """Highest education level with textual evidence: phd/master/bachelor/associate/diploma/school/unknown."""
    best = "unknown"
    for section, line in split_sections(text):
        for level, pattern, needs_context in _EDU_RULES:
            if EDUCATION_LEVELS[level] <= EDUCATION_LEVELS[best]:
                continue
            for m in pattern.finditer(line):
                if level == "bachelor" and _MARITAL.search(line[: m.start()]):
                    continue  # "Marital status: Bachelor"
                if needs_context and not (section == "education" or (_DEGREE_CONTEXT.search(line) and len(line) < 160)):
                    continue
                best = level
                break
    return best


# ----------------------------------------------------------------------------- candidate name

_NAME_STOP = re.compile(
    r"\b(?:resume|curriculum|vitae|cv|bio-?data|profile|summary|objective|contact|address|email|e-mail|mobile|"
    r"phone|linkedin|github|career|education|experience|skills|declaration|personal|details|page|india|"
    r"engineer|developer|manager|analyst|consultant|executive|officer|specialist|designer|intern|accountant|"
    r"technician|supervisor|architect|administrator|assistant|trainee|fresher|professional|associate|lead|"
    r"director|head|scientist|tester|operator|coordinator|representative|ltd|pvt|limited|inc|llc|"
    r"technologies|solutions|services|systems|university|college|school|institute|software|data|sales|"
    r"marketing|finance|hr|logistics|purchase|quality|production|mechanical|electrical|civil|application|"
    r"for|post|of|the|and|to|in|at|years?|role|position|dear|sir|madam|respected|subject|"
    r"educational|qualifications?|academic|technical|work|professional|achievements?|certifications?|"
    r"references?|hobbies|languages|projects?|training|internships?|strengths?|"
    r"nagar|street|road|colony|layout|district|dist|village|taluk|near|opp|flat|apartments?|sector|"
    r"phase|cross|main|block|floor|house|plot|pin|city|state)\b",
    re.IGNORECASE,
)
_COMMON_SINGLE_WORDS = {"experienced", "dedicated", "motivated", "objective", "summary", "overview", "welcome",
                         "introduction", "about", "hello", "highlights", "achievements", "work", "projects",
                         "certifications", "references", "languages", "hobbies", "strengths", "contact", "address"}
_NAME_LABEL = re.compile(r"^\s*(?:candidate\s+)?(?:full\s+)?name\s*[:\-]\s*(.+)$", re.IGNORECASE)
_NAME_TOKEN = re.compile(r"^[A-Za-z][A-Za-z.'\-]*$")
_FILENAME_NOISE = re.compile(
    r"\b(?:resume|cv|naukri|updated|update|latest|final|new|copy|profile|biodata|my|the|doc|pdf|docx|fresher|"
    r"with|exp|experience|yrs?|years?)\b", re.IGNORECASE)


_DEGREE_TOKEN = re.compile(r"^(?:b\.?e\.?|b\.?tech|m\.?tech|m\.?e\.?|m\.?s\.?|mba|mca|bca|bba|b\.?sc|m\.?sc|"
                           r"b\.?com|m\.?com|ph\.?d|ca|cma|cfa|pmp|acca|cpa)$", re.IGNORECASE)


def _title(name: str) -> str:
    parts = []
    for tok in name.split():
        if any(c.islower() for c in tok) and any(c.isupper() for c in tok):
            parts.append(tok)
        elif "." in tok.strip("."):  # "R.ANAND" -> "R. Anand"
            parts.append(". ".join(p.capitalize() for p in tok.split(".") if p))
        else:
            parts.append(tok.capitalize())
    return " ".join(parts)


def _leading_name_tokens(line: str) -> str:
    """Name-like prefix of a header line that continues with a degree/qualification ("R.ANAND RAJ B.E (MECH)")."""
    out = []
    for tok in line.split():
        if _DEGREE_TOKEN.match(tok) or tok.startswith("(") or re.search(r"\d", tok):
            break
        if not _NAME_TOKEN.match(tok) or _NAME_STOP.fullmatch(tok):
            return ""  # the line continues with ordinary words: not a "name + qualification" header
        out.append(tok)
    return " ".join(out) if 2 <= len(out) <= 4 and len(out) < len(line.split()) else ""


def _valid_name(candidate: str, require_capitals: bool = False) -> Optional[str]:
    candidate = re.sub(r"\s+", " ", candidate.strip(" .,:;-|")).strip()
    # honorifics / professional prefixes are not part of the name: "CA. Vikram Rao", "Dr. Meena"
    candidate = re.sub(r"^(?:(?:ca|cs|cma|dr|er|mr|mrs|ms|miss|prof|adv)\.?\s+|(?:ca|dr|er|mr|mrs|ms|prof)\.)",
                       "", candidate, flags=re.IGNORECASE).strip()
    if not candidate or len(candidate) > 40 or "@" in candidate or re.search(r"\d", candidate):
        return None
    tokens = candidate.split()
    if require_capitals and not all(t[0].isupper() for t in tokens):
        return None  # header names are capitalized; lowercase lines are prose ("innovative products from")
    if not 1 <= len(tokens) <= 4 or not all(_NAME_TOKEN.match(t) for t in tokens):
        return None
    if _NAME_STOP.search(candidate):
        return None
    if re.search(r"objective|summary|profile|curriculum|resume|career|qualification|declaration|experience",
                 candidate.lower().replace(" ", "")):
        return None  # headings whose words were run together by the PDF extractor ("Careerobjective")
    letters = sum(c.isalpha() for c in candidate)
    if letters < 3 or (len(tokens) >= 3 and all(len(t.strip(".")) == 1 for t in tokens)):
        return None  # "N I P A W A R" style spacing
    return _title(candidate)


def name_from_filename(file_name: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", file_name)
    stem = re.sub(r"[\[\(\{][^\]\)\}]*[\]\)\}]", " ", stem)  # "[14yrs]", "(1)"
    stem = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)  # camelCase
    stem = re.sub(r"[_\-.+,]+", " ", stem)
    stem = _FILENAME_NOISE.sub(" ", stem)
    stem = re.sub(r"\d+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return _title(stem) if stem else file_name


def extract_name(display_text: str, file_name: str = "") -> Tuple[str, str]:
    """(name, source) where source is 'label', 'header', 'email' or 'filename'."""
    lines = [l.strip() for l in (display_text or "").splitlines() if l.strip()]
    for line in lines[:40]:
        m = _NAME_LABEL.match(line)
        if m:
            name = _valid_name(re.split(r"\s{2,}|\||,|\b(?:email|mobile|phone|dob)\b", m.group(1), flags=re.I)[0])
            if name:
                return name, "label"
    for idx, line in enumerate(lines[:8]):
        # the name is often joined with contact info on the same line: "Rohan Mehra | rohan@x.com"
        head = re.split(r"\s*(?:\||,|•|\s-\s|\s{2,})\s*", line)[0]
        name = _valid_name(head, True) or _valid_name(_leading_name_tokens(head), True)
        if not name:
            continue
        if len(name.split()) >= 2:
            return name, "header"
        # single-word names only from the very first line, which must be just that word
        if idx == 0 and head == line and len(name) >= 3 and name.lower() not in _COMMON_SINGLE_WORDS:
            # large-font headers often break the name over two lines: "KAVERI" / "MALAR JOSEPH"
            nxt = _valid_name(lines[1], True) if len(lines) > 1 else None
            if nxt and len(nxt.split()) <= 3 and lines[1].isupper() == line.isupper():
                return f"{name} {nxt}", "header"
            return name, "header"
    m = re.search(r"\b([a-z]{2,})[._]([a-z]{2,})(?:[._][a-z]{2,})?\d*@", (display_text or "").lower())
    if m:
        name = _valid_name(f"{m.group(1)} {m.group(2)}")
        if name:
            return name, "email"
    return (name_from_filename(file_name) if file_name else "Unknown"), "filename"


# ----------------------------------------------------------------------------- facade





def extract_all(text: str, taxonomy: Optional[Dict[str, List[str]]] = None, display_text: str = "",
                file_name: str = "") -> ExtractionResult:
    scan = scan_taxonomy(text)
    name, source = extract_name(display_text or text, file_name)
    return ExtractionResult(
        skills=scan.skills,
        experience_years=extract_experience(text),
        education_level=extract_education(text),
        name=name,
        name_source=source,
        skill_anchors=scan.anchors,
    )

