from pathlib import Path
import os


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


BASE_DIR = Path(__file__).parent
# All generated data (caches, logs, saved results, failure logs) lives under DATA_DIR.
# Override with ATS_DATA_DIR to keep runtime artefacts out of the source tree.
DATA_DIR = Path(os.environ.get("ATS_DATA_DIR") or BASE_DIR)
CACHE_DIR = DATA_DIR / "cache"
LOG_DIR = DATA_DIR / "logs"
OUTPUT_DIR = DATA_DIR / "output"
RUNS_DIR = DATA_DIR / "runs"
SKILL_TAXONOMY_PATH = BASE_DIR / "skill_taxonomy.json"
SKILL_RELATIONS_PATH = BASE_DIR / "skill_relations.json"

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DEVICE = "cpu"
# The model reads at most 256 word-pieces per input, so long resumes are split into
# chunks of roughly this many words; the resume vector is the mean of its chunk vectors.
EMBEDDING_CHUNK_WORDS = 180
EMBEDDING_MAX_CHUNKS = _env_int("ATS_EMBEDDING_MAX_CHUNKS", 3)

# Semantic (MiniLM) scoring. Set ATS_USE_SEMANTIC_BULK=0 for throughput-only bulk runs.
USE_SEMANTIC_EMBEDDING_BULK = _env_bool("ATS_USE_SEMANTIC_BULK", True)
USE_SEMANTIC_EMBEDDING_SINGLE = _env_bool("ATS_USE_SEMANTIC_SINGLE", True)
# Load the model at server start so the first request is not slowed by it (default: when semantic bulk is on).
WARMUP_EMBEDDER = _env_bool("ATS_WARMUP_EMBEDDER", USE_SEMANTIC_EMBEDDING_BULK)
# Start parser worker processes at server start (avoids a slow first request on Windows).
WARMUP_PARSE_POOL = _env_bool("ATS_WARMUP_PARSE_POOL", True)

# Prefilter: resumes in which none of the job's required or preferred skills can be
# found are reported as "filtered" and excluded from ranking. Set ATS_PREFILTER=0 to rank everything.
PREFILTER_ENABLED = _env_bool("ATS_PREFILTER", True)

# OCR fallback for PDFs without a usable text layer (scanned resumes). Runs only when
# normal extraction yields almost no text, and only if the Tesseract binary is found.
OCR_ENABLED = _env_bool("ATS_OCR", True)
OCR_MIN_TEXT_CHARS = 150
OCR_MAX_PAGES = _env_int("ATS_OCR_MAX_PAGES", 4)
TESSERACT_CMD = os.environ.get("ATS_TESSERACT_CMD", "")

# PDF pages beyond this are ignored (resumes are short; bounds time spent on huge PDFs).
MAX_PDF_PAGES = _env_int("ATS_MAX_PDF_PAGES", 12)

# "Present"/"till date" in resumes is measured up to this date. Leave unset for today; set
# ATS_REFERENCE_DATE=YYYY-MM-DD when re-screening an archive of resumes collected earlier.
def _reference_date():
    from datetime import datetime

    raw = os.environ.get("ATS_REFERENCE_DATE", "").strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d") if raw else None
    except ValueError:
        return None


REFERENCE_DATE = _reference_date()

# Upload / resource limits for the HTTP API.
MAX_FILES_PER_REQUEST = _env_int("ATS_MAX_FILES_PER_REQUEST", 1000)
MAX_FILE_SIZE_MB = _env_int("ATS_MAX_FILE_SIZE_MB", 20)
MAX_REQUEST_SIZE_MB = _env_int("ATS_MAX_REQUEST_SIZE_MB", 2048)
MAX_CONCURRENT_JOBS = max(1, _env_int("ATS_MAX_CONCURRENT_JOBS", 1))
# Folder screening over HTTP is disabled unless at least one allowed root is configured
# (os.pathsep-separated list of directories).
ALLOWED_FOLDER_ROOTS = [p for p in os.environ.get("ATS_ALLOWED_FOLDER_ROOTS", "").split(os.pathsep) if p.strip()]

# Saved result files never contain resume text or embeddings.
SAVE_RESULTS_JSON = _env_bool("ATS_SAVE_RESULTS", True)

WEIGHT_SKILLS = 0.40
WEIGHT_EXPERIENCE = 0.30
WEIGHT_SEMANTIC = 0.20
WEIGHT_EDUCATION = 0.10

# Credit a job skill earns from each kind of evidence (see skills.match_skills). Exact names and
# equivalent names (aliases/acronyms) earn full credit; a specific tool that belongs to a broader
# required skill (MySQL for "SQL") earns partial credit; merely related skills earn none.
CATEGORY_MATCH_CREDIT = 0.75
# A mention that only appears as a web/profile link, a bare label on a contact line ("github | linkedin")
# or inside an organisation name ("Income Tax Department", "Tally Solutions Pvt Ltd") says little about
# proficiency. Such weak evidence is shown to the reviewer but earns this credit (0 = not counted, so it
# also does not pass the prefilter on its own).
WEAK_EVIDENCE_CREDIT = 0.0

DEFAULT_TOP_N = 10

# Bump when the corresponding algorithm changes so stale cache entries are never reused.
PARSER_VERSION = "p5"
EXTRACTOR_VERSION = "e5"
EMBEDDING_VERSION = "m2"

for path in (CACHE_DIR, LOG_DIR, OUTPUT_DIR, RUNS_DIR):
    path.mkdir(parents=True, exist_ok=True)
