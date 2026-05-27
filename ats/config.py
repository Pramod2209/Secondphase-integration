from pathlib import Path
import os

BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
LOG_DIR = BASE_DIR / "logs"
OUTPUT_DIR = BASE_DIR / "output"
RUNS_DIR = BASE_DIR / "runs"
SKILL_TAXONOMY_PATH = BASE_DIR / "skill_taxonomy.json"

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DEVICE = "cpu"

# Bulk runs can skip transformer embeddings for significantly faster throughput.
# Keep single-run semantic scoring enabled for deep analysis.
USE_SEMANTIC_EMBEDDING_BULK = os.environ.get("ATS_USE_SEMANTIC_BULK", "0") == "1"
USE_SEMANTIC_EMBEDDING_SINGLE = os.environ.get("ATS_USE_SEMANTIC_SINGLE", "1") == "1"
WARMUP_EMBEDDER = os.environ.get("ATS_WARMUP_EMBEDDER", "0") == "1"

PREFILTER_MIN_HITS = 1
OCR_ENABLED = False

WEIGHT_SKILLS = 0.40
WEIGHT_EXPERIENCE = 0.30
WEIGHT_SEMANTIC = 0.20
WEIGHT_EDUCATION = 0.10

DEFAULT_TOP_N = 10

for path in (CACHE_DIR, LOG_DIR, OUTPUT_DIR):
    path.mkdir(parents=True, exist_ok=True)
RUNS_DIR.mkdir(parents=True, exist_ok=True)
