# ATS Fast/Accurate Mode Guide

## Created launchers
- `start_ats_fast_mode.bat`
- `start_ats_accurate_mode.bat`

Both scripts:
- activate `.venv`
- stop existing ATS API/UI processes
- start backend API and wait for `/api/health`
- start React UI on port 5173

## When to use which mode
- **Fast mode**: highest throughput for large bulk runs (semantic embeddings disabled in bulk).
- **Accurate mode**: highest ranking quality (semantic embeddings enabled in bulk), slower.

## How to test

### 1) Start mode
From project root:

```bat
start_ats_fast_mode.bat
```

or

```bat
start_ats_accurate_mode.bat
```

### 2) Verify health
In a new PowerShell terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

Expected:

```text
status
------
ok
```

### 3) Run benchmark (direct pipeline)

```powershell
. .\.venv\Scripts\Activate.ps1
python .\tools\benchmark.py "C:\path\to\resumes_folder" --top-n 20
```

### 4) Run API batch test (real endpoint)
Use your existing script:

```powershell
. .\.venv\Scripts\Activate.ps1
python .\test_parallel_full_1980.py
```

## What to check in logs/output
- `execution_summary.duration_seconds`
- `execution_summary.throughput_resumes_per_second`
- `execution_summary.semantic_enabled`
- parse failures file (if any): `ats\runs\failed_files_*.jsonl`

## Performance target workflow
1. Run **fast mode** and measure 1000 resumes.
2. If still above target, reduce pressure:
   - lower `ATS_MAX_CONCURRENT_BATCHES`
   - lower `ATS_THREAD_WORKERS`
3. Re-run benchmark and compare duration.

## Stop processes quickly
```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*uvicorn ats.server:app*' -or $_.CommandLine -like '*vite --host 127.0.0.1 --port 5173*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```
