# ATS fast / accurate mode guide

Both launchers call `start_ats_stack.bat`, which:
- checks `.venv` and the Python/npm requirements;
- stops existing ATS API/UI processes (any `uvicorn ats.server:app` or the Vite UI on port 5173);
- starts the API on http://127.0.0.1:8000, waits for `/api/health`, starts the UI on http://127.0.0.1:5173.

| Launcher | Bulk semantic (MiniLM) scoring | Use when |
|---|---|---|
| `start_ats_accurate_mode.bat` (= `start_ats_stack.bat`) | on | ranking quality matters (default) |
| `start_ats_fast_mode.bat` | off (skills / experience / education only) | maximum throughput |

## Verify
```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```
`status` should be `ok`; `ocr_available` and `doc_supported` show whether Tesseract / antiword were found.

## Benchmark a folder directly (no HTTP)
```powershell
.\.venv\Scripts\python.exe tools\benchmark.py "C:\path\to\resumes" --top-n 20
```

## What to check
- `execution_summary.duration_seconds`, `throughput_resumes_per_second`, `semantic_enabled`
- `summary_stats.failure_reasons` and the failure log `ats\runs\failed_files_*.jsonl`
- `summary_stats.warnings` (e.g. semantic scoring unavailable)

## Tuning
`ATS_PARSE_WORKERS` (parser processes) is the main throughput knob; lower it on machines with little free RAM
(~120 MB per worker). See README.md for all settings.
