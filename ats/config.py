from pathlib import Path

BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
LOG_DIR = BASE_DIR / "logs"
OUTPUT_DIR = BASE_DIR / "output"
SKILL_TAXONOMY_PATH = BASE_DIR / "skill_taxonomy.json"

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DEVICE = "cpu"

PREFILTER_MIN_HITS = 1
OCR_ENABLED = False

WEIGHT_SKILLS = 0.40
WEIGHT_EXPERIENCE = 0.30
WEIGHT_SEMANTIC = 0.20
WEIGHT_EDUCATION = 0.10

DEFAULT_TOP_N = 10

for path in (CACHE_DIR, LOG_DIR, OUTPUT_DIR):
    path.mkdir(parents=True, exist_ok=True)
