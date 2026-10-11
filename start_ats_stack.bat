@echo off
REM ATS launcher. Usage: start_ats_stack.bat [fast|accurate]   (default: accurate)
REM   accurate : semantic (MiniLM) scoring on in bulk runs - best ranking quality (default)
REM   fast     : semantic scoring off in bulk runs - skills/experience/education only
setlocal

set "ROOT=%~dp0"
set "MODE=%~1"
if "%MODE%"=="" set "MODE=accurate"
set "API_URL=http://127.0.0.1:8000/api/health"
set "UI_URL=http://127.0.0.1:5173/"

if /I "%MODE%"=="fast" (
  set "ATS_USE_SEMANTIC_BULK=0"
  set "ATS_WARMUP_EMBEDDER=0"
) else (
  set "ATS_USE_SEMANTIC_BULK=1"
  set "ATS_WARMUP_EMBEDDER=1"
)
REM Optional tuning (defaults are picked from CPU/RAM): ATS_PARSE_WORKERS, ATS_EMBEDDING_BATCH_SIZE,
REM ATS_EMBEDDING_THREADS, ATS_EMBEDDING_MAX_CHUNKS, ATS_OCR, ATS_PREFILTER, ATS_DATA_DIR.

echo [ATS] Root: %ROOT%  Mode: %MODE%

if not exist "%ROOT%.venv\Scripts\python.exe" (
  echo [ATS] Virtual environment not found at %ROOT%.venv - create it and run: pip install -r ats\requirements.txt
  exit /b 1
)
"%ROOT%.venv\Scripts\python.exe" -c "import uvicorn, fastapi, sentence_transformers, pdfminer, docx" 2>nul
if errorlevel 1 (
  echo [ATS] Missing Python packages. Run: "%ROOT%.venv\Scripts\python.exe" -m pip install -r ats\requirements.txt
  exit /b 1
)
where npm >nul 2>nul
if errorlevel 1 (
  echo [ATS] npm is not available in PATH. Install Node.js and retry.
  exit /b 1
)

echo [ATS] Stopping existing ATS API/UI processes if any...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$procs = Get-CimInstance Win32_Process; foreach($p in $procs){ if($p.CommandLine -and ($p.CommandLine -like '*uvicorn ats.server:app*' -or $p.CommandLine -like '*vite --host 127.0.0.1 --port 5173*')){ try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; Write-Output ('stopped_pid=' + $p.ProcessId) } catch {} } }"

echo [ATS] Starting backend API...
REM The new window inherits the ATS_* variables set above.
start "ATS API (%MODE%)" cmd /k "cd /d "%ROOT%" && "%ROOT%.venv\Scripts\python.exe" -m uvicorn ats.server:app --host 127.0.0.1 --port 8000 --log-level info"

echo [ATS] Waiting for backend health...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; for($i=0; $i -lt 180; $i++){ try { $r = Invoke-RestMethod -Uri '%API_URL%' -Method Get -TimeoutSec 3; if($r.status -eq 'ok'){ $ok=$true; break } } catch {}; Start-Sleep -Seconds 1 }; if($ok){ Write-Output 'health_ok'; exit 0 } else { Write-Output 'health_failed'; exit 1 }"
if errorlevel 1 (
  echo [ATS] Backend did not become healthy in time. Check the "ATS API" window for errors.
  exit /b 1
)

echo [ATS] Starting React UI...
start "ATS UI" cmd /k "cd /d "%ROOT%webui" && npm run dev -- --host 127.0.0.1 --port 5173"

timeout /t 2 /nobreak >nul
start "" "%UI_URL%"

echo [ATS] Stack started.  API: %API_URL%   UI: %UI_URL%   Mode: %MODE%
exit /b 0
