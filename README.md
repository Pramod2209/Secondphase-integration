# Bulk ATS: bulk resume scoring and ranking

Scores a batch of resumes (hundreds to ~1,000+ per run) against one job requirement and returns a
ranked list with a per-candidate score breakdown and explanation. It runs locally: no database,
no accounts, no cloud services. Nothing is kept between runs except optional result files and caches.

## What it does

```
resumes (PDF / DOCX / DOC / TXT) + job (title, description, required/preferred skills, min years, education)
  -> parse (text layer; OCR fallback for scanned PDFs)
  -> extract skills, years of experience, education level, candidate name
  -> prefilter (resumes with none of the job's skills are reported as "filtered")
  -> MiniLM semantic similarity between resume and job description
  -> weighted score -> ranked list + explanation; every file accounted for (processed / filtered / failed + reason)
```

### Scoring

| Component | Weight | How it is computed |
|---|---|---|
| Skills | 40% | credit for required skills / required skills x 100, +2 x credit per preferred skill (max +10), capped at 100. A skill earns 1.0 for an exact or equivalent name and 0.75 for a more specific tool of a broad skill (see "Skill matching"). No required skills -> 100 (neutral). |
| Experience | 30% | min(years / required years, 1) x 100. No minimum -> 100. Years only count if at least one of the job's required/preferred skills was found (otherwise 0: experience in an unrelated field is not evidence). |
| Semantic | 20% | cosine similarity (x100) between the resume and the job title+description (all-MiniLM-L6-v2). |
| Education | 10% | requirement met -> 100, one level short -> 70, lower -> 40, no education found -> 0. Requirement "any" -> 100. |

If semantic scoring is off or unavailable, the other weights are re-normalized. Scores are always
finite and between 0 and 100. Ties are broken by skills, then experience, then file name.

### Extraction rules (deterministic, no LLM)

- **Skills** come from `ats/skill_taxonomy.json` (~440 skills plus aliases). Job skills are normalized
  to the same names ("JS" -> javascript, "K8s" -> kubernetes, "React.js" -> react). Skills that are
  not in the taxonomy are still searched for literally, so any skill a job asks for can match.
  Ambiguous words ("go", "r", "c", "spring", "excel", "react", "angular", "sem" ...) count only in a technical context:
  in a skills list, next to other skills, or with a qualifier such as "Spring Boot" or "Apache Spark".
  See "Skill matching" for how equivalent, broader and related skills are treated.
- **Experience** is the larger of:
  - employment date ranges, with overlaps merged; ranges in education, projects and internships
    are excluded, as are version ranges such as "SQL Server 2008-2019";
  - explicit statements such as "5+ years of experience", ignoring age and third-party statements;
  - per-job "Period: N years" lines.

  "Present" means today. To re-screen an archive collected earlier, set
  `ATS_REFERENCE_DATE=YYYY-MM-DD`.
- **Education** is the highest level with real evidence (phd / master / bachelor / associate /
  diploma / school / unknown). Abbreviations that are also words ("me", "be", "ms", "ba") only
  count with a field of study or academic context, so "contact me", "MS Excel" and
  "Marital status: Bachelor" are not degrees.
- **Name** comes from a "Name:" label, then the resume header, then the email address, then the
  filename as a last resort. The source is reported as `name_source`.

### Skill matching

Each job skill gets the best evidence found in the resume, in this order:

| Tier | Example (job skill <- resume) | Credit |
|---|---|---|
| exact | SQL <- "SQL" | 1.0 |
| equivalent (alias) | Kubernetes <- "K8s", HR <- "Human Resources", SQL Server <- "MSSQL", SEO <- "Search Engine Optimisation" | 1.0 |
| category (specific tool for a broad skill) | SQL <- "MySQL" / "PostgreSQL" / "Oracle"; JavaScript <- "React"; CAD <- "SolidWorks"; HR <- "payroll" | 0.75 |
| related (shown, not counted) | MySQL <- "PostgreSQL"; React <- "JavaScript"; AWS <- "Azure"; Docker <- "Kubernetes" | 0 |

- Equivalent names are taxonomy aliases, plus acronyms derived from multi-word skills ("standard
  operating procedures" <- "SOP"; derived acronyms only count in a skills list). Spelled-out
  acronyms are recognised only when they are taxonomy aliases: matching any phrase with the right
  initials was tried and removed, because on real resumes it matched ordinary text
  ("schedules and procedures" -> SAP, "project life cycle" -> PLC).
- Direction matters. A specific tool supports a broad requirement, never the reverse. "MySQL" earns
  0.75 for SQL, but "SQL" earns nothing for a MySQL requirement, and "JavaScript" nothing for React.
- `ats/skill_relations.json` holds the relationships. `categories` lists broad skill -> specific
  skills, `related` lists groups of skills that are not interchangeable, and `conflicts` lists words
  that show a name is used in another sense ("EMR" next to "AWS"/"S3" is Amazon EMR, not
  electronic medical records; "Oracle ERP" is not a database). To extend matching, add aliases to the
  taxonomy and relationships to this file. No code change is needed.
- **Explicit vs weak evidence.** A mention is *weak* when it only appears:
  - in a web or profile link ("github.com/user");
  - as a bare label on a contact line ("email | github | linkedin");
  - inside an organisation name ("Income Tax Department", "XYZ CNC Solutions Pvt Ltd"). A role
    before the company ("Data Analyst at ABC Pvt Ltd") stays explicit.

  Weak evidence is reported but earns `WEAK_EVIDENCE_CREDIT` (default 0, see `ats/config.py`), so on
  its own it neither passes the prefilter nor adds to the score. An explicit mention anywhere else
  in the resume always takes precedence.
- A lone letter joined to the next word only by a space (a PDF bullet glyph: "• HR" read as "e hr";
  or "A/P and A/R" read as "p and a") counts only where a written form of the skill has a word break
  at that point. So "e hr" is not EHR and "p and a" is not "panda" (pandas), while T-SQL, PL/SQL,
  CI/CD, A/B testing, "A B testing", C++, C#, "C Sharp" and "e commerce" still match.
- The prefilter and the score use the same evidence. A resume whose only evidence is related or weak
  is still filtered, and the explanation names that evidence so a reviewer can check.
- Each result has `skill_evidence` (skill, kind, tier, evidence, credit, strength `explicit`/`weak`,
  and a `note` for weak items). The explanation says which matches were partial, e.g.
  `sql (via mysql: specific tool for this skill, 75% credit)`, and lists
  `Weak evidence, not counted: github for git (web or profile link)`.
- Skills absent from both files are matched literally only. The system does **not** understand
  arbitrary skills: equivalents it has not been told about (e.g. "QC" for "quality control") are missed.

## Supported files

| Type | Handling |
|---|---|
| `.pdf` | pdfminer text layer (first 12 pages; PDFs with a broken trailer/page tree are recovered by scanning their objects). If almost no text is found, **OCR** runs on the page images with Tesseract (first 4 pages). PDFs whose text is drawn as vector outlines fail with `vector_text_pdf`. |
| `.docx` | Paragraphs and tables. Falls back to raw XML so text boxes, headers and footers are included; a DOCX that only contains pictures of the resume is OCR'd. |
| `.doc` | Read with `antiword` if installed (Git for Windows bundles it). Otherwise reported as `unsupported_file_type`. |
| `.txt` | UTF-8, UTF-16 or Latin-1. |
| anything else | Reported as failed with `unsupported_file_type`. Never silently skipped. |

Every submitted file appears in `all_results` with status `processed`, `filtered` or `failed`.
Failed files carry a reason code: `unsupported_file_type`, `empty_file`, `corrupt_file`,
`encrypted_pdf`, `no_text_layer`, `file_too_large`, `parse_error` or `internal_error`.
Files with identical content are flagged with `duplicate_of`. Files with the same name are all
processed: uploads are labelled `name [2]`, and in folder mode they are told apart by their
relative path.

## Install

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r ats\requirements.txt
cd webui && npm install
```

Optional:
- Install [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) for OCR of scanned PDFs. It
  is auto-detected in the default install folder, or set `ATS_TESSERACT_CMD`.
- Install `antiword` for `.doc` files.

The MiniLM model (~90 MB) is downloaded on first use and then used from the local cache.

## Run

- `start_ats_stack.bat`: API on :8000 + UI on :5173, semantic scoring on. This is the same as `start_ats_accurate_mode.bat`.
- `start_ats_fast_mode.bat`: same, with semantic scoring off for bulk runs.
- `python -m ats.main`: interactive command line (single resume or a whole folder).
- `python tools/benchmark.py <folder> [--top-n N]`: screen a folder directly and print a summary.

**UI.** Upload any number of resumes. The browser sends them in requests of up to 900 files,
shows live progress, merges and re-ranks the results, and lists every file that could not be
processed.

### HTTP API

Every POST needs the header `X-ATS-Client: <anything>`. This blocks cross-site form posts.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Status plus capabilities (OCR, .doc support, limits). |
| POST | `/api/single` | One resume (`resume` file + job fields). With `debug=true` it also returns a 500-character text preview; otherwise no resume text is returned. |
| POST | `/api/bulk` | Up to 1,000 files (`resumes`) + job fields + `top_n` + optional `request_id`. |
| GET | `/api/progress/{request_id}` | Live progress of a running `/api/bulk` call. |
| POST | `/api/bulk-parallel-folder` | Screen a folder on this machine (`folder_path`). **Disabled** unless `ATS_ALLOWED_FOLDER_ROOTS` lists the allowed directories. |

Job fields are `title`, `description`, `required_skills` / `preferred_skills` (comma-separated),
`min_experience_years` (0-50) and `required_education` (any / diploma / associate / bachelor /
master / phd). Invalid input returns 422 with a readable message. Responses never contain resume
text or embedding vectors.

## Configuration (environment variables)

| Variable | Default | Effect |
|---|---|---|
| `ATS_USE_SEMANTIC_BULK` / `ATS_USE_SEMANTIC_SINGLE` | 1 / 1 | MiniLM semantic score in bulk / single runs |
| `ATS_PREFILTER` | 1 | Report resumes with none of the job's skills as "filtered" instead of ranking them |
| `ATS_OCR`, `ATS_OCR_MAX_PAGES`, `ATS_TESSERACT_CMD` | 1, 4, auto | OCR fallback for scanned PDFs |
| `ATS_MAX_PDF_PAGES` | 12 | PDF pages read per file |
| `ATS_PARSE_WORKERS` | from CPU/RAM | Parser processes (the main throughput knob) |
| `ATS_EMBEDDING_BATCH_SIZE`, `ATS_EMBEDDING_THREADS`, `ATS_EMBEDDING_MAX_CHUNKS` | 32, auto, 3 | Embedding performance and coverage |
| `ATS_WARMUP_EMBEDDER`, `ATS_WARMUP_PARSE_POOL` | on | Load the model and start the workers when the server starts |
| `ATS_MAX_FILES_PER_REQUEST`, `ATS_MAX_FILE_SIZE_MB`, `ATS_MAX_REQUEST_SIZE_MB`, `ATS_MAX_CONCURRENT_JOBS` | 1000, 20, 2048, 1 | API limits |
| `ATS_ALLOWED_FOLDER_ROOTS` | (empty = disabled) | Folders the folder endpoint may read (separated by `;` on Windows) |
| `ATS_CORS_ORIGINS` | localhost:5173 | Allowed UI origins |
| `ATS_DATA_DIR` | `ats/` | Where `cache/`, `logs/`, `output/`, `runs/` are written |
| `ATS_SAVE_RESULTS` | 1 | Save a results JSON per run (scores and extraction only; never resume text). With 0, no results file is written and the failure log in `runs/` records a random candidate ID, file type, reason and timings instead of the file name |
| `ATS_REFERENCE_DATE` | today | Date that "present" means in resumes |

## Processing architecture and performance

- **Parsing and extraction** run in a pool of worker processes, because pdfminer is pure Python
  and does not speed up with threads. The pool is reused across requests and started at server start.
  Each worker checks every 3 s that the server is still running and exits if it was killed.
- **Embeddings** run in the main process on one thread, overlapped with parsing. torch uses
  several cores, and only one copy of the model is loaded.
- **Semantic coverage.** Each resume is reduced to its most relevant sections (summary, skills,
  experience) and split into up to 3 chunks of about 180 words. MiniLM reads only 256 tokens per
  input, so a single chunk would see little more than the contact header.
- **Caches.** Embedding vectors are cached under `cache/embeddings`, keyed by model, version and a
  hash of the full chunk text. Cache files contain no text.

Measured on the development machine (32 cores, about 2–5 GB free RAM, 1,000 real resumes):
see the "Benchmarks" section below.

## Benchmarks

1,000 real resumes (922 PDF, 1 `.PDF`, 72 DOCX, 5 DOC) on a 32-core Windows machine with about
2–5 GB of free RAM. Each run is end to end through `screen_bulk`. Peak memory is the whole
process tree.

| Run | Time | Resumes/s | Peak memory | Failed |
|---|---|---|---|---|
| Original code (before repair) | 496 s | 2.0 | 3.4 GB | 17 + 5 `.doc` silently dropped |
| Accurate mode, cold cache | 70–77 s | 13–14 | 2.1 GB | 2 (`vector_text_pdf`) |
| Accurate mode, warm embedding cache | 32–41 s | 24–31 | 1.6 GB | 2 |
| Fast mode (semantic off) | 30–39 s | 26–33 | 1.4 GB | 2 |

Ranges are the final hardening runs and an earlier clean run. Of the 1,000 files, 596 were ranked (616 with the tiered matcher below)
and 402 were filtered out (no required or preferred skill). The 2 failures are "printed to PDF"
files whose text is drawn as vector outlines; they need a renderer and are reported, not hidden.
The accurate cold run includes OCR of scanned resumes. Times vary with free RAM and other load on
the machine; an earlier run of the original code on the same data took 330 s.

Tiered skill matching costs about 16.5 ms per resume per job, against 7–12 ms before. That is roughly 5–9 s of
CPU per 1,000 resumes, spread across the parser workers. The old and new code were run alternately in the
same session, twice each: accurate mode, cold cache, backend job, 1,000 resumes.

| Prefilter | Code | Time (2 runs) | Peak memory | Ranked / filtered / failed |
|---|---|---|---|---|
| on | previous matcher | 123 s, 92 s | 2.05–2.06 GB | 596 / 402 / 2 |
| on | tiered matcher | 131 s, 122 s | 2.05–2.08 GB | 616 / 382 / 2 |
| off | previous matcher | 145 s, 133 s | 2.05–2.06 GB | 998 / 0 / 2 |
| off | tiered matcher | 116 s, 169 s | 2.07–2.09 GB | 998 / 0 / 2 |

Run-to-run variation (machine load, free RAM 3.6–5.4 GB) is larger than the matching cost. With the prefilter
on, 20 resumes that were filtered now have category evidence (all of it present in the resume text), and
none are lost. Scores only rise (40 of the 596 resumes ranked by both, by at most 6 points). Rank correlation
is 0.991, with 9 of the top 10 and 22 of the top 25 the same. The best recovered resume ranks 162nd.

## Validation

- **Synthetic, controlled** (`tests/test_domains.py`): 11 professions. Software, data analyst,
  marketing, HR, finance, mechanical, sales, UI/UX, healthcare, electrical and civil. Each has a
  strong, a partial and an irrelevant resume. Strong > partial > irrelevant holds with semantic
  scoring on and off. Job skills that are not in the taxonomy (e.g. "GD&T", "patient care",
  "quantity surveying", "user research") are matched from the resume text.
- **Real resumes** (random sample of 150 from a held-out folder of 2,000; none were used to tune the
  rules): 8 job descriptions run through the production pipeline. Software/.NET, data analyst,
  production/mechanical, accounts/finance, HR, sales, purchase/supply chain and VLSI. Healthcare,
  civil, UI/UX and marketing were validated **only** with synthetic resumes; the real data has too
  few such resumes (heuristic count: 5, 1, 1 and a handful).
- **Skill matching, before vs after** (10 job profiles across software, data/ML, mechanical, electrical, HR,
  marketing, finance, sales and healthcare; semantic scoring off so that only matching differs).
  The dev set (1,000 resumes) was used to find and fix problems. A separate held-out folder of 1,000
  was used for measurement. No rule was tuned on it, but it **has been inspected**: its losses and a
  precision sample were read by hand during this work, so it is no longer a blind test set.

  | | dev | held-out |
  |---|---|---|
  | resume-job pairs that now pass the prefilter (were filtered) | 415 | 390 |
  | pairs that no longer pass | 3 (2 prose "react accordingly", 1 employer name) | 8 (organisation names; 6 correct, 2 borderline) |
  | rank correlation of candidates passing both (Spearman, per job with 10+ candidates) | 0.94–0.99 | 0.95–0.99 |
  | recovered resumes reaching a job's top 10 | 3 of 415 | 1 of 390 |

  Hand-labelled precision on dev samples: exact 28/30, alias 29/30, category 32/36 (about 94% weighted).
  Recall against known vocabulary only: of 469 uncredited loose mentions, a sample of 60 had 20 true misses
  (about 97% estimated recall); skills described in words the taxonomy does not know are not counted here.
  Weak evidence on dev: 11 mentions (8 GitHub links, 3 employer names), all correctly not credited.
  Most recoveries come from a specific tool supporting a *preferred* skill (e.g. Excel for "MS Office"),
  which lets the resume past the prefilter but adds at most a small bonus. Most recovered resumes rank in the lower half.
- **MiniLM for skill names** (56 labelled pairs): equivalent pairs scored 0.19–0.98 cosine and unrelated
  pairs 0.08–0.62 ("spring"/"season" 0.62, "java"/"javascript" 0.40, "computer numerical
  control"/"cnc" 0.19). A threshold that accepts 75% of equivalents also accepts 5 of 14 unrelated pairs,
  so the model is not used to award skill credit.

## Tests

```bat
.venv\Scripts\python -m unittest discover -s tests -t .
```

150 tests cover parsing (including generated scanned PDFs for OCR), skills, experience, education,
names, skill matching tiers across domains (equivalents, categories, related skills, weak contextual evidence, PDF bullet glyphs, ordinary-word false positives), score bounds and edge cases, ranking order on controlled candidates, bulk runs (1, 2, 5 and
100 files, duplicates, corrupt/empty/unsupported files), caching, every API endpoint, and that
parser workers exit when the server process is force-killed (no orphaned processes). Fixtures
are generated at test time, and runtime files go to a temporary directory. `scripts/manual/`
holds older ad-hoc scripts that call a running server; they are not part of the test suite.

## Known limitations

- Extraction is rule-based. Unusual layouts can still hide experience dates or degrees.
  Multi-column PDFs sometimes interleave sections.
- PDFs whose text is drawn as vector outlines (no text operators, no page images) cannot be read
  without a PDF renderer; they are reported as `vector_text_pdf` (2 of 1,000 real resumes; 1 of a random 150).
- Two-column layouts can separate dates from job titles (dates sometimes land under the wrong heading);
  such jobs may be missed or a dated project under "Experience" counted. Text fused by the PDF itself
  (e.g. "BCAat") cannot be recognised as a degree.
- Skill relationships are curated, not learned. A skill outside `skill_taxonomy.json` /
  `skill_relations.json` is matched literally only, so its synonyms and specific tools are missed.
  Put common synonyms in the job's skill list or add them to the files. The MiniLM model was evaluated
  for this and is not used: it could not separate equivalent from unrelated skill names (see
  "Validation").
- Category evidence is partial credit (0.75), not proof of the broad skill. Matching is keyword- and
  context-based and does not understand what a sentence says:
  - negation ("React UI, development not included in my role") still counts;
  - a developer who *built* HR or payroll software gets HR category credit;
  - "sales person" in a software project counts as sales.

  Weak-evidence detection covers links, contact lines and organisation names only. Check
  `skill_evidence` for borderline candidates.
- Known recall gaps (measured on the dev set):
  - skill names wrapped across a line break ("lead ⏎ generation");
  - ambiguous names in a vertical list with no other recognised skill nearby ("excel" alone between
    "rdbms" and "tools");
  - versioned or described tool names ("Tally 7.2", "Tally accounting package").
- When one job skill contains another ("manufacturing" and "lean manufacturing"), the longer one
  takes the mention.
- OCR adds about 2–5 s per scanned resume and depends on scan quality.
- "Present" is measured to today, so an old resume overstates current experience unless
  `ATS_REFERENCE_DATE` is set.
- The semantic score is raw cosine × 100. Unrelated documents still score 5–25.
- The prefilter removes resumes that mention none of the job's skills. Disable it
  (`ATS_PREFILTER=0`) if skills may be absent from otherwise relevant resumes.
- Single-machine, single-user tool. There is no authentication, so keep the API bound to 127.0.0.1.
