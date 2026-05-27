@echo off
setlocal

set "ROOT=%~dp0"
set "API_URL=http://127.0.0.1:8000/api/health"
set "UI_URL=http://127.0.0.1:5173/"

echo [ATS-ACCURATE] Root: %ROOT%

REM Activate virtual environment
if exist "%ROOT%.venv\Scripts\activate.bat" (
  call "%ROOT%.venv\Scripts\activate.bat"
) else (
  echo [ATS-ACCURATE] Virtual environment not found at %ROOT%.venv
  exit /b 1
)

REM Ensure backend deps
python -c "import uvicorn" 2>nul
if errorlevel 1 (
  echo [ATS-ACCURATE] Installing project requirements...
  python -m pip install -r ats\requirements.txt
  if errorlevel 1 (
    echo [ATS-ACCURATE] Failed to install requirements
    exit /b 1
  )
)

REM Ensure npm exists
where npm >nul 2>nul
if errorlevel 1 (
  echo [ATS-ACCURATE] npm is not available in PATH. Install Node.js and retry.
  exit /b 1
)

REM Accurate mode runtime config (semantic bulk ON)
set "ATS_USE_SEMANTIC_BULK=1"
set "ATS_USE_SEMANTIC_SINGLE=1"
set "ATS_WARMUP_EMBEDDER=1"
set "ATS_BATCH_SIZE=100"
set "ATS_MAX_CONCURRENT_BATCHES=2"
set "ATS_THREAD_WORKERS=16"
set "ATS_PROCESS_WORKERS=1"

echo [ATS-ACCURATE] Stopping existing ATS API/UI processes if any...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$procs = Get-CimInstance Win32_Process; foreach($p in $procs){ if($p.CommandLine -and ($p.CommandLine -like '*uvicorn ats.server:app*' -or $p.CommandLine -like '*vite --host 127.0.0.1 --port 5173*')){ try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; Write-Output ('stopped_pid=' + $p.ProcessId) } catch {} } }"

echo [ATS-ACCURATE] Starting backend API...
start "ATS API ACCURATE" cmd /k "cd /d %ROOT% && call .venv\Scripts\activate.bat && set ATS_USE_SEMANTIC_BULK=%ATS_USE_SEMANTIC_BULK% && set ATS_USE_SEMANTIC_SINGLE=%ATS_USE_SEMANTIC_SINGLE% && set ATS_WARMUP_EMBEDDER=%ATS_WARMUP_EMBEDDER% && set ATS_BATCH_SIZE=%ATS_BATCH_SIZE% && set ATS_MAX_CONCURRENT_BATCHES=%ATS_MAX_CONCURRENT_BATCHES% && set ATS_THREAD_WORKERS=%ATS_THREAD_WORKERS% && set ATS_PROCESS_WORKERS=%ATS_PROCESS_WORKERS% && python -m uvicorn ats.server:app --host 127.0.0.1 --port 8000 --log-level info"

echo [ATS-ACCURATE] Waiting for backend health...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; for($i=0; $i -lt 120; $i++){ try { $r = Invoke-RestMethod -Uri '%API_URL%' -Method Get -TimeoutSec 3; if($r.status -eq 'ok'){ $ok=$true; break } } catch {}; Start-Sleep -Seconds 1 }; if($ok){ Write-Output 'health_ok'; exit 0 } else { Write-Output 'health_failed'; exit 1 }"
if errorlevel 1 (
  echo [ATS-ACCURATE] Backend did not become healthy in time.
  exit /b 1
)

echo [ATS-ACCURATE] Starting React UI...
start "ATS UI" cmd /k "cd /d %ROOT%webui && npm run dev -- --host 127.0.0.1 --port 5173"

timeout /t 2 /nobreak >nul
start "" "%UI_URL%"

echo [ATS-ACCURATE] Stack started.
echo [ATS-ACCURATE] API: %API_URL%
echo [ATS-ACCURATE] UI : %UI_URL%
echo [ATS-ACCURATE] Mode: semantic bulk ON (quality optimized)
exit /b 0
